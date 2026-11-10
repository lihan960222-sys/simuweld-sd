import math
import xml.etree.ElementTree as ET
from pathlib import Path


def validate_config(config):
    source = config["source_process"]
    if not isinstance(source, str) or not source.strip():
        raise ValueError("来源 Process 名称不能为空。")
    digest = config["source_sha256"]
    if (not isinstance(digest, str) or len(digest) != 64
            or any(value not in "0123456789abcdefABCDEF" for value in digest)):
        raise ValueError("来源 DAT SHA256 无效。")
    if config["length_unit"] != "mm":
        raise ValueError("局部试算网格必须使用 mm。")
    for field in ("cooling_limit_c", "max_cooling_s"):
        if not math.isfinite(config[field]) or config[field] <= 0:
            raise ValueError("{} 必须为有限正数。".format(field))
    paths = {path["id"]: path for path in config["source_paths"]}
    if len(paths) != len(config["source_paths"]) or not paths:
        raise ValueError("来源焊路编号重复或为空。")
    names = set()
    if not config["groups"]:
        raise ValueError("没有需要生成的局部试算组。")
    for group in config["groups"]:
        name = group["name"]
        if (not isinstance(name, str) or not name.strip() or name == source or name in names
                or any(character in '<>:"/\\|?*' or ord(character) < 32 for character in name)):
            raise ValueError("试算组名称无效、重复或与来源 Process 相同。")
        names.add(name)
        if not group["matches"] or not group["crop"]["components"]:
            raise ValueError("{} 缺少焊路或组件。".format(name))
        component_names = [component["name"] for component in group["crop"]["components"]]
        if len(set(component_names)) != len(component_names):
            raise ValueError("{} 的组件名称重复。".format(name))
        if any(component["name"].casefold() == "base"
               and (not component["keep_whole"] or not component["element_ids"])
               for component in group["crop"]["components"]):
            raise ValueError("Base 必须完整保留。")
        constraint_names = set()
        for constraint in group["constraints"]:
            axes = constraint["axes"]
            if (not constraint["name"] or constraint["name"] in constraint_names
                    or len(axes) != 3 or any(type(value) is not bool for value in axes) or not any(axes)
                    or not constraint["samples"]):
                raise ValueError("固定节点约束名称、方向或采样节点无效。")
            constraint_names.add(constraint["name"])
            keys = set()
            for sample in constraint["samples"]:
                key = (sample["component"], sample["node_id"])
                components = [component for component in group["crop"]["components"] if component["name"] == key[0]]
                if (len(components) != 1 or key[1] not in components[0]["node_ids"] or key in keys
                        or len(sample["xyz_mm"]) != 3 or any(not math.isfinite(value) for value in sample["xyz_mm"])):
                    raise ValueError("固定节点采样记录重复、无效或不属于保留网格。")
                keys.add(key)
        for match in group["matches"]:
            source_path = paths[match["path_id"]]
            points, normals = match["points_mm"], match["normals"]
            if (len(points) < 2 or len(points) != len(normals)
                    or any(len(row) != 3 or any(not math.isfinite(value) for value in row)
                           for row in points + normals)
                    or any(not math.isclose(math.hypot(*normal), 1., abs_tol=1e-6)
                           for normal in normals)):
                raise ValueError("{} 的坐标或方向无效。".format(match["label"]))
            length = math.fsum(math.dist(first, second) for first, second in zip(points, points[1:]))
            duration = match["end_s"] - match["start_s"]
            if (any(not math.isfinite(match[field]) for field in
                    ("start_s", "end_s", "source_start_mm", "source_end_mm"))
                    or duration <= 0 or match["start_s"] < 0 or length <= 0
                    or match["source_start_mm"] < 0
                    or match["source_end_mm"] > source_path["length_mm"] + 1e-6
                    or not math.isclose(length, match["source_end_mm"] - match["source_start_mm"], abs_tol=1e-6)
                    or not math.isclose(length / duration, source_path["speed_mm_s"], rel_tol=1e-7)
                    or not math.isclose(match["start_s"], source_path["start_s"]
                                        + match["source_start_mm"] / source_path["speed_mm_s"], abs_tol=1e-6)):
                raise ValueError("{} 的路径范围、速度或原始时间不一致。".format(match["label"]))


