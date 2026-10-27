SCRIPT_BODY = '''


def validate_plan():
    if not HEATS:
        raise ValueError("热源列表不能为空。")
    heat_fields = {"power_w", "velocity", "efficiency", "conical_upper_radius", "conical_lower_radius", "conical_depth",
                   "conical_gaussian_parameter", "volume_heat_fraction", "disc_radius", "surface_depth", "disc_gaussian_parameter"}
    for name, parameters in HEATS.items():
        if set(parameters) != heat_fields:
            raise ValueError("热源参数不完整或包含未知参数：{}".format(name))
        for key, value in parameters.items():
            zero_allowed = key in ("efficiency", "conical_lower_radius", "volume_heat_fraction", "conical_gaussian_parameter", "disc_gaussian_parameter")
            maximum = 1 if key in ("efficiency", "volume_heat_fraction") else 3 if key.endswith("gaussian_parameter") else float("inf")
            if not math.isfinite(value) or not 0 <= value <= maximum or (value == 0 and not zero_allowed):
                raise ValueError("热源参数无效：{} / {}".format(name, key))
    if not PATHS or len({path["name"] for path in PATHS}) != len(PATHS):
        raise ValueError("焊路列表不能为空，名称不能重复。")
    if len(ORIENTATION) != 3 or not all(math.isfinite(value) for value in ORIENTATION) or math.hypot(*ORIENTATION) == 0:
        raise ValueError("方向向量无效。")
    for path in PATHS:
        if path["heat_source"] not in HEATS:
            raise ValueError("焊路引用的热源不存在：{}".format(path["name"]))
        if not math.isfinite(path["pause_after"]) or path["pause_after"] < 0:
            raise ValueError("焊后停止时间必须为非负有限数值。")
        points = path["points"]
        if len(points) < 2 or any(len(point) != 3 or not all(math.isfinite(value) for value in point) for point in points):
            raise ValueError("路径点坐标无效：{}".format(path["name"]))
        distances = [math.sqrt(math.fsum((a-b)**2 for a, b in zip(first, second))) for first, second in zip(points, points[1:])]
        if any(length <= 0 or not math.isfinite(length) for length in distances):
            raise ValueError("相邻点距离必须大于零：{}".format(path["name"]))
        if not math.isfinite(path["length_mm"]) or abs(math.fsum(distances) - path["length_mm"]) > 1e-6:
            raise ValueError("路径坐标与长度不一致：{}".format(path["name"]))


def find_named(items, name, label, required=True):
    matches = [item for item in items if item.name == name]
    if len(matches) > 1:
        raise RuntimeError("存在多个同名 {}：{}".format(label, name))
    if not matches:
        if required:
            raise RuntimeError("未找到 {}：{}".format(label, name))
        return None
    return matches[0]


def main():
    from simufact import UnitValueTime, UnitValueLength, UnitValuePower, UnitValueSpeed, Vector3D
    from simufact.welding import HeatSourceLaser

    validate_plan()
    # current_project 由 Simufact Welding 的脚本环境提供。
    project = current_project()
    if project is None:
        raise RuntimeError("Simufact Welding 当前没有打开 Project。")
    process = find_named(project.all_processes, PROCESS_NAME, "Process")
    robot = find_named(process.all_robots, ROBOT_NAME, "Robot", required=False)
    if robot is not None and tuple(robot.all_trajectories):
        raise RuntimeError("目标 Robot 已有 Trajectory，请使用空 Robot 或新名称。")
    existing_heat_names = {parameter.name for parameter in project.all_heat_source_parameters}
    heat_conflicts = existing_heat_names & set(HEATS)
    if heat_conflicts:
        raise RuntimeError("热源名称已存在，请设置新的热源名称：{}".format(", ".join(sorted(heat_conflicts))))
    conflicts = {item.name for item in project.all_weld_lines} & {path["name"] for path in PATHS}
    if conflicts:
        raise RuntimeError("Project 已有同名 WeldLine：{}".format(", ".join(sorted(conflicts))))
    lengths = ("conical_upper_radius", "conical_lower_radius", "conical_depth", "disc_radius", "surface_depth")
    scalars = ("volume_heat_fraction", "conical_gaussian_parameter", "disc_gaussian_parameter")
    heat_sources = {}
    for name, parameters in HEATS.items():
        heat_source = project.new_heat_source_parameters(name)
        if heat_source is None or heat_source.name != name:
            raise RuntimeError("创建热源失败：{}".format(name))
        heat_source.velocity = UnitValueSpeed(parameters["velocity"], "mm/s")
        specification = heat_source.specification
        specification.mode = "transient-direct"
        specification.power = UnitValuePower(parameters["power_w"], "W")
        specification.efficiency = parameters["efficiency"]
        heat_source.specification = specification
        geometry = {key: UnitValueLength(parameters[key], "mm") for key in lengths}
        geometry.update({key: parameters[key] for key in scalars})
        heat_source.heat_source = HeatSourceLaser(**geometry)
        actual = heat_source.specification
        checks = [(float(heat_source.velocity.value("mm/s")), parameters["velocity"]),
                  (float(actual.power.value("W")), parameters["power_w"]), (float(actual.efficiency), parameters["efficiency"])]
        checks.extend((float(getattr(heat_source.heat_source, key).value("mm")), parameters[key]) for key in lengths)
        checks.extend((float(getattr(heat_source.heat_source, key)), parameters[key]) for key in scalars)
        if actual.mode != "transient-direct" or any(not math.isfinite(value) or abs(value - expected) > max(1e-8, abs(expected)*1e-8) for value, expected in checks):
            raise RuntimeError("热源参数读取结果与设置不一致：{}".format(name))
        heat_sources[name] = heat_source
    if robot is None:
        robot = process.new_robot(ROBOT_NAME)
        if robot is None or robot.name != ROBOT_NAME:
            raise RuntimeError("创建 Robot 失败：{}".format(ROBOT_NAME))
    trajectories = []
    for path in PATHS:
        heat_source = heat_sources[path["heat_source"]]
        weld_line = project.new_weld_line(path["name"], "local-vector")
        if weld_line is None or weld_line.name != path["name"]:
            raise RuntimeError("创建 WeldLine 失败：{}".format(path["name"]))
        for point in path["points"]:
            weld_line.add_point(True, Vector3D(point, "mm"), ORIENTATION)
        if len(tuple(weld_line)) != len(path["points"]):
            raise RuntimeError("WeldLine 点数与计划不符：{}".format(path["name"]))
        # 时间参数依次为 PauseStart、LeadTime、FollowUpTime、PauseEnd。
        trajectory = robot.new_trajectory(weld_line, UnitValueTime(0, "s"), UnitValueTime(0, "s"),
                                          UnitValueTime(0, "s"), UnitValueTime(path["pause_after"], "s"))
        if trajectory is None:
            raise RuntimeError("创建 Trajectory 失败：{}".format(path["name"]))
        trajectory.name = path["name"]
        trajectory.heat_source_parameters = heat_source
        actual_length = float(trajectory.original_length.value("mm"))
        if not math.isfinite(actual_length) or abs(actual_length - path["length_mm"]) > 1e-6:
            raise RuntimeError("Trajectory 长度与计划不符：{}".format(path["name"]))
        if trajectory.name != path["name"] or trajectory.heat_source_parameters.name != heat_source.name:
            raise RuntimeError("Trajectory 名称或热源分配与计划不符。")
        trajectories.append(trajectory)
    # Robot 的执行顺序由 Trajectory.index 决定。
    for index, trajectory in enumerate(trajectories):
        trajectory.index = index
    if [item.name for item in robot.all_trajectories] != [path["name"] for path in PATHS]:
        raise RuntimeError("Robot 的执行顺序与计划不符。")
    for index, trajectory in enumerate(trajectories):
        if trajectory.index != index:
            raise RuntimeError("Trajectory.index 与计划不符。")
        print("顺序 {}：{}，焊后停止 {} s。".format(index + 1, trajectory.name, PATHS[index]["pause_after"]))
    print("已创建 {} 条焊路，请在 Simufact Welding 中保存 Project。".format(len(PATHS)))


if __name__ == "__main__":
    main()
'''
