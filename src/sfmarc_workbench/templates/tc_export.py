import csv
import math
from pathlib import Path


CSV_HEADER = ("Increment", "Time_s", "Component", "SolverNodeID", "NodeID", "Temperature_C", "ModelSHA256")


def validate_config(config):
    name = config["source_process"]
    reserved = {"CON", "PRN", "AUX", "NUL"}
    reserved.update("COM{}".format(index) for index in range(1, 10))
    reserved.update("LPT{}".format(index) for index in range(1, 10))
    if (not isinstance(name, str) or not name or name != name.strip()
            or name.endswith(".") or name in {".", ".."}
            or any(ord(character) < 32 or character in '<>:"/\\|?*' for character in name)
            or name.split(".", 1)[0].upper() in reserved):
        raise ValueError("来源 Process 名称无法安全用于 CSV 文件名。")
    digest = config["source_sha256"]
    if (not isinstance(digest, str) or len(digest) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in digest)):
        raise ValueError("来源 DAT 的 SHA256 无效。")
    groups = config["groups"]
    if not groups:
        raise ValueError("没有需要导出的节点组。")
    names = set()
    for group in groups:
        group_name, count, node_ids = group["name"], group["node_count"], group["node_ids"]
        if not isinstance(group_name, str) or not group_name.strip() or group_name in names:
            raise ValueError("节点组名称为空或重复。")
        names.add(group_name)
        if (type(count) is not int or count <= 0 or not node_ids or len(node_ids) > count
                or any(type(node_id) is not int or node_id <= 0 for node_id in node_ids)
                or len(set(node_ids)) != len(node_ids)):
            raise ValueError("节点组 {} 的节点总数或节点编号无效。".format(group_name))


def build_increment_times(increments, times):
    if not increments or len(increments) != len(times):
        raise ValueError("History 中的增量编号与时间数据为空或长度不同。")
    increment_times = {}
    for number, time_s in zip(increments, times):
        if not math.isfinite(number) or number < 0 or int(number) != number:
            raise ValueError("History 中存在无效增量编号: {}".format(number))
        if not math.isfinite(time_s) or time_s < 0 or number in increment_times:
            raise ValueError("History 中存在无效时间或重复增量编号: {}".format(number))
        increment_times[int(number)] = time_s
    return increment_times


def select_temperatures(node_values, node_ids):
    selected = {}
    requested = set(node_ids)
    for node_id, temperature_k in node_values:
        if node_id in requested:
            if node_id in selected:
                raise ValueError("温度结果中存在重复节点编号: {}".format(node_id))
            if not math.isfinite(temperature_k) or temperature_k < 0:
                raise ValueError("节点 {} 的温度无效: {} K".format(node_id, temperature_k))
            # Mesh.node_results() 默认返回 SI 单位，温度为 Kelvin。
            selected[node_id] = temperature_k - 273.15
    missing = requested - selected.keys()
    if missing:
        raise ValueError("当前增量缺少节点温度: {}".format(sorted(missing)))
    return [selected[node_id] for node_id in node_ids]


