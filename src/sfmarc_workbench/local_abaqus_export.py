from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from copy import deepcopy
from hashlib import sha256
from collections import defaultdict

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from .background import BackgroundModel
from .templates.shell_mapping import path_segments
from .local_conservative import conservative_loading


def selected_paths(paths, order=None, reverse_ids=()):
    known = {path['id']: path for path in paths}
    order = list(known) if order is None else order
    if not order or len(order) != len(set(order)) or not set(order).issubset(known) or not set(reverse_ids).issubset(order):
        raise ValueError('目标焊路选择或顺序无效。')
    output = [deepcopy(known[ident]) for ident in order]
    original_order = [path['id'] for path in paths if path['id'] in order]
    if order != original_order:
        cursor = 0.
        for path in output:
            duration = path['end_s'] - path['start_s']
            cooling = max(0., path['cooling_end_s'] - path['end_s'])
            path.update(start_s=cursor, end_s=cursor + duration, cooling_end_s=cursor + duration + cooling)
            cursor += duration + cooling
    for path in output:
        if path['id'] in reverse_ids:
            path['points_mm'].reverse()
            path['normals'].reverse()
    return output


def read_shell(data, filename, length_unit, preview_data=None):
    if length_unit not in ('mm', 'm'):
        raise ValueError('INP 长度单位必须为 mm 或 m。')
    if preview_data is None:
        model = BackgroundModel(data, filename, 'inp')
        if model.source_dimensions != {2}:
            raise ValueError('Abaqus 整体模型需要 shell 网格。')
        points = model.points * (1000 if length_unit == 'm' else 1)
        faces, body_ids, body_names = model.faces, model.body_ids, model.body_names
    else:
        preview = json.loads(preview_data)
        if preview.get('schema') != 'simuweld-shell-preview-1' or preview.get('source_sha256') != sha256(data).hexdigest():
            raise ValueError('装配预览与当前 INP 不一致。')
        points = np.asarray(preview['points_mm'], dtype=float)
        faces = preview['faces']
        if points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all() or not faces:
            raise ValueError('装配预览坐标或单元为空。')
        edges = defaultdict(list)
        for index, face in enumerate(faces):
            if len(face) not in (3, 4) or len(set(face)) != len(face) or any(type(node) is not int or not 0 <= node < len(points) for node in face):
                raise ValueError('装配预览单元连接无效。')
            for first, last in zip(face, face[1:] + face[:1]):
                edges[tuple(sorted((first, last)))].append(index)
        rows, columns = [], []
        for owners in edges.values():
            rows.extend([owners[0]] * (len(owners) - 1))
            columns.extend(owners[1:])
        graph = coo_matrix((np.ones(len(rows)), (rows, columns)), shape=(len(faces), len(faces))).tocsr()
        _, body_ids = connected_components(graph, directed=False)
        body_names = {int(body): '实体 {}'.format(body + 1) for body in np.unique(body_ids)}
    bodies = []
    for ident in sorted(body_names):
        nodes = sorted({node for face, body in zip(faces, body_ids) if body == ident for node in face})
        coordinates = points[nodes]
        bodies.append({'id': ident, 'name': body_names[ident],
                       'min_mm': coordinates.min(axis=0).tolist(), 'max_mm': coordinates.max(axis=0).tolist()})
    return {'data': data, 'filename': filename, 'length_unit': length_unit,
            'preview': {'points_mm': points.tolist(),
                        'faces': [{'node_ids': face, 'body': int(body)} for face, body in zip(faces, body_ids)],
                        'bodies': bodies, 'node_count': len(points), 'face_count': len(faces)}}


def export_abaqus(model, equivalent, paths, body_components, job_name):
    if not job_name or not job_name[0].isalpha() or any(not (character.isascii() and (character.isalnum() or character == '_')) for character in job_name):
        raise ValueError('计算名称需要英文字母开头，仅包含字母、数字和下划线。')
    regions = []
    for body in model['preview']['bodies']:
        component = body_components.get(str(body['id']))
        if component:
            if component not in equivalent['component_names']:
                raise ValueError('目标实体对应的代表组件不存在。')
            regions.append(dict(body, component=component))
    if not regions:
        raise ValueError('请指定至少一个目标板件与代表数据的对应关系。')
    element_loads, conservation = conservative_loading(model['preview'], equivalent, paths, body_components)
    config = {'schema': 'simuweld-abaqus-loading-1', 'job_name': job_name, 'input_file': 'source_model.inp',
              'model_length_unit': model['length_unit'], 'regions': regions,
              'equivalent': equivalent, 'paths': paths,
              'segments': path_segments(paths, equivalent['representative_length_mm']),
              'element_loads': element_loads, 'conservation': conservation,
              'target_version': '6.14-5', 'section_integration': 'Simpson-5',
              'validation_status': '文件生成与数值检查；目标 Abaqus 求解待验证'}
    output = BytesIO()
    templates = Path(__file__).with_name('templates')
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('source_model.inp', model['data'])
        archive.writestr('sd_loading.json', json.dumps(config, ensure_ascii=False, allow_nan=False, indent=2))
        for name in ('prepare_abaqus_model.py', 'shell_mapping.py', 'local_shell_loading.for'):
            archive.write(templates / name, name)
        archive.writestr('使用说明.txt',
            '请完整解压并保留 source_model.inp。\n'
            '在 Abaqus 6.14-5 命令环境运行：abaqus cae noGUI=prepare_abaqus_model.py\n'
            '脚本使用 Abaqus 官方接口读取模型并创建顺序应变加载，保留材料、截面及初始边界。\n'
            '最终模型采用五个 Simpson 厚度点，通过 USDFLD 向 UEXPAN 传递每个材料点的代表应变。\n'
            '材料和板间连接需要在原 INP 中完成。缺少定义时脚本明确停止并保存可检查的 CAE。\n'
            '子程序及求解需在目标 Abaqus 版本验证，当前包不代表已经完成工程精度验证。\n'
            'sd_loading.json 记录代表数据、焊路分段及全过程顺序。\n'
            + ('' if equivalent['final_cooling_complete'] else
               '注意：来源末态最高温度 {:.2f}℃，未达到 {:.2f}℃ 冷却条件。本包可检查流程，工程精度需要冷却完整结果验证。\n'.format(
                   equivalent['final_temperature_max_c'], equivalent['cooling_limit_c'])))
    return output.getvalue()
