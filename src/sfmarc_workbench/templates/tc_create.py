import math
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


def validate_config(config):
    for field in ("source_process", "target_process"):
        if not isinstance(config[field], str) or not config[field].strip():
            raise ValueError("{} 必须包含有效 Process 名称。".format(field))
    if config["source_process"] == config["target_process"]:
        raise ValueError("来源 Process 与目标 Process 的名称必须不同。")
    if any(character in '<>:"/\\|?*' or ord(character) < 32 for character in config["target_process"]):
        raise ValueError("目标 Process 名称必须能够用于热源 XML 文件名。")
    digest = config["source_sha256"]
    if (not isinstance(digest, str) or len(digest) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in digest)):
        raise ValueError("来源 DAT 的 SHA256 无效。")
    room_c, melting_c = config["room_c"], config["melting_c"]
    if not math.isfinite(room_c) or not math.isfinite(melting_c) or melting_c <= room_c:
        raise ValueError("室温与熔化温度无效。")
    if not isinstance(config["coverage_note"], str):
        raise ValueError("曲线覆盖范围说明必须为文本。")
    paths = config["paths"]
    if not paths:
        raise ValueError("没有需要创建 Thermal Cycle 的焊路。")
    identifiers = set()
    for path in paths:
        path_id = path["id"]
        if type(path_id) is not int or path_id <= 0 or path_id in identifiers:
            raise ValueError("焊路编号必须为不同的正整数。")
        identifiers.add(path_id)
        values = (path["length_mm"], path["speed_mm_s"], path["start_s"], path["end_s"])
        if (any(not math.isfinite(value) for value in values)
                or path["length_mm"] <= 0 or path["speed_mm_s"] <= 0
                or path["start_s"] < 0 or path["end_s"] <= path["start_s"]):
            raise ValueError("焊路 {} 的长度、速度或时间无效。".format(path_id))
        segments = path["segments"]
        if not segments:
            raise ValueError("焊路 {} 没有分段。".format(path_id))
        previous_point, previous_time = None, path["start_s"]
        for index, segment in enumerate(segments, 1):
            if segment["path_id"] != path_id or segment["index"] != index:
                raise ValueError("焊路 {} 的分段编号或顺序无效。".format(path_id))
            if not isinstance(segment["label"], str) or not segment["label"].strip():
                raise ValueError("分段名称不能为空。")
            points, normals = segment["points_mm"], segment["normals"]
            if (len(points) < 2 or len(normals) != len(points)
                    or any(len(row) != 3 or any(not math.isfinite(value) for value in row)
                           for row in points + normals)
                    or any(not math.isclose(math.hypot(*normal), 1., abs_tol=1e-6) for normal in normals)):
                raise ValueError("{} 的坐标或方向无效。".format(segment["label"]))
            lengths = [math.dist(first, second) for first, second in zip(points, points[1:])]
            if (any(length <= 0 for length in lengths)
                    or not math.isclose(math.fsum(lengths), segment["length_mm"], rel_tol=1e-9, abs_tol=1e-6)):
                raise ValueError("{} 的几何长度与配置不一致。".format(segment["label"]))
            if previous_point is not None and math.dist(previous_point, points[0]) > 1e-6:
                raise ValueError("焊路 {} 的分段几何不连续。".format(path_id))
            if (not math.isfinite(segment["start_s"]) or not math.isfinite(segment["end_s"])
                    or segment["end_s"] <= segment["start_s"]
                    or not math.isclose(segment["start_s"], previous_time, rel_tol=0, abs_tol=1e-7)):
                raise ValueError("{} 的时间顺序不连续。".format(segment["label"]))
            previous_point, previous_time = points[-1], segment["end_s"]
            curve = segment["curve"]
            half_duration = (segment["end_s"] - segment["start_s"]) / 2
            if (len(curve) < 2 or any(len(row) != 2 for row in curve)
                    or any(not math.isfinite(value) for row in curve for value in row)
                    or any(not 0 <= row[1] <= 2 for row in curve)
                    or any(first[0] >= second[0] for first, second in zip(curve, curve[1:]))
                    or curve[0][0] < -half_duration - 1e-9 or curve[-1][0] > half_duration + 1e-9
                    or not curve[0][0] <= 0 < curve[-1][0]):
                raise ValueError("{} 的归一化曲线或时间顺序无效。".format(segment["label"]))
            peak = max(range(len(curve)), key=lambda row: curve[row][1])
            if peak in (0, len(curve) - 1):
                raise ValueError("{} 的温度曲线峰值必须位于时间记录内部。".format(segment["label"]))
            samples = segment["samples"]
            if not samples:
                raise ValueError("{} 没有采样节点。".format(segment["label"]))
            keys = set()
            for sample in samples:
                key = (sample["component"], sample["node_id"])
                if (not isinstance(key[0], str) or not key[0] or type(key[1]) is not int or key[1] <= 0
                        or key in keys or len(sample["xyz_mm"]) != 3
                        or any(not math.isfinite(value) for value in sample["xyz_mm"])
                        or any(not math.isfinite(sample[field]) for field in ("station_mm", "arrival_s", "distance_mm"))
                        or sample["distance_mm"] < 0):
                    raise ValueError("{} 的采样节点记录无效或重复。".format(segment["label"]))
                keys.add(key)
            heat_source_dimensions(segment, 0.)
        if (not math.isclose(math.fsum(segment["length_mm"] for segment in segments), path["length_mm"], abs_tol=1e-6)
                or not math.isclose(previous_time, path["end_s"], rel_tol=0, abs_tol=1e-7)
                or math.dist(segments[0]["points_mm"][0], path["points_mm"][0]) > 1e-6
                or math.dist(segments[-1]["points_mm"][-1], path["points_mm"][-1]) > 1e-6):
            raise ValueError("焊路 {} 的分段总长度、端点或时间与原始焊路不一致。".format(path_id))
    root = ET.fromstring(config["heat_source_xml"])
    if (root.tag != "HeatSources" or len(root.findall("WeldingParameters")) != 1
            or len(root.findall("WeldingParameters/velocity")) != 1):
        raise ValueError("热源参考 XML 缺少唯一的 WeldingParameters 或 velocity。")