def export_limitations(config):
    limitations = []
    for group in config["groups"]:
        for component in group["crop"]["components"]:
            if component["element_ids"] and component.get("has_new_cut_faces", not component["keep_whole"]):
                limitations.append("{} / {}：本地 API 未提供切割面独立绝热与原表面散热的设置接口。".format(
                    group["name"], component["name"]))
    return limitations


def validate_fixed_nodes_reference(text):
    root = ET.fromstring(text)
    if root.tag != "wlFixedNodes" or root.get("version") != "2026.2":
        raise ValueError("固定节点参考文件必须为 Simufact 2026.2 的 wlFixedNodes。")
    for field in ("name", "start_time_active", "stop_time_active", "start_time", "stop_time",
                  "x_fixed_dof", "y_fixed_dof", "z_fixed_dof"):
        if len(root.findall(field)) != 1:
            raise ValueError("固定节点参考 XML 缺少唯一字段 {}。".format(field))
    if not root.findall("fixed_displacements"):
        raise ValueError("固定节点参考 XML 没有实际节点记录。")
    for element in root.findall("fixed_displacements"):
        if (not element.get("node_Number", "").isdigit() or int(element.get("node_Number")) <= 0
                or not element.get("model_internal_name") or not element.get("model_display_name")):
            raise ValueError("固定节点参考 XML 的节点字段不完整。")


def node_coordinates_mm(vector):
    from simufact import UnitValueLength
    if not vector.unit:
        raise ValueError("网格节点坐标缺少长度单位。")
    return tuple(float(UnitValueLength(value, vector.unit).value("mm"))
                 for value in (vector.x, vector.y, vector.z))


def match_node_positions(nodes, samples, allow_missing=False):
    from scipy.spatial import cKDTree
    if not nodes or not samples:
        raise ValueError("固定节点匹配需要非空网格与采样节点。")
    tree = cKDTree([xyz for _, xyz in nodes])
    result = []
    for sample in samples:
        indices = tree.query_ball_point(sample["xyz_mm"], 1e-4)
        if not indices and allow_missing:
            continue
        if len(indices) != 1:
            raise ValueError("原节点 {} 在导入网格中找到 {} 个坐标匹配节点。".format(sample["node_id"], len(indices)))
        result.append(nodes[indices[0]])
    if len({node_id for node_id, _ in result}) != len(result):
        raise ValueError("多个原节点匹配到了同一个导入节点。")
    return result


def update_fixed_nodes_tree(root, constraint, geometry_names):
    if root.tag != "wlFixedNodes" or root.get("version") != "2026.2":
        raise ValueError("生成的固定节点对象 XML 版本或类型不符合参考样例。")
    names = root.findall("name")
    if len(names) != 1 or names[0].get("display_name") != constraint["name"] or not names[0].get("internal_name"):
        raise ValueError("固定节点对象名称或内部名称不唯一。")
    for direction, enabled in zip("xyz", constraint["axes"]):
        elements = root.findall(direction + "_fixed_dof")
        if len(elements) != 1:
            raise ValueError("固定节点对象缺少方向字段。")
        elements[0].text = str(enabled).lower()
    for element in list(root.findall("fixed_displacements")):
        root.remove(element)
    for node in constraint["nodes"]:
        display_name, internal_name = geometry_names[node["component"]]
        if display_name != node["geometry"] or not internal_name:
            raise ValueError("固定节点的 Geometry 名称与保存文件不一致。")
        ET.SubElement(root, "fixed_displacements", node_Number=str(node["node_id"]),
                      model_display_name=display_name, model_internal_name=internal_name)