def temperature_rows(process, source_groups):
    results = process.results
    reference = next(iter(results))
    meshes = []
    for component in process.all_components:
        mesh = reference.mesh(component.name)
        meshes.append((component.name, mesh.number_nodes, {node.id for node in mesh.nodes}))
    groups = []
    selected_components = set()
    for group in source_groups:
        node_ids = tuple(group["node_ids"])
        requested = set(node_ids)
        candidates = [name for name, count, available in meshes
                      if count == group["node_count"] and requested.issubset(available)]
        if len(candidates) != 1:
            raise ValueError("{} 对应的结果网格组件数量为 {}，无法唯一确认组件。".format(
                group["name"], len(candidates)))
        component_name = candidates[0]
        if component_name in selected_components:
            raise ValueError("多个来源节点组匹配到同一结果组件 {}。".format(component_name))
        selected_components.add(component_name)
        groups.append((component_name, node_ids))
        print("组件 {}：确认 {} 个目标节点。".format(component_name, len(node_ids)), flush=True)
    history = results.history(groups[0][0], result_values=["CYCRAT", "TIME"])
    time_column = history.column("TIME")
    time_column.convert_to_unit("s")
    increment_times = build_increment_times(history.column("CYCRAT").data, time_column.data)
    seen_increments = set()
    for index, increment in enumerate(results):
        number = increment.number
        if number not in increment_times or number in seen_increments:
            raise ValueError("结果增量 {} 在 History 中缺失或重复。".format(number))
        seen_increments.add(number)
        if index % 50 == 0:
            print("读取结果 {}/{}，增量 {}".format(index + 1, len(results), number), flush=True)
        for component_name, node_ids in groups:
            mesh = increment.mesh(component_name)
            values = ((result.id, result.value) for result in mesh.node_results("TEMPTURE"))
            temperatures = select_temperatures(values, node_ids)
            for node_id, temperature_c in zip(node_ids, temperatures):
                yield number, increment_times[number], component_name, node_id, node_id, temperature_c


def write_temperature_csv(output_file, rows, expected_rows, source_sha256):
    output_file = Path(output_file)
    partial_file = output_file.with_name(output_file.name + ".partial")
    if expected_rows <= 0:
        raise ValueError("没有可导出的结果。")
    if output_file.exists() or partial_file.exists():
        raise FileExistsError("输出文件或未完成文件已存在: {}".format(output_file))
    count = 0
    # 全部增量导出并读取检查完成后生成最终 CSV；异常时保留 .partial 文件。
    with partial_file.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(CSV_HEADER)
        for row in rows:
            if len(row) != 6:
                raise ValueError("温度数据字段数量无效。")
            writer.writerow(tuple(row) + (source_sha256 if count == 0 else "",))
            count += 1
    if count != expected_rows:
        raise ValueError("导出行数不符合预期: {}，应为 {}。".format(count, expected_rows))
    with partial_file.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        if tuple(next(reader)) != CSV_HEADER:
            raise ValueError("CSV 的字段检查失败。")
        checked_rows = 0
        for row in reader:
            if (len(row) != len(CSV_HEADER)
                    or row[-1] != (source_sha256 if checked_rows == 0 else "")):
                raise ValueError("CSV 的数据字段数量或来源标识检查失败。")
            checked_rows += 1
        if checked_rows != expected_rows:
            raise ValueError("CSV 的数据行数检查失败。")
    if output_file.exists():
        raise FileExistsError("输出文件已存在: {}".format(output_file))
    partial_file.rename(output_file)
    return count


def main():
    validate_config(CONFIG)
    directory = Path(__file__).resolve().parent
    process_name = CONFIG["source_process"]
    output_file = directory / (process_name + "_node_temperatures.csv")
    if output_file.exists() or output_file.with_name(output_file.name + ".partial").exists():
        raise FileExistsError("输出文件或未完成文件已存在: {}".format(output_file))

    from simufact.welding import current_project

    project = current_project()
    if project is None:
        raise RuntimeError("请在 Simufact Welding 中打开目标项目。")
    process = project.process(process_name)
    if process is None:
        raise ValueError("未找到 Process: {}".format(process_name))
    increment_count = len(process.results)
    node_count = sum(len(group["node_ids"]) for group in CONFIG["groups"])
    if increment_count <= 0:
        raise ValueError("来源 Process 没有已保存的结果增量。")
    print("来源 DAT SHA256：{}".format(CONFIG["source_sha256"]), flush=True)
    print("导出 {} 个已保存增量、{} 个节点。".format(increment_count, node_count), flush=True)
    count = write_temperature_csv(output_file, temperature_rows(process, CONFIG["groups"]),
                                  increment_count * node_count, CONFIG["source_sha256"])
    print("导出完成：{} 行，文件 {}".format(count, output_file))


if __name__ == "__main__":
    main()