def segment_speed(segment):
    return segment["length_mm"] / (segment["end_s"] - segment["start_s"])


def sample_projection(segment, position):
    candidates = []
    points, normals = segment["points_mm"], segment["normals"]
    for index, (first, second) in enumerate(zip(points, points[1:])):
        vector = [b - a for a, b in zip(first, second)]
        delta = [value - origin for value, origin in zip(position, first)]
        fraction = min(1., max(0., math.fsum(a * b for a, b in zip(delta, vector))
                                  / math.fsum(value * value for value in vector)))
        offset = [value - fraction * step for value, step in zip(delta, vector)]
        normal = [(1 - fraction) * a + fraction * b for a, b in zip(normals[index], normals[index + 1])]
        magnitude = math.hypot(*normal)
        if magnitude <= 1e-12:
            raise ValueError("{} 的相邻方向无法插值。".format(segment["label"]))
        normal = [value / magnitude for value in normal]
        depth = -math.fsum(a * b for a, b in zip(offset, normal))
        radial = math.hypot(*(value + depth * axis for value, axis in zip(offset, normal)))
        candidates.append((math.hypot(*offset), radial, depth))
    _, radial, depth = min(candidates)
    return radial, depth


def heat_source_dimensions(segment, source_depth_mm):
    radius, depth = segment["radius_mm"], segment["depth_mm"]
    if (not math.isfinite(radius) or not math.isfinite(depth) or radius <= 0 or depth <= 0
            or not math.isfinite(source_depth_mm) or source_depth_mm < 0):
        raise ValueError("{} 的热源半径或深度无效。".format(segment["label"]))
    depth = max(depth, source_depth_mm)
    for sample in segment["samples"]:
        radial, axial = sample_projection(segment, sample["xyz_mm"])
        if axial < -1e-6:
            raise ValueError("{} 的采样节点 {} 位于热源方向上方。".format(segment["label"], sample["node_id"]))
        radius, depth = max(radius, radial + 1e-6), max(depth, axial + 1e-6)
    return radius, depth


def thermal_cycle_element(curve):
    element = ET.Element("thermalCycle")
    ET.SubElement(element, "fullCycle", enable="false")
    ET.SubElement(element, "subDivisionMode").text = "noDivision"
    table = ET.SubElement(element, "xyTable", rowCount=str(len(curve)),
                          xUnit="seconds", yUnit="1/1")
    for index, (time_s, temperature) in enumerate(curve, 1):
        ET.SubElement(table, "row", nb=str(index)).text = "{:.15g};{:.15g}".format(time_s, temperature)
    return element