def patch_saved_constraints(project_directory, resolved):
    documents = [(path, ET.parse(path)) for path in project_directory.rglob("*.xml")]
    pending = []
    for process_name, constraints in resolved.items():
        process_files = [path for path, tree in documents if tree.getroot().tag == "wlProcess"
                         and tree.getroot().find("name") is not None
                         and tree.getroot().find("name").get("display_name") == process_name]
        if len(process_files) != 1:
            raise ValueError("无法唯一找到 {} 的 Process XML。".format(process_name))
        process_directory = process_files[0].parent
        process_root = next(tree.getroot() for path, tree in documents if path == process_files[0])
        objects = [(path, tree) for path, tree in documents if path.is_relative_to(process_directory)]
        for constraint in constraints:
            selected = [(path, tree) for path, tree in objects if tree.getroot().tag == "wlFixedNodes"
                        and tree.getroot().find("name") is not None
                        and tree.getroot().find("name").get("display_name") == constraint["name"]]
            if len(selected) != 1:
                raise ValueError("无法唯一找到 {} 的 FixedNodes XML。".format(constraint["name"]))
            fixed_name = selected[0][1].getroot().find("name")
            references = [element for element in process_root.findall(".//name")
                          if element.get("internal_name") == fixed_name.get("internal_name")
                          and element.get("display_name") == constraint["name"]]
            if len(references) != 1:
                raise ValueError("Process XML 未唯一引用固定节点对象 {}。".format(constraint["name"]))
            geometry_names = {}
            for component in {node["component"] for node in constraint["nodes"]}:
                bodies = [tree.getroot() for _, tree in objects if tree.getroot().find("geometry") is not None
                          and tree.getroot().find("name") is not None
                          and tree.getroot().find("name").get("display_name") == component]
                if len(bodies) != 1:
                    raise ValueError("组件 {} 的 Geometry 引用不唯一。".format(component))
                geometry = bodies[0].find("geometry")
                geometry_names[component] = (geometry.get("display_name"), geometry.get("internal_name"))
            path, tree = selected[0]
            update_fixed_nodes_tree(tree.getroot(), constraint, geometry_names)
            pending.append((path, tree))
    for path, tree in pending:
        tree.write(path, encoding="utf-8", xml_declaration=True)


def enable_strain_output(source_path, target_path):
    tree = ET.parse(source_path)
    root = tree.getroot()
    if root.tag != "advanced_process_parameters_file":
        raise ValueError("工艺参数 XML 根元素无效。")
    elements = root.findall("additional_result_output_settings/is_output_strain_tensors")
    if len(elements) != 1:
        raise ValueError("工艺参数 XML 缺少唯一的 is_output_strain_tensors。")
    elements[0].set("value", "true")
    tree.write(target_path, encoding="utf-8", xml_declaration=True)


def matching_trajectories(process, paths):
    candidates = [(robot, trajectory) for robot in process.all_robots
                  for trajectory in robot.all_trajectories]
    if len(candidates) != len(paths):
        raise ValueError("来源 Process 的轨迹数量与 DAT 焊路数量不一致。")
    matches = {}
    for path in paths:
        selected = []
        for robot, trajectory in candidates:
            parameter = trajectory.heat_source_parameters
            if parameter is None or parameter.heat_source is None:
                raise ValueError("{} 缺少原热源。".format(trajectory.name))
            if (abs(float(trajectory.original_length.value("mm")) - path["length_mm"]) <= 0.02
                    and abs(float(parameter.velocity.value("mm/s")) - path["speed_mm_s"]) <= 0.03):
                selected.append((robot, trajectory))
        if len(selected) != 1:
            raise ValueError("焊路 {} 无法通过长度与速度唯一匹配原轨迹。".format(path["id"]))
        matches[path["id"]] = selected[0]
    if len({(robot.name, trajectory.index) for robot, trajectory in matches.values()}) != len(paths):
        raise ValueError("来源焊路没有匹配到不同的原轨迹。")
    return matches


def schedule_matches(matches, source_paths, states):
    paths = {path["id"]: path for path in source_paths}
    schedules = {}
    for match in sorted(matches, key=lambda item: item["start_s"]):
        state = states[match["path_id"]]
        path = paths[match["path_id"]]
        lead = state["lead_s"] if math.isclose(match["source_start_mm"], 0., abs_tol=1e-7) else 0.
        follow = state["follow_s"] if math.isclose(match["source_end_mm"], path["length_mm"], abs_tol=1e-7) else 0.
        rows = schedules.setdefault(state["robot"], [])
        previous_end = rows[-1]["end_s"] if rows else state["robot_start_s"]
        pause = match["start_s"] - lead - previous_end
        if pause < -1e-7:
            raise ValueError("{} 的原始等待时间与所选焊路发生重叠。".format(match["label"]))
        rows.append({"match": match, "pause_s": max(0., pause), "lead_s": lead,
                     "follow_s": follow, "end_s": match["end_s"] + follow})
    return schedules


