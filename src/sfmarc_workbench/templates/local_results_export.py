import csv
from datetime import datetime
import json
import math
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED


RESULT_VALUES = ["TEMPTURE", "TOTDISP", "PS", "TSTRN", "ESTRN", "TMST"]


def select_checkpoints(time_by_number, events, final_time_s):
    if not time_by_number or not events or not math.isfinite(final_time_s):
        raise ValueError("没有完整的结果时刻或焊接事件。")
    times = sorted(time_by_number.items(), key=lambda row: (row[1], row[0]))
    events = sorted(events, key=lambda row: row['start_s'])
    for event in events:
        if not (0 <= event['start_s'] < event['end_s'] <= final_time_s + 1e-6):
            raise ValueError("焊接事件时间无效或结果不完整。")
    if any(first['end_s'] > second['start_s'] + 1e-6 for first, second in zip(events, events[1:])):
        raise ValueError("局部焊接事件存在并发，不能提取独立的逐次冷却状态。")
    if times[-1][1] < final_time_s - 1e-6:
        raise ValueError("结果不完整，尚未到达指定最终冷却时刻。")
    initial = [row for row in times if row[1] <= events[0]['start_s'] + 1e-8]
    if not initial:
        raise ValueError("没有第一次焊接之前的初始状态。")
    number, time = initial[0]
    selected = [{'kind': 'initial', 'increment': number, 'time_s': time}]
    for index, event in enumerate(events):
        limit = events[index + 1]['start_s'] if index + 1 < len(events) else final_time_s
        candidates = [row for row in times if event['end_s'] - 1e-8 <= row[1] <= limit + 1e-6]
        if not candidates:
            raise ValueError("焊接 {} 没有已保存的冷却结果。".format(event['name']))
        number, time = candidates[-1]
        selected.append({'kind': 'cooled', 'pass': index + 1, 'event': event['name'],
                         'increment': number, 'time_s': time, 'cooling_s': max(0., time - event['end_s'])})
    return selected


def process_events(process):
    events = []
    for robot in process.all_robots:
        cursor = float(robot.start_time.value('s'))
        for trajectory in sorted(robot.all_trajectories, key=lambda value: value.index):
            start = cursor + float(trajectory.pause_time_start.value('s')) + float(trajectory.lead_time.value('s'))
            end = start + float(trajectory.welding_time.value('s'))
            events.append({'name': trajectory.name, 'robot': robot.name, 'start_s': start, 'end_s': end})
            cursor = end + float(trajectory.follow_up_time.value('s')) + float(trajectory.pause_time_end.value('s'))
    if not events:
        raise ValueError('局部算例没有焊接轨迹。')
    return sorted(events, key=lambda row: row['start_s'])


def increment_times(numbers, times):
    if len(numbers) != len(times) or not numbers:
        raise ValueError("结果时间与增量编号为空或数量不一致。")
    result = {}
    for number, time in zip(numbers, times):
        if not math.isfinite(number) or int(number) != number or number < 0:
            raise ValueError("结果增量编号无效：{}".format(number))
        if number in result or not math.isfinite(time) or time < 0:
            raise ValueError("结果时间无效或增量编号重复：{}".format(number))
        result[int(number)] = float(time)
    return result


def checked_files(directory, suffix):
    files = sorted(Path(directory).rglob("*" + suffix))
    if not files or any(path.stat().st_size == 0 for path in files):
        raise ValueError("导出目录没有完整的 {} 文件：{}".format(suffix, directory))
    return files


def pack_results(directory):
    directory = Path(directory)
    target = directory.with_suffix(".zip")
    partial = directory.with_suffix(".zip.partial")
    if target.exists() or partial.exists():
        raise FileExistsError("结果包已存在：{}".format(target))
    files = sorted(path for path in directory.rglob("*") if path.is_file())
    if not files:
        raise ValueError("结果目录为空。")
    with ZipFile(partial, "x", ZIP_DEFLATED, allowZip64=True) as archive:
        for path in files:
            archive.write(path, path.relative_to(directory).as_posix())
    with ZipFile(partial) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise ValueError("结果包校验失败：{}".format(bad))
    partial.rename(target)
    return target