def write_import_files(config, output_directory, dimensions=None):
    files = {}
    segments = [segment for path in config["paths"] for segment in path["segments"]]
    for ordinal, segment in enumerate(segments, 1):
        key = (segment["path_id"], segment["index"])
        tree = ET.ElementTree(ET.fromstring(config["heat_source_xml"]))
        parameters = tree.getroot().find("WeldingParameters")
        parameters.find("velocity").text = "{:.15g}".format(segment_speed(segment) / 1000)
        for tag in ("transient", "thermalCycle"):
            for element in parameters.findall(tag):
                parameters.remove(element)
        parameters.append(thermal_cycle_element(segment["curve"]))
        conical = tree.getroot().find("WeldingHeatSource[@type='Conical']")
        if conical is None:
            raise ValueError("热源参考 XML 缺少 Conical 定义。")
        radius, depth = (dimensions[key] if dimensions is not None else
                         heat_source_dimensions(segment, float(conical.findtext("shape/depth")) * 1000))
        conical.find("volumeRatio").text = "1"
        for field, value in (("uppperRadius", radius), ("lowerRadius", radius), ("depth", depth)):
            conical.find("shape/" + field).text = "{:.15g}".format(value / 1000)
        filename = output_directory / "{}_{:04d}.xml".format(config["target_process"], ordinal)
        with filename.open("xb") as stream:
            tree.write(stream, encoding="utf-8", xml_declaration=True)
        written = ET.parse(str(filename)).getroot().find("WeldingParameters/thermalCycle")
        if ET.tostring(written) != ET.tostring(parameters.find("thermalCycle")):
            raise ValueError("{} 的热源 XML 写入检查失败。".format(segment["label"]))
        files[key] = filename
    return files


def matching_trajectories(process, paths):
    candidates = [(robot, trajectory) for robot in process.all_robots
                  for trajectory in robot.all_trajectories]
    if len(candidates) != len(paths):
        raise ValueError("Process {} 的轨迹数量为 {}，配置焊路数量为 {}。".format(
            process.name, len(candidates), len(paths)))
    matches = {}
    for path in paths:
        selected = []
        for robot, trajectory in candidates:
            parameter = trajectory.heat_source_parameters
            if parameter is None or parameter.heat_source is None:
                raise ValueError("轨迹 {} 缺少热源参数或热源。".format(trajectory.name))
            length = float(trajectory.original_length.value("mm"))
            velocity = float(parameter.velocity.value("mm/s"))
            if abs(length - path["length_mm"]) <= 0.02 and abs(velocity - path["speed_mm_s"]) <= 0.03:
                selected.append((robot, trajectory))
        if len(selected) != 1:
            raise ValueError("焊路 {} 的长度与速度匹配得到 {} 条轨迹，无法唯一确认。".format(
                path["id"], len(selected)))
        matches[path["id"]] = selected[0]
    if len({(robot.name, trajectory.index) for robot, trajectory in matches.values()}) != len(paths):
        raise ValueError("配置焊路没有匹配到不同的轨迹。")
    for robot in process.all_robots:
        ordered = sorted((trajectory.index, path["start_s"], path["id"])
                         for path in paths for matched_robot, trajectory in [matches[path["id"]]]
                         if matched_robot.name == robot.name)
        if any(first[1] >= second[1] for first, second in zip(ordered, ordered[1:])):
            raise ValueError("Robot {} 的轨迹顺序与配置开始时间顺序不符。".format(robot.name))
    return matches


def trajectory_timing_state(robot, trajectory):
    return (robot.name, float(robot.start_time.value("s")), trajectory.name, trajectory.index,
            float(trajectory.original_length.value("mm")),
            float(trajectory.pause_time_start.value("s")), float(trajectory.lead_time.value("s")),
            float(trajectory.follow_up_time.value("s")), float(trajectory.pause_time_end.value("s")))


def trajectory_state(robot, trajectory):
    parameter = trajectory.heat_source_parameters
    heat = parameter.heat_source
    lengths = ("conical_upper_radius", "conical_lower_radius", "conical_depth", "disc_radius", "surface_depth")
    scalars = ("volume_heat_fraction", "conical_gaussian_parameter", "disc_gaussian_parameter")
    return (trajectory_timing_state(robot, trajectory), parameter.name,
            float(parameter.velocity.value("mm/s")), parameter.specification.mode,
            tuple(float(getattr(heat, field).value("mm")) for field in lengths),
            tuple(float(getattr(heat, field)) for field in scalars))


