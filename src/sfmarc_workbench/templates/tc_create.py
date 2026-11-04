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
                or path["start_s"] < 0 or path["end_s"] <= path["start_s"]
                or type(path["segment_count"]) is not int or path["segment_count"] <= 0):
            raise ValueError("焊路 {} 的长度、速度、时间或分段数量无效。".format(path_id))
        curve = path["curve"]
        if (len(curve) < 2 or any(len(row) != 2 for row in curve)
                or any(not math.isfinite(value) for row in curve for value in row)
                or any(not 0 <= row[1] <= 2 for row in curve)
                or any(first[0] >= second[0] for first, second in zip(curve, curve[1:]))):
            raise ValueError("焊路 {} 的归一化曲线或时间顺序无效。".format(path_id))
    root = ET.fromstring(config["heat_source_xml"])
    if (root.tag != "HeatSources" or len(root.findall("WeldingParameters")) != 1
            or len(root.findall("WeldingParameters/velocity")) != 1):
        raise ValueError("热源参考 XML 缺少唯一的 WeldingParameters 或 velocity。")


def thermal_cycle_element(count, curve):
    element = ET.Element("thermalCycle")
    ET.SubElement(element, "fullCycle", enable="false")
    ET.SubElement(element, "count").text = str(count)
    ET.SubElement(element, "subDivisionMode").text = "constant"
    table = ET.SubElement(element, "xyTable", rowCount=str(len(curve)),
                          xUnit="seconds", yUnit="1/1")
    for index, (time_s, temperature) in enumerate(curve, 1):
        ET.SubElement(table, "row", nb=str(index)).text = "{:.15g};{:.15g}".format(time_s, temperature)
    return element


def write_import_files(config, output_directory):
    files = {}
    for path in config["paths"]:
        tree = ET.ElementTree(ET.fromstring(config["heat_source_xml"]))
        parameters = tree.getroot().find("WeldingParameters")
        parameters.find("velocity").text = "{:.15g}".format(path["speed_mm_s"] / 1000)
        for element in parameters.findall("thermalCycle"):
            parameters.remove(element)
        parameters.append(thermal_cycle_element(path["segment_count"], path["curve"]))
        filename = output_directory / "thermal_cycle_{}.xml".format(path["id"])
        with filename.open("xb") as stream:
            tree.write(stream, encoding="utf-8", xml_declaration=True)
        written = ET.parse(str(filename)).getroot().find("WeldingParameters/thermalCycle")
        if ET.tostring(written) != ET.tostring(parameters.find("thermalCycle")):
            raise ValueError("焊路 {} 的热源 XML 写入检查失败。".format(path["id"]))
        files[path["id"]] = filename
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
    return (trajectory_timing_state(robot, trajectory), parameter.name,
            float(parameter.velocity.value("mm/s")), parameter.specification.mode,
            parameter.heat_source.name if parameter.heat_source is not None else None)


def check_imported_parameter(parameter, path):
    specification = parameter.specification
    if specification.mode != "thermal-cycle" or specification.full_thermal_cycle:
        raise ValueError("导入热源必须为 Thermal Cycle，Full thermal cycle 必须关闭。")
    table = specification.thermal_cycle
    if len(table.x_columns) != 1 or len(table.y_columns) != 1:
        raise ValueError("导入热循环表格必须包含一个时间列和一个温度列。")
    time_column = table.x_columns[0]
    time_column.convert_to_unit("s")
    times, temperatures = time_column.data, table.y_columns[0].data
    curve = path["curve"]
    if len(times) != len(curve) or len(temperatures) != len(curve) or any(
            not math.isclose(float(value), expected, rel_tol=1e-7, abs_tol=1e-8)
            for actual, target in zip(zip(times, temperatures), curve)
            for value, expected in zip(actual, target)):
        raise ValueError("焊路 {} 的导入热循环曲线与配置不符。".format(path["id"]))


