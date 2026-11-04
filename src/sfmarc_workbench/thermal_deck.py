from pathlib import PurePosixPath
import re

from fortranformat import FortranRecordReader
from meshioplusplus.marc import _marc
import numpy as np

from .deck import Deck


def read_weld_paths(deck: Deck, length_unit: str) -> dict:
    if length_unit not in ("m", "mm"):
        raise ValueError("长度单位需要为 m 或 mm")
    scale = 1000.0 if length_unit == "m" else 1.0
    text = [line.text for line in deck.lines]
    iw, rw = deck.iw, 20 if deck.extended else 10

    def key(line):
        return " ".join(line.strip().lower().split())

    def record(index, fmt):
        index = deck.next_data(index)
        if "," in text[index]:
            raise ValueError(f"第 {index + 1} 行使用了尚未支持的自由字段记录")
        values = FortranRecordReader(fmt).read(text[index])
        if any(value is None or (isinstance(value, (int, float)) and not np.isfinite(value))
               for value in values):
            raise ValueError(f"第 {index + 1} 行包含缺失或非有限数值")
        return values, index + 1

    def require(condition, message):
        if not condition:
            raise ValueError(message)

    def single_keyword(name):
        indices = [i for i, value in enumerate(text[:deck.end_option]) if key(value) == name]
        require(len(indices) == 1, f"需要且只能包含一个 {name.upper()}")
        return indices[0]

    def coordinates(cursor, count):
        points = []
        for _ in range(count):
            values, cursor = record(cursor, f"3E{rw}.0")
            points.append(values)
        return np.asarray(points, dtype=float), cursor

    paths = {}
    header, cursor = record(single_keyword("weld path") + 1, f"2I{iw}")
    require(header[0] > 0 and header[1] == 0, "WELD PATH 总记录形式尚未支持")
    for _ in range(header[0]):
        values, cursor = record(cursor, f"6I{iw},A")
        ident = values[0]
        require(ident > 0 and ident not in paths, "WELD PATH 编号重复或无效")
        require(values[1:6] == [2, 2, 1, 0, 1] and values[6].strip(),
                f"焊路 {ident} 的路径定义形式尚未支持")
        counts, cursor = record(cursor, f"2I{iw}")
        require(counts[0] == 1 and counts[1] >= 2, f"焊路 {ident} 的坐标记录无效")
        points, cursor = coordinates(cursor, counts[1])
        orientation, cursor = record(cursor, f"I{iw},E{rw}.0,I{iw}")
        require(orientation == [1, 0.0, 0], f"焊路 {ident} 的方向定义形式尚未支持")
        targets, cursor = coordinates(cursor, counts[1])
        directions = points - targets
        sizes = np.linalg.norm(directions, axis=1)
        lengths = np.linalg.norm(np.diff(points, axis=0), axis=1) * scale
        require(np.isfinite(sizes).all() and np.isfinite(lengths).all()
                and np.isfinite(points * scale).all() and np.isfinite(lengths.sum())
                and np.all(sizes > 0) and np.all(lengths > 0),
                f"焊路 {ident} 包含无效路径或方向")
        paths[ident] = {"id": ident, "points_mm": (points * scale).tolist(),
                        "normals": (directions / sizes[:, None]).tolist(),
                        "length_mm": float(lengths.sum())}

    fluxes = {}
    names = set()
    header, cursor = record(single_keyword("weld flux") + 1, f"4I{iw}")
    require(header[0] > 0 and header[1:] == [0, 1, 1], "WELD FLUX 总记录形式尚未支持")
    for _ in range(header[0]):
        values, cursor = record(cursor, f"6I{iw},A")
        _, ident, kind, path_id, _, _, name = values
        name = name.strip()
        require(ident > 0 and ident not in fluxes and name and name not in names,
                "WELD FLUX 编号或名称重复或无效")
        require(values[0] == 1 and values[4:6] == [0, 0] and kind in (5, 7),
                f"热源 {ident} 的定义形式尚未支持")
        require(path_id in paths, f"热源 {ident} 引用了缺失的焊路 {path_id}")
        motion, cursor = record(cursor, f"6E{rw}.0,I{iw},E{rw}.0")
        require(motion[:5] == [0.0] * 5 and motion[5] > 0 and motion[6] > 0
                and motion[7] == 1.2, f"热源 {ident} 的运动定义形式尚未支持")
        shape, cursor = record(cursor, f"8E{rw}.0")
        require(shape[0] > 0 and shape[1] > 0, f"热源 {ident} 的功率定义无效")
        tables, cursor = record(cursor, f"8I{iw}")
        require(tables[0] >= 0 and tables[1:] == [0] * 7,
                f"热源 {ident} 的功率表定义形式尚未支持")
        selection, cursor = record(cursor, f"2I{iw}")
        selection_kind, cursor = record(cursor, f"I{iw}")
        require(selection == [0, 10] and selection_kind[0] == (14 if kind == 7 else 1),
                f"热源 {ident} 的作用集合形式尚未支持")
        target_index = deck.next_data(cursor)
        require(re.fullmatch(r"[\w.\-]+", text[target_index].strip()) is not None,
                f"热源 {ident} 的作用集合名称无效")
        cursor = target_index + 1
        fluxes[ident] = {"name": name, "path": path_id, "kind": kind,
                         "speed": motion[5] * scale, "velocity": motion[6],
                         "power": tables[0]}
        names.add(name)

    model_start = next((i for i, value in enumerate(text) if key(value) == "end"), -1)
    require(model_start >= 0, "没有找到 Marc 参数段结束记录")
    table_indices = {}
    for index in range(model_start + 1, deck.end_option):
        if not re.match(r"^table(?:\s|$)", text[index], re.I):
            continue
        values, _ = record(index + 1, f"I{iw}")
        ident = values[0]
        require(ident > 0 and ident not in table_indices, "TABLE 编号重复或无效")
        table_indices[ident] = index
    tables = {}
    for ident in {flux[k] for flux in fluxes.values() for k in ("velocity", "power") if flux[k] > 0}:
        require(ident in table_indices, f"没有找到热源使用的 TABLE {ident}")
        header, cursor = record(table_indices[ident] + 1, f"5I{iw}")
        require(header == [ident, 1, 0, 0, 2], f"TABLE {ident} 的维度或定义形式尚未支持")
        flags, cursor = record(cursor, f"12I{iw}")
        require(flags[1] >= 2 and flags[:1] + flags[2:] == [1, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1],
                f"TABLE {ident} 的插值或外延形式尚未支持")
        points = []
        for _ in range(flags[1]):
            values, cursor = record(cursor, f"2E{rw}.0")
            points.append(values)
        points = np.asarray(points, dtype=float)
        require(points[0, 0] >= 0 and np.all(np.diff(points[:, 0]) > 0)
                and np.all(points[:, 1] >= 0), f"TABLE {ident} 的时间或系数无效")
        tables[ident] = points

    for flux in fluxes.values():
        velocity = tables[flux["velocity"]]
        require(np.allclose(velocity[:, 1], velocity[0, 1], rtol=1e-12, atol=1e-12)
                and velocity[0, 1] > 0, "当前转换仅支持恒定焊接速度")
        if flux["power"]:
            power = tables[flux["power"]]
            require(np.allclose(velocity[[0, -1], 0], power[[0, -1], 0], rtol=0, atol=1e-9),
                    "热源速度表与功率表的有效时间区间不一致")
        flux["speed"] *= float(velocity[0, 1])
        require(np.isfinite(flux["speed"]) and flux["speed"] > 0, "热源速度无效")
        flux["interval"] = velocity[[0, -1], 0]

    flux_by_name = {flux["name"]: flux for flux in fluxes.values()}
    referenced = set()
    active_paths = set()
    current_time = 0.0
    for case_index, case in enumerate(deck.cases):
        if case.initial:
            continue
        stop = deck.cases[case_index + 1].header if case_index + 1 < len(deck.cases) else len(text)
        active = [flux_by_name[text[i].strip()] for i in case.label_lines if text[i].strip() in flux_by_name]
        step_indices = [i for i in range(case.header + 1, stop) if key(text[i]) == "auto step"]
        if not step_indices:
            require(not active and any(key(text[i]).startswith("approach ")
                                       for i in range(case.header + 1, stop)),
                    f"阶段 {case.name} 缺少可确定时间的 AUTO STEP")
            continue
        require(len(step_indices) == 1, f"阶段 {case.name} 包含重复 AUTO STEP")
        values, _ = record(step_indices[0] + 1, f"2E{rw}.0")
        duration = values[1]
        require(duration > 0, f"阶段 {case.name} 的持续时间无效")
        end_time = current_time + duration
        require(np.isfinite(end_time), f"阶段 {case.name} 的累计时间无效")
        ids = {flux["path"] for flux in active}
        require(len(ids) <= 1, f"阶段 {case.name} 同时激活多条焊路，当前转换尚未支持")
        if active:
            ident = next(iter(ids))
            require(ident not in active_paths, f"焊路 {ident} 被多个阶段重复激活")
            require(len({flux["kind"] for flux in active}) == len(active),
                    f"焊路 {ident} 包含重复类型的热源")
            first = active[0]
            for flux in active:
                require(np.allclose(flux["interval"], [current_time, end_time], rtol=0, atol=1e-9),
                        f"阶段 {case.name} 的时间与热源表有效区间不一致")
                require(np.isclose(flux["speed"], first["speed"], rtol=1e-12, atol=1e-12),
                        f"焊路 {ident} 的热源速度不一致")
                require(flux["name"] not in referenced, "热源被多个阶段重复引用")
                referenced.add(flux["name"])
            path = paths[ident]
            # DAT 阶段时间与折线路径坐标分别保留，允许时间输出精度造成的微小差异。
            require(abs(path["length_mm"] / first["speed"] - duration) <= max(1e-4, duration * 1e-4),
                    f"焊路 {ident} 的长度、速度与持续时间不一致")
            path.update(speed_mm_s=float(first["speed"]), start_s=current_time, end_s=end_time)
            active_paths.add(ident)
        current_time = end_time
    require(active_paths == set(paths) and referenced == names, "存在没有对应执行阶段的焊路或热源")
    ordered = sorted(paths.values(), key=lambda path: path["start_s"])
    for index, path in enumerate(ordered):
        path["cooling_end_s"] = ordered[index + 1]["start_s"] if index + 1 < len(ordered) else current_time

    room_c = _initial_room_temperature(deck, text, record, iw, rw)
    titles = [value[5:].strip() for value in text[:model_start] if re.match(r"^title\s", value, re.I)]
    return {"source_process": titles[0] if titles and titles[0] else PurePosixPath(deck.root).stem,
            "paths": ordered, "end_s": current_time, "room_c": room_c}