def check_imported_parameter(parameter, segment):
    specification = parameter.specification
    if specification.mode != "thermal-cycle" or specification.full_thermal_cycle:
        raise ValueError("导入热源必须为 Thermal Cycle，Full thermal cycle 必须关闭。")
    table = specification.thermal_cycle
    if len(table.x_columns) != 1 or len(table.y_columns) != 1:
        raise ValueError("导入热循环表格必须包含一个时间列和一个温度列。")
    time_column = table.x_columns[0]
    time_column.convert_to_unit("s")
    times, temperatures = time_column.data, table.y_columns[0].data
    curve = segment["curve"]
    if len(times) != len(curve) or len(temperatures) != len(curve) or any(
            not math.isclose(float(value), expected, rel_tol=1e-7, abs_tol=1e-8)
            for actual, target in zip(zip(times, temperatures), curve)
            for value, expected in zip(actual, target)):
        raise ValueError("{} 的导入热循环曲线与配置不符。".format(segment["label"]))


def main():
    validate_config(CONFIG)
    directory = Path(__file__).resolve().parent
    source_name, target_name = CONFIG["source_process"], CONFIG["target_process"]
    paths = CONFIG["paths"]

    from simufact import UnitValueLength, UnitValueSpeed, UnitValueTime, Vector3D
    from simufact.welding import HeatSourceLaser, current_project

    project = current_project()
    if project is None:
        raise RuntimeError("请在 Simufact Welding 2026.2 中打开目标项目。")
    if any(process.name == target_name for process in project.all_processes):
        raise ValueError("项目中已经存在 {}，已经终止创建。".format(target_name))
    source = project.process(source_name)
    if source is None:
        raise ValueError("项目中未找到 {}。".format(source_name))
    source_paths = matching_trajectories(source, paths)
    for _, trajectory in source_paths.values():
        if not isinstance(trajectory.heat_source_parameters.heat_source, HeatSourceLaser):
            raise ValueError("{} 需要激光热源参数。".format(trajectory.name))
        if tuple(trajectory.all_geometries):
            raise ValueError("{} 包含焊道几何，无法仅按坐标替换轨迹。".format(trajectory.name))
    source_states = {path_id: trajectory_state(*pair) for path_id, pair in source_paths.items()}
    segments = [segment for path in paths for segment in path["segments"]]
    parameter_names = {parameter.name for parameter in project.all_heat_source_parameters}
    weld_names = {weld_line.name for weld_line in project.all_weld_lines}
    new_names = {(segment["path_id"], segment["index"]): "{}_{:04d}".format(target_name, ordinal)
                 for ordinal, segment in enumerate(segments, 1)}
    if (parameter_names | weld_names).intersection(new_names.values()):
        raise ValueError("项目中已经存在本次需要创建的 WeldLine 或热源参数名称。")
    dimensions = {(segment["path_id"], segment["index"]): heat_source_dimensions(
        segment, float(source_paths[segment["path_id"]][1].heat_source_parameters.heat_source.conical_depth.value("mm")))
        for segment in segments}
    work_directory = directory / ".work"
    work_directory.mkdir(exist_ok=True)
    import_directory = Path(tempfile.mkdtemp(prefix="thermal_cycle_", dir=str(work_directory)))
    import_files = write_import_files(CONFIG, import_directory, dimensions)
    target = source.copy()
    target.name = target_name
    if target.name != target_name or source.name != source_name:
        raise ValueError("复制后的 Process 名称检查失败。")
    target_paths = matching_trajectories(target, paths)
    if {path_id: trajectory_state(*pair) for path_id, pair in target_paths.items()} != source_states:
        raise ValueError("复制后的轨迹参数与来源 Process 不一致，已经终止配置。")

    for robot in target.all_robots:
        robot_paths = sorted((path for path in paths if target_paths[path["id"]][0].name == robot.name),
                             key=lambda path: source_states[path["id"]][0][3])
        for path in robot_paths:
            robot.delete_trajectory(target_paths[path["id"]][1])
        if tuple(robot.all_trajectories):
            raise ValueError("Robot {} 仍包含未替换的轨迹。".format(robot.name))
        created = []
        for path in robot_paths:
            state = source_states[path["id"]][0]
            source_heat = source_paths[path["id"]][1].heat_source_parameters.heat_source
            if float(robot.start_time.value("s")) != state[1]:
                raise ValueError("Robot {} 的开始时间发生变化。".format(robot.name))
            for segment in path["segments"]:
                key = (path["id"], segment["index"])
                name = new_names[key]
                radius, depth = dimensions[key]
                parameter = project.import_heat_source_parameters_si(str(import_files[key]))
                if parameter is None or parameter.name in parameter_names:
                    raise ValueError("没有获得独立的导入热源参数，已经终止配置。")
                parameter.name = name
                if parameter.name != name:
                    raise ValueError("{} 的热源参数名称设置失败。".format(segment["label"]))
                parameter_names.add(name)
                parameter.heat_source = HeatSourceLaser(
                    volume_heat_fraction=1., conical_upper_radius=UnitValueLength(radius, "mm"),
                    conical_lower_radius=UnitValueLength(radius, "mm"), conical_depth=UnitValueLength(depth, "mm"),
                    conical_gaussian_parameter=float(source_heat.conical_gaussian_parameter))
                parameter.velocity = UnitValueSpeed(segment_speed(segment), "mm/s")
                check_imported_parameter(parameter, segment)
                heat = parameter.heat_source
                if (any(not math.isclose(float(getattr(heat, field).value("mm")), expected, abs_tol=1e-7)
                        for field, expected in (("conical_upper_radius", radius), ("conical_lower_radius", radius), ("conical_depth", depth)))
                        or not math.isclose(float(parameter.velocity.value("mm/s")), segment_speed(segment), rel_tol=1e-8)):
                    raise ValueError("{} 的热源尺寸或速度读取检查失败。".format(segment["label"]))
                weld_line = project.new_weld_line(name, "local-vector")
                if weld_line is None or weld_line.name != name:
                    raise ValueError("{} 的 WeldLine 创建失败。".format(segment["label"]))
                for point, normal in zip(segment["points_mm"], segment["normals"]):
                    weld_line.add_point(True, Vector3D(point, "mm"), normal)
                if len(tuple(weld_line)) != len(segment["points_mm"]):
                    raise ValueError("{} 的 WeldLine 点数检查失败。".format(segment["label"]))
                first, last = segment["index"] == 1, segment["index"] == len(path["segments"])
                times = (state[5] if first else 0., state[6] if first else 0.,
                         state[7] if last else 0., state[8] if last else 0.)
                trajectory = robot.new_trajectory(weld_line, *(UnitValueTime(value, "s") for value in times))
                if trajectory is None:
                    raise ValueError("{} 的 Trajectory 创建失败。".format(segment["label"]))
                trajectory.name = name
                trajectory.heat_source_parameters = parameter
                actual = trajectory_timing_state(robot, trajectory)
                if (trajectory.name != name or trajectory.heat_source_parameters.name != name
                        or not math.isclose(actual[4], segment["length_mm"], abs_tol=1e-6)
                        or actual[5:] != times):
                    raise ValueError("{} 的轨迹长度、时间或热源分配检查失败。".format(segment["label"]))
                check_imported_parameter(trajectory.heat_source_parameters, segment)
                created.append(trajectory)
                print("已创建 {}：{}，长度 {:.6g} mm，曲线 {} 个点。".format(
                    segment["label"], name, segment["length_mm"], len(segment["curve"])), flush=True)
        for index, trajectory in enumerate(created):
            trajectory.index = index
        if ([trajectory.name for trajectory in robot.all_trajectories] != [trajectory.name for trajectory in created]
                or any(trajectory.index != index for index, trajectory in enumerate(created))):
            raise ValueError("Robot {} 的分段轨迹顺序检查失败。".format(robot.name))

    project.save()
    print("请在 {} 的工艺过程参数中手动取消自适应细化，保存后重新生成求解输入文件。".format(target_name), flush=True)
    if not target.write_program_input():
        raise RuntimeError("{} 的求解输入文件生成失败。".format(target_name))
    print("已保存 {} 并生成求解输入文件。".format(target_name))
    print("来源 DAT SHA256：{}".format(CONFIG["source_sha256"]))
    print("归一化基准：室温 {} °C，熔化温度 {} °C。".format(CONFIG["room_c"], CONFIG["melting_c"]))
    print(CONFIG["coverage_note"])
    print("热源 XML 文件保存在 {}。".format(import_directory))
    print("已创建 {} 条独立分段轨迹，每段采用无细分热循环。".format(len(segments)))
    print("求解前请确认已手动取消自适应细化，并重新生成求解输入文件。", flush=True)


if __name__ == "__main__":
    main()
