import csv
from io import StringIO
import json
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from zipfile import ZipFile
from hashlib import sha256

import numpy as np

from .local_shell_results import read_component, validate_locations
from .local_shell_equivalent import representative
from .templates.local_results_export import select_checkpoints


def weld_csv(text):
    if '[m]' not in text:
        raise ValueError('焊路 CSV 必须标明单位 [m]。')
    rows = list(csv.reader((line for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')), delimiter=';'))
    if not rows or rows[0] != ['1']:
        raise ValueError('焊路 CSV 需要 local vector 方向定义。')
    points, normals = [], []
    for row in rows[1:]:
        if len(row) != 8 or row[1].strip().lower() != 'true':
            raise ValueError('焊路 CSV 数据列或激活状态不符合要求。')
        values = np.asarray(row[2:], dtype=float)
        if not np.isfinite(values).all():
            raise ValueError('焊路 CSV 含非有限数值。')
        points.append((values[:3] * 1000).tolist())
        normals.append(values[3:].tolist())
    if len(points) < 2:
        raise ValueError('结果包没有完整的焊路坐标。')
    return {'points_mm': points, 'normals': normals}


def analyze_archive(path, work, length_mm=1., bins=24, final_time_s=None, local_plan=None, progress=None):
    with ZipFile(path) as archive:
        names = {}
        expanded = 0
        for info in archive.infolist():
            name = info.filename.replace('\\', '/')
            target = PurePosixPath(name)
            if target.is_absolute() or '..' in target.parts or ':' in name or name in names:
                raise ValueError('结果 ZIP 包含重复或无效路径。')
            names[name] = info
            expanded += info.file_size
        if expanded > 10 * 1024 ** 3 or len(names) > 20000:
            raise ValueError('结果 ZIP 超过 10 GB 展开空间或文件数量上限。')
        if 'manifest.json' not in names:
            raise ValueError('结果 ZIP 缺少 manifest.json。')
        manifest = json.loads(archive.read(names['manifest.json']))
        if manifest.get('schema') not in ('simuweld-local-results-1', 'simuweld-local-results-2'):
            raise ValueError('结果包版本不受支持。')
        if manifest.get('unit_system') != 'si' or manifest.get('element_values_at') != 'integration-points' or manifest.get('undeformed_mesh') is not True:
            raise ValueError('结果包需要 SI 单位、积分点值和未变形网格。')
        expected = manifest.get('process_components', [])
        if not expected or len(expected) != len(set(expected)) or any(not isinstance(name, str) or not name for name in expected):
            raise ValueError('结果清单的组件名称为空或重复。')
        increments = manifest['increments']
        time_by_number = {row['number']: row['time_s'] for row in increments}
        if (len(time_by_number) != len(increments) or not increments
                or any(type(number) is not int or number < 0 or not np.isfinite(time) or time < 0 for number, time in time_by_number.items())):
            raise ValueError('结果增量编号重复。')
        events = manifest.get('events')
        if events is None:
            if local_plan is None:
                raise ValueError('旧结果包需要同时提供局部求解 DAT，以确定焊接时间。')
            if local_plan['source_process'] != manifest['process']:
                raise ValueError('局部 DAT 的 Process 名称与结果包不一致。')
            events = [{'name': str(row['id']), 'start_s': row['start_s'], 'end_s': row['end_s']} for row in local_plan['paths']]
        if final_time_s is None:
            final_time_s = max(time_by_number.values())
        checkpoints = select_checkpoints(time_by_number, events, final_time_s)
        for checkpoint in checkpoints:
            entry = next(row for row in increments if row['number'] == checkpoint['increment'])
            components = [PurePosixPath(name.replace('\\', '/')).stem.rsplit('_', 1)[0] for name in entry['files']]
            if len(components) != len(set(components)) or set(components) != set(expected):
                raise ValueError('增量 {} 的组件文件需要完整且唯一覆盖组件清单。'.format(checkpoint['increment']))
        paths = []
        for robot in manifest['robots']:
            prefix = robot['directory'] + '/paths/'
            for name in sorted(names):
                if name.startswith(prefix) and name.lower().endswith('.csv'):
                    paths.append(weld_csv(archive.read(names[name]).decode('utf-8-sig')))
        if not paths and local_plan:
            paths = local_plan['paths']
        if len(paths) != len(events):
            raise ValueError('结果包焊路坐标数量与焊接事件不一致。')
        if not paths:
            raise ValueError('结果包没有焊路。')
        # 当前一组局部结果对应同一区域的多次经过，代表区间使用第一条局部焊路。
        reference = paths[0]
        points = reference['points_mm']
        normal = np.asarray(reference['normals'], dtype=float).mean(axis=0)
        reports, previous, edges_by_component = [], {}, {}
        completed = 0
        total = sum(len(next(row['files'] for row in increments if row['number'] == checkpoint['increment'])) for checkpoint in checkpoints)
        with TemporaryDirectory(dir=work, prefix='local-shell-') as directory:
            for checkpoint in checkpoints:
                entry = next(row for row in increments if row['number'] == checkpoint['increment'])
                components = []
                for number, filename in enumerate(entry['files']):
                    name = filename.replace('\\', '/')
                    if name not in names:
                        raise ValueError('结果清单引用文件缺失：' + name)
                    file = Path(directory) / ('result_{}.unv'.format(number))
                    file.write_bytes(archive.read(names[name]))
                    value = read_component(file)
                    key = PurePosixPath(name).stem.rsplit('_', 1)[0]
                    if key not in manifest['process_components']:
                        raise ValueError('结果文件所属组件与清单不符：' + key)
                    check = validate_locations(value)
                    if checkpoint['kind'] == 'initial':
                        if np.max(np.abs(value['plastic'])) > 1e-10:
                            raise ValueError('初始塑性应变不为零，需要提供独立的初始代表数据。')
                    else:
                        profile = representative(value, points, length_mm, normal, bins=bins,
                                                 edges_mm=edges_by_component.get(key))
                        edges_by_component[key] = profile['edges_mm']
                        old = previous.get(key)
                        if old is not None:
                            old_bands = {index: band for index, band in enumerate(old['bands'])}
                            if len(old_bands) != len(profile['bands']):
                                raise ValueError('各时刻代表区间横向覆盖不一致。')
                            increments_bands = []
                            for current, prior in zip(profile['bands'], old['bands']):
                                if abs(current['r_min_mm'] - prior['r_min_mm']) > 1e-8:
                                    raise ValueError('各时刻横向区间不能一一对应。')
                                band = dict(current)
                                for field in ('membrane', 'curvature_per_mm', 'integrated_strain_mm3', 'integrated_first_moment_mm4'):
                                    band[field] = (np.asarray(current[field]) - prior[field]).tolist()
                                increments_bands.append(band)
                        else:
                            increments_bands = profile['bands']
                        previous[key] = profile
                        components.append({'name': key, 'profile': dict(profile, bands=increments_bands),
                                           'definition_check': check})
                    completed += 1
                    if progress:
                        progress(completed, total)
                reports.append(dict(checkpoint, components=components))
        final_temperature = max(row['profile']['temperature_max_c'] for row in reports[-1]['components'])
        result = {'schema': 'simuweld-shell-equivalent-1', 'process': manifest['process'],
                'representative_length_mm': length_mm, 'paths': paths, 'events': events,
                'states': reports, 'component_names': manifest['process_components'],
                'source_to_target_node_mapping': False,
                'cooling_limit_c': manifest.get('cooling_limit_c', 26.),
                'final_temperature_max_c': final_temperature,
                'final_cooling_complete': final_temperature <= manifest.get('cooling_limit_c', 26.),
                'assumption': '一组代表区间用于全部位置，逐次使用独立的塑性应变增量。',
                'validation_status': '真实数据数值核对；尚未完成 Abaqus 局部重放求解'}
        result['equivalent_id'] = sha256(json.dumps(result, sort_keys=True, allow_nan=False).encode()).hexdigest()
        return result