def main():
    validate_config(CONFIG)
    directory = Path(__file__).resolve().parent
    source_name, target_name = CONFIG["source_process"], CONFIG["target_process"]
    paths = CONFIG["paths"]

    from simufact.welding import current_project

    project = current_project()
    if project is None:
        raise RuntimeError("请在 Simufact Welding 2026.2 中打开目标项目。")
    if any(process.name == target_name for process in project.all_processes):
        raise ValueError("项目中已经存在 {}，已经终止创建。".format(target_name))
    source = project.process(source_name)
    if source is None:
        raise ValueError("项目中未找到 {}。".format(source_name))
    source_paths = matching_trajectories(source, paths)
    source_states = {path_id: trajectory_state(*pair) for path_id, pair in source_paths.items()}
    parameter_names = {parameter.name for parameter in project.all_heat_source_parameters}
    new_names = {path["id"]: "{}_{}".format(target_name, path["id"]) for path in paths}
    if parameter_names.intersection(new_names.values()):
        raise ValueError("项目中已经存在本次需要创建的热源参数名称。")
    work_directory = directory / ".work"
    work_directory.mkdir(exist_ok=True)
    import_directory = Path(tempfile.mkdtemp(prefix="thermal_cycle_", dir=str(work_directory)))
    import_files = write_import_files(CONFIG, import_directory)
    target = source.copy()
    target.name = target_name
    if target.name != target_name or source.name != source_name:
        raise ValueError("复制后的 Process 名称检查失败。")
    target_paths = matching_trajectories(target, paths)
    if {path_id: trajectory_state(*pair) for path_id, pair in target_paths.items()} != source_states:
        raise ValueError("复制后的轨迹参数与来源 Process 不一致，已经终止配置。")

    for path in paths:
        path_id = path["id"]
        parameter = project.import_heat_source_parameters_si(str(import_files[path_id]))
        if parameter is None or parameter.name in parameter_names:
            raise ValueError("没有获得独立的导入热源参数，已经终止配置。")
        parameter.name = new_names[path_id]
        if parameter.name != new_names[path_id]:
            raise ValueError("焊路 {} 的热源参数名称设置失败。".format(path_id))
        parameter_names.add(parameter.name)
        source_parameter = source_paths[path_id][1].heat_source_parameters
        parameter.heat_source = source_parameter.heat_source
        parameter.velocity = source_parameter.velocity
        if (float(parameter.velocity.value("mm/s")) != float(source_parameter.velocity.value("mm/s"))
                or parameter.heat_source.name != source_parameter.heat_source.name):
            raise ValueError("焊路 {} 的热源或速度继承检查失败。".format(path_id))
        check_imported_parameter(parameter, path)
        robot, trajectory = target_paths[path_id]
        trajectory.heat_source_parameters = parameter
        if trajectory.heat_source_parameters.name != parameter.name:
            raise ValueError("焊路 {} 的热源分配失败。".format(path_id))
        check_imported_parameter(trajectory.heat_source_parameters, path)
        if trajectory_timing_state(robot, trajectory) != source_states[path_id][0]:
            raise ValueError("焊路 {} 的轨迹或 Robot 时间发生变化。".format(path_id))
        # 本地 2026.2 API 文档未提供分段数量属性，求解采用数量需要核对 DAT。
        print("已分配 {}：{}，请求分段数量 {}，需要核对生成 DAT，曲线 {} 个点。".format(
            trajectory.name, parameter.name, path["segment_count"], len(path["curve"])), flush=True)

    if {path_id: trajectory_state(*pair) for path_id, pair in source_paths.items()} != source_states:
        raise ValueError("来源 Process 的轨迹参数发生变化，已经终止保存。")
    result = target.check_model()
    print(result)
    if not result.ok:
        raise ValueError("{} 的 Simufact 模型检查未通过，已经终止保存。".format(target_name))
    project.save()
    if not target.write_program_input():
        raise RuntimeError("{} 的求解输入文件生成失败。".format(target_name))
    print("已保存 {} 并生成求解输入文件。".format(target_name))
    print("来源 DAT SHA256：{}".format(CONFIG["source_sha256"]))
    print("归一化基准：室温 {} °C，熔化温度 {} °C。".format(CONFIG["room_c"], CONFIG["melting_c"]))
    print(CONFIG["coverage_note"])
    print("热源 XML 文件保存在 {}。".format(import_directory))
    print("请求分段总数 {}；求解采用数量需要核对生成 DAT。".format(
        sum(path["segment_count"] for path in paths)))


if __name__ == "__main__":
    main()
