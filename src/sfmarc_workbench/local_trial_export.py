from io import BytesIO
from hashlib import sha256
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile, ZIP_DEFLATED

import numpy as np
from meshioplusplus import Mesh
from meshioplusplus.marc import _marc
from meshioplusplus.nastran import write

from .templates.local_trial_paths import weld_lines


def export_local_trial(trial, data):
    if data["model_id"] != trial.model_id:
        raise ValueError("模型已更新，请重新设置局部试算")
    if any(group["model_id"] != trial.model_id for group in data["groups"]):
        raise ValueError("试算组的来源模型已更新，请重新设置该组")
    config = {"source_process": trial.plan["source_process"],
              "source_sha256": trial.model.deck.digest, "length_unit": "mm",
              "source_paths": trial.plan["paths"], "groups": []}
    config.update(schema='simuweld-local-case-2',
                  cooling_limit_c=data.get('cooling_limit_c', 26.), max_cooling_s=data.get('max_cooling_s', 60.),
                  component_names=data.get('component_names', {}))
    if data.get('source_process'):
        config['source_process'] = data['source_process'].strip()
    output = BytesIO()
    names = set()
    work = Path(getattr(sys, "_sd_root", Path.cwd())) / "work"
    work.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="local-trial-", dir=work) as directory, ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for index, spec in enumerate(data["groups"], 1):
            name = spec["name"].strip()
            if not name or name in names:
                raise ValueError("试算名称不能为空或重复")
            names.add(name)
            result = trial.preview(spec)
            matches = [row for row in result["matches"] if row["matched"]]
            if not matches:
                raise ValueError("请至少选择一段有效焊路")
            group = {"name": name, "matches": matches, "crop": result["crop"],
                     "selection": result["selection"], "spec": spec}
            ordered = sorted(matches, key=lambda row: row['start_s'])
            origin = ordered[0]['start_s']
            prefix = data.get('target_process', '').strip() or config['source_process'] + '-Local'
            group['target_process'] = prefix if len(data['groups']) == 1 else prefix + '-{:02d}'.format(index)
            if any(character in '<>:"/\\|?*' or ord(character) < 32 for character in group['target_process']):
                raise ValueError('目标 Process 名称含无效字符。')
            group['time_origin_s'] = origin
            group['events'] = [{'name': group['target_process'] + '_Weld_{:03d}'.format(number),
                                'path_id': match['path_id'], 'start_s': match['start_s'] - origin,
                                'end_s': match['end_s'] - origin}
                               for number, match in enumerate(ordered, 1)]
            for number, component in enumerate(group["crop"]["components"], 1):
                original = trial.points[[trial.node_index[node] for node in trial.components[component['name']]]]
                component['source_bounds_mm'] = [original.min(axis=0).tolist(), original.max(axis=0).tolist()]
                if not component["element_ids"]:
                    continue
                nodes = component["node_ids"]
                local = {node: offset for offset, node in enumerate(nodes)}
                blocks = {}
                for ident in component["element_ids"]:
                    kind, connection, _ = trial.elements[ident]
                    if kind == 7:
                        cell_type, connection = _marc._collapse_solid(list(connection))
                    else:
                        cell_type = {134: "tetra", 136: "wedge"}[kind]
                    entries = blocks.setdefault(cell_type, [])
                    entries.append((ident, [local[node] for node in connection]))
                cells = [(cell_type, np.asarray([row for _, row in entries], dtype=np.int64))
                         for cell_type, entries in blocks.items()]
                element_order = [ident for entries in blocks.values() for ident, _ in entries]
                mesh = Mesh(trial.points[[trial.node_index[node] for node in nodes]], cells)
                filename = f"meshes/group_{index}_component_{number}.bdf"
                mesh_path = Path(directory) / Path(filename).name
                # 文件路径与默认格式使用库的 C++ writer，保留大字段节点坐标精度。
                write(mesh_path, mesh)
                component["mesh_file"] = filename
                component['mesh_sha256'] = sha256(mesh_path.read_bytes()).hexdigest()
                component["bdf_node_original_ids"] = nodes
                component["bdf_element_original_ids"] = element_order
                archive.write(mesh_path, filename)
            config["groups"].append(group)
        lines = weld_lines(config)
        encoded = json.dumps(config, ensure_ascii=False, allow_nan=False, indent=2)
        archive.writestr("local_trial.json", encoded.encode("utf-8"))
        archive.writestr('case_manifest.json', encoded.encode('utf-8'))
        case_template = Path(__file__).with_name('templates') / 'local_trial_case.py'
        case_code = case_template.read_text(encoding='utf-8')
        compile(case_code, 'create_local_case.py', 'exec')
        archive.writestr('create_local_case.py', case_code.encode('utf-8'))
        template = Path(__file__).with_name("templates").joinpath("local_trial_paths.py").read_text(encoding="utf-8")
        path_config = json.dumps({"groups": [{"name": group["name"], "matches": group["matches"]}
                                           for group in config["groups"]]}, ensure_ascii=False, allow_nan=False)
        code = "import json\nCONFIG = json.loads(" + repr(path_config) + ")\n\n" + template
        compile(code, "create_local_weld_paths.py", "exec")
        archive.writestr("create_local_weld_paths.py", code.encode("utf-8"))
        archive.writestr("使用说明.txt", (
            "完整解压后，在来源 Simufact 项目中运行 create_local_case.py，创建独立局部 Process。\n"
            "脚本替换局部网格、创建焊路及 Robot、复制热源并保留实际经过时间间隔。\n"
            "最终冷却使用试件最高温度停止条件，夹具在应变提取期间保留。\n"
            "请确认切割面热边界、夹具接触和局部连接，再生成 DAT。\n"
            "仅需创建焊路时可以单独使用 create_local_weld_paths.py。\n"
            "焊路名称中的序号为组内焊接顺序。以下时间为原始算例绝对时间，设置等待时间时结合各条焊路的结束时刻。\n"
            + "\n".join("{}：{}～{} s".format(line["name"], line["start_s"], line["end_s"]) for line in lines)
            + "\n网格组件对应关系、原始节点编号和提取区域保存在 local_trial.json。\n"
        ).encode("utf-8"))
    return output.getvalue()