def _initial_room_temperature(deck, text, record, iw, rw):
    unit_lines = [i for i, value in enumerate(text[:deck.end_option]) if re.match(r"^unit\s", value, re.I)]
    if len(unit_lines) != 1 or FortranRecordReader(f"A{deck.kw},2I{iw}").read(text[unit_lines[0]])[1:] != [1, 1]:
        return None
    indices = [i for i, value in enumerate(text[:deck.end_option]) if value.strip().lower() == "initial temp"]
    if len(indices) != 1:
        return None
    names = {text[i].strip() for case in deck.cases if case.initial for i in case.label_lines}
    known = {}
    for name, family, tokens, _ in deck.mesh.sets:
        if family == "node":
            known[name.lower()] = _marc._expand(tokens, name, known, "INITIAL TEMP")
    count, cursor = record(indices[0] + 1, f"I{iw}")
    covered, values = set(), []
    for _ in range(count[0]):
        header, cursor = record(cursor, f"6I{iw},A")
        if header[:6] != [1, 0, 0, 0, 0, 0]:
            return None
        temperature, cursor = record(cursor, f"E{rw}.0")
        table, cursor = record(cursor, f"I{iw}")
        selection, cursor = record(cursor, f"I{iw}")
        index = deck.next_data(cursor)
        target, cursor = text[index].strip().lower(), index + 1
        if table != [0] or selection != [2] or target not in known:
            return None
        if header[6].strip() in names:
            values.append(temperature[0])
            covered.update(known[target])
    required = {node for _, _, nodes in deck.mesh.elements for node in nodes}
    if not values or not required.issubset(covered) or not np.allclose(values, values[0], rtol=0, atol=1e-9):
        return None
    return float(values[0] - 273.15)