def export_results(process, directory):
    results = process.results
    if len(results) == 0:
        raise ValueError("所选算例没有已保存的计算结果。")
    components = [component.name for component in process.all_components]
    if not components:
        raise ValueError("所选算例没有组件。")
    history = results.history(components[0], result_values=["CYCRAT", "TIME"])
    times = history.column("TIME")
    times.convert_to_unit("s")
    time_by_number = increment_times(history.column("CYCRAT").data, times.data)
    numbers = [increment.number for increment in results]
    if len(set(numbers)) != len(numbers) or any(number not in time_by_number for number in numbers):
        raise ValueError("已保存结果与 History 增量编号无法对应。")
    saved_times = {number: time_by_number[number] for number in numbers}
    events = process_events(process)
    settings = globals().get('EXPORT_SETTINGS', {})
    requested_end = settings.get('final_time_s')
    final_time = max(saved_times.values()) if requested_end is None else float(requested_end)
    checkpoints = select_checkpoints(saved_times, events, final_time)
    numbers = list(dict.fromkeys(row['increment'] for row in checkpoints))
    directory = Path(directory)
    directory.mkdir(exist_ok=False)
    manifest = {"schema": "simuweld-local-results-2", "process": process.name,
                "requested_results": RESULT_VALUES, "unit_system": "si",
                "element_values_at": "integration-points", "undeformed_mesh": True,
                "geometries": "all-deformable", "process_components": components,
                "increments": [], "robots": [], "events": events, "checkpoints": checkpoints,
                "cooling_limit_c": settings.get('cooling_limit_c', 26.),
                "final_checkpoint_requires_temperature_check": True,
                "validation": "nonempty_files_and_zip_crc; result_fields_not_read_back"}
    with (directory / "increments.csv").open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Increment", "Time_s", "Directory"])
        for index, number in enumerate(numbers):
            folder = directory / "increments" / "{:08d}".format(number)
            folder.mkdir(parents=True)
            print("导出结果 {}/{}，增量 {}，时间 {} s".format(
                index + 1, len(numbers), number, time_by_number[number]), flush=True)
            # 官方导出器保留张量及积分点位置，按增量保存自适应网格。
            results.export_to_unv(str(folder), geometries="all-deformable",
                                  result_values=RESULT_VALUES, increments=[number], unit_system="si",
                                  element_values_at="integration-points", degenerated_hexas_as="hexas",
                                  names="display", undeformed_mesh=True, single_file=False)
            files = checked_files(folder, ".unv")
            relative = folder.relative_to(directory).as_posix()
            writer.writerow([number, time_by_number[number], relative])
            manifest["increments"].append({"number": number, "time_s": time_by_number[number],
                "files": [path.relative_to(directory).as_posix() for path in files]})
    for index, robot in enumerate(process.all_robots):
        names = [trajectory.name for trajectory in robot.all_trajectories]
        if not names:
            continue
        folder = directory / "robots" / "{:04d}".format(index + 1)
        folder.mkdir(parents=True)
        robot.export_trajectories_list(str(folder / "sequence.csv"))
        robot.export_trajectories(str(folder / "paths"), names, align=False)
        checked_files(folder, ".csv")
        manifest["robots"].append({"name": robot.name, "directory": folder.relative_to(directory).as_posix(),
                                   "trajectories": names, "start_time": str(robot.start_time)})
    increment_by_number = {increment.number: increment for increment in results}
    for checkpoint in manifest['checkpoints']:
        temperatures = {}
        for name in components:
            values = [float(item.value) - 273.15 for item in increment_by_number[checkpoint['increment']].mesh(name).node_results('TEMPTURE', unit='si')]
            if not values or any(not math.isfinite(value) for value in values):
                raise ValueError('结果温度缺失或含非有限数值：' + name)
            temperatures[name] = max(values)
        checkpoint['temperature_max_c'] = temperatures
    manifest['final_cooling_complete'] = max(manifest['checkpoints'][-1]['temperature_max_c'].values()) <= manifest['cooling_limit_c']
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "说明.txt").write_text(
        "结果由 Simufact 官方 UNV 导出器生成。单位为 SI，时间表使用秒。\n"
        "各选定状态保存未变形网格、温度、位移、塑性/总/弹性/热应变；单元值位于积分点。\n"
        "仅包含求解器已保存的增量和结果字段。未计算及未保存的数据无法补充。\n"
        "当前脚本检查输出文件非空与 ZIP 完整性，尚未读取验证 UNV 内各结果字段。\n"
        "请同时保留此次求解 DAT、原始结果及 SD 导出的局部试算 ZIP。\n", encoding="utf-8")
    return pack_results(directory)


def main():
    from simufact.welding import current_project

    project = current_project()
    if project is None:
        raise RuntimeError("请打开局部计算项目。")
    processes = list(project.all_processes)
    selected = [process for process in processes if process.name == PROCESS_NAME] if PROCESS_NAME else processes
    if len(selected) != 1:
        raise ValueError("请在 SD 的局部算例名称中填写准确名称后重新导出脚本。当前项目算例：{}".format(
            "、".join(process.name for process in processes)))
    process = selected[0]
    print("读取局部算例：{}".format(process.name), flush=True)
    directory = Path(__file__).resolve().parent / ("local_results_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:8])
    output = export_results(process, directory)
    print("结果文件已导出：{}。请保留原始求解结果供数据核对。".format(output), flush=True)


if __name__ == "__main__":
    main()