def main():
    validate_config(CONFIG)
    validate_fixed_nodes_reference(CONFIG["fixed_nodes_reference_xml"])

    from simufact import Matrix4x4, UnitValueTemperature, UnitValueTime, Vector3D
    from simufact.welding import current_project, open_project

    directory = Path(__file__).resolve().parent
    project = current_project()
    if project is None:
        raise RuntimeError("请在 Simufact Welding 中打开来源项目。")
    source = project.process(CONFIG["source_process"])
    if source is None:
        raise ValueError("找不到来源 Process。")
    criteria = source.termination_criteria
    if criteria.number_body_criteria or criteria.number_global_criteria or criteria.number_node_criteria:
        raise ValueError("来源 Process 已有停止条件，需要确认其与局部冷却停止条件的关系。")
    existing_names = {process.name for process in project.all_processes}
    if any(group["name"] in existing_names for group in CONFIG["groups"]):
        raise ValueError("项目中已经存在目标试算组 Process。")
    project_directory = directory / "local_trial_project"
    project_file = project_directory / "LocalTrials.swproj"
    snapshot_directory = directory / ".work" / "local_trial_snapshot"
    if project_directory.exists() or snapshot_directory.exists():
        raise ValueError("局部试算项目输出目录已经存在，请使用新的导出目录。")
    source_matches = matching_trajectories(source, CONFIG["source_paths"])
    inherited_constraints = []
    for fixed in source.all_fixed_nodes:
        samples = []
        for geometry, node_id, coords in fixed.nodes:
            components = [body for body in source.all_components if body.geometry.name == geometry.name]
            if len(components) != 1:
                raise ValueError("原约束 Geometry {} 无法唯一关联组件。".format(geometry.name))
            if tuple(tuple(row) for row in components[0].transformation) != ((1., 0., 0., 0.), (0., 1., 0., 0.), (0., 0., 1., 0.), (0., 0., 0., 1.)):
                raise ValueError("原固定节点所属组件包含位置变换，需要确认约束坐标所在坐标系。")
            samples.append({"component": components[0].name, "node_id": node_id, "xyz_mm": node_coordinates_mm(coords)})
        inherited_constraints.append({"name": fixed.name, "axes": [axis in fixed.fixed_directions for axis in "xyz"],
                                      "samples": samples, "inherited": True})
    states = {}
    for path_id, (robot, trajectory) in source_matches.items():
        if tuple(trajectory.all_geometries):
            raise ValueError("{} 包含焊道几何，不能仅替换轨迹坐标。".format(trajectory.name))
        states[path_id] = {"robot": robot.name, "robot_start_s": float(robot.start_time.value("s")),
                           "lead_s": float(trajectory.lead_time.value("s")),
                           "follow_s": float(trajectory.follow_up_time.value("s"))}
    schedules = {group["name"]: schedule_matches(group["matches"], CONFIG["source_paths"], states)
                 for group in CONFIG["groups"]}
    source_components = {component.name for component in source.all_components}
    cover_fixtures = [fixture for collection in (source.all_bearings, source.all_clampings, source.all_fixed_geometries)
                      for fixture in collection if fixture.name.casefold() == "cover"]
    weld_names = {line.name for line in project.all_weld_lines}
    for group in CONFIG["groups"]:
        for fixture in cover_fixtures:
            records = [component for component in group["crop"]["components"] if component["name"] == fixture.name]
            if len(records) != 1 or not records[0].get("mesh_file"):
                raise ValueError("Cover 是夹具对象，当前配置缺少与其 Geometry 对应的可裁剪网格；未创建局部项目。")
        if {component["name"] for component in group["crop"]["components"]} != source_components:
            raise ValueError("组件清单必须覆盖来源 Process 的全部组件；不支持隐式保留未列出的组件。")
        for robot_name, rows in schedules[group["name"]].items():
            if any("{}_{}_{:04d}".format(group["name"], robot_name, index + 1) in weld_names
                   for index in range(len(rows))):
                raise ValueError("项目中已经存在本次需要创建的 WeldLine 名称。")
        for component in group["crop"]["components"]:
            if source.component(component["name"]) is None:
                raise ValueError("来源 Process 缺少组件 {}。".format(component["name"]))
            if not component["keep_whole"] and component["element_ids"]:
                mesh_file = (directory / component["mesh_file"]).resolve()
                if not mesh_file.is_relative_to(directory) or not mesh_file.is_file():
                    raise ValueError("裁剪网格文件不存在或超出导出目录。")
        if not any(component["element_ids"] and component["name"].casefold() not in ("base", "cover")
                   for component in group["crop"]["components"]):
            raise ValueError("没有可用于温度停止条件的板件。")
        inherited_names = {constraint["name"] for constraint in inherited_constraints}
        if inherited_names.intersection(constraint["name"] for constraint in group["constraints"]):
            raise ValueError("新固定节点约束名称与原 Process 的约束名称重复。")
    work = directory / ".work"
    work.mkdir(exist_ok=True)
    source_xml = work / "source_parameters.xml"
    strain_xml = work / "strain_parameters.xml"
    source.process_parameters.export_all_settings(str(source_xml))
    enable_strain_output(source_xml, strain_xml)
    project_directory.mkdir()
    project.save_as(str(project_file), mode="project_only")
    project = current_project()
    source = project.process(CONFIG["source_process"])
    resolved = {}

    for group in CONFIG["groups"]:
        target = source.copy()
        target.name = group["name"]
        if target.name != group["name"] or source.name != CONFIG["source_process"]:
            raise ValueError("Process 名称读取检查失败。")
        target_paths = matching_trajectories(target, CONFIG["source_paths"])
        parameters = {path_id: trajectory.heat_source_parameters for path_id, (_, trajectory) in target_paths.items()}
        removed_names = {component["name"] for component in group["crop"]["components"] if not component["element_ids"]}
        original_pairs = {(pair.touching_body.name, pair.contacted_body.name): pair
                          for pair in target.contact_table.all_contact_pairs()
                          if pair.touching_body.name not in removed_names and pair.contacted_body.name not in removed_names}
        for component in group["crop"]["components"]:
            if not component["element_ids"]:
                target.delete_component(component["name"])
                continue
            if not component["keep_whole"]:
                imported = project.import_geometry(str(directory / component["mesh_file"]), unit="mm", as_single_body=True)
                if imported is None:
                    raise RuntimeError("导入裁剪网格失败。")
                body = target.component(component["name"])
                body.geometry = imported
                # BDF 使用 DAT 的全局坐标，组件不再施加原几何的位置变换。
                body.transformation = Matrix4x4(((1., 0., 0., 0.), (0., 1., 0., 0.),
                                                (0., 0., 1., 0.), (0., 0., 0., 1.)))
                mesh = body.volume_mesh()
                if (mesh is None or mesh.number_nodes != len(component["node_ids"])
                        or mesh.number_elements != len(component["element_ids"])):
                    raise ValueError("{} 导入后的节点数或单元数与裁剪网格不一致。".format(component["name"]))
        target.contact_table.update()
        for pair in target.contact_table.all_contact_pairs():
            if (pair.touching_body.name, pair.contacted_body.name) not in original_pairs:
                pair.active = False
                target.contact_table.set_contact_pair(pair)
        for pair in original_pairs.values():
            target.contact_table.set_contact_pair(pair)
        for robot in target.all_robots:
            for trajectory in tuple(robot.all_trajectories):
                robot.delete_trajectory(trajectory)
            for index, row in enumerate(schedules[group["name"]].get(robot.name, [])):
                match = row["match"]
                name = "{}_{}_{:04d}".format(group["name"], robot.name, index + 1)
                line = project.new_weld_line(name, "local-vector")
                for point, normal in zip(match["points_mm"], match["normals"]):
                    line.add_point(True, Vector3D(point, "mm"), normal)
                trajectory = robot.new_trajectory(line, UnitValueTime(row["pause_s"], "s"),
                                                 UnitValueTime(row["lead_s"], "s"),
                                                 UnitValueTime(row["follow_s"], "s"), UnitValueTime(0., "s"))
                trajectory.heat_source_parameters = parameters[match["path_id"]]
                trajectory.index = index
                if not math.isclose(float(trajectory.welding_time.value("s")), match["end_s"] - match["start_s"], abs_tol=1e-6):
                    raise ValueError("{} 的实际焊接耗时与来源 DAT 不一致。".format(name))
        target.process_parameters.import_all_settings(str(strain_xml))
        last_end = max(row["end_s"] for rows in schedules[group["name"]].values() for row in rows)
        target.process_parameters.time_control.analysis_end_time = UnitValueTime(last_end + CONFIG["max_cooling_s"], "s")
        plate_names = tuple(component["name"] for component in group["crop"]["components"]
                            if component["element_ids"] and component["name"].casefold() not in ("base", "cover"))
        target.termination_criteria.new_body_criterion(
            targets=plate_names, loadcases=("Cooling",), value=UnitValueTemperature(CONFIG["cooling_limit_c"], "°C"),
            type="MaximumTemperature", only_last_loadcase=True, termination_mode="Analysis")
        resolved[group["name"]] = []
        for constraint in inherited_constraints + group["constraints"]:
            directions = tuple(axis for axis, enabled in zip("xyz", constraint["axes"]) if enabled)
            if not constraint.get("inherited", False):
                fixed = target.new_fixed_nodes(name=constraint["name"], fixed_directions=directions)
                if fixed is None or fixed.name != constraint["name"]:
                    raise RuntimeError("创建空 FixedNodes 对象失败。")
            nodes = []
            for component in {sample["component"] for sample in constraint["samples"]}:
                body = target.component(component)
                if body is None and constraint.get("inherited", False):
                    continue
                matrix = tuple(tuple(row) for row in body.transformation)
                if matrix != ((1., 0., 0., 0.), (0., 1., 0., 0.), (0., 0., 1., 0.), (0., 0., 0., 1.)):
                    raise ValueError("固定节点所属组件仍含位置变换，不能直接使用全局坐标匹配。")
                mesh = body.geometry.volume_mesh()
                actual_nodes = [(node.id, node_coordinates_mm(node.coords)) for node in mesh.nodes]
                samples = [sample for sample in constraint["samples"] if sample["component"] == component]
                for node_id, xyz in match_node_positions(actual_nodes, samples, allow_missing=constraint.get("inherited", False)):
                    nodes.append({"component": component, "geometry": body.geometry.name, "node_id": node_id, "xyz_mm": xyz})
            if not nodes:
                if not constraint.get("inherited", False):
                    raise ValueError("新固定节点约束没有有效节点。")
                target.delete_fixed_nodes(constraint["name"])
                continue
            resolved[group["name"]].append({"name": constraint["name"], "axes": constraint["axes"], "nodes": nodes})
        project.save()
        print("已保存 {} 的局部模型，尚未生成最终 DAT。".format(group["name"]))
        for limitation in export_limitations({"groups": [group]}):
            print(limitation)
        print("请在 Simufact 中确认新增切割面绝热、原表面散热、组件接触、材料方向与 solid-shell 方向，完成模型检查后保存，并使用 Simufact 的生成求解输入文件操作生成最终 DAT。")
    snapshot_directory.mkdir(parents=True)
    project.save_as(str(snapshot_directory / "WorkingCopy.swproj"), mode="project_only")
    patch_saved_constraints(project_directory, resolved)
    open_project(str(project_file))
    project = current_project()
    for process_name, constraints in resolved.items():
        for constraint in constraints:
            fixed = project.process(process_name).fixed_nodes(constraint["name"])
            expected = {(node["geometry"], node["node_id"]): node["xyz_mm"] for node in constraint["nodes"]}
            actual = {(geometry.name, node_id): node_coordinates_mm(coords) for geometry, node_id, coords in fixed.nodes}
            if (set(actual) != set(expected) or len(fixed.nodes) != len(expected)
                    or any(math.dist(actual[key], expected[key]) > 1e-4 for key in expected)
                    or set(fixed.fixed_directions) != {axis for axis, enabled in zip("xyz", constraint["axes"]) if enabled}):
                raise ValueError("重新打开项目后，{} 的约束节点或固定方向检查失败。".format(constraint["name"]))
    print("已重新打开独立项目并核对固定节点：{}。当前项目已切换，来源项目保持原文件。".format(project_file))


if __name__ == "__main__":
    main()
