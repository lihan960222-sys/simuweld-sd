import ast
import csv
import math
import json
import sys
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryFile
import xml.etree.ElementTree as ET

from meshioplusplus.marc import _marc
import numpy as np

from .geometry import SurfaceModel
from .thermal_deck import read_weld_paths
from .sampling import sampling_parameters, select_nodes


def simplify_curve(times, values, tolerance):
    peak = int(np.argmax(values))
    keep = {0, peak, len(times) - 1}
    boundaries = sorted(keep)
    pending = list(zip(boundaries[:-1], boundaries[1:]))
    while pending:
        first, last = pending.pop()
        if last - first <= 1:
            continue
        error = np.abs(np.interp(times[first:last + 1], times[[first, last]], values[[first, last]])
                       - values[first:last + 1])
        if error.max() > tolerance:
            split = first + int(np.argmax(error))
            keep.add(split)
            pending.extend(((first, split), (split, last)))
    indices = np.array(sorted(keep))
    error = float(np.abs(np.interp(times, times[indices], values[indices]) - values).max())
    if error > tolerance + 1e-8 or peak not in keep:
        raise ValueError("温度曲线简化检查未通过")
    return indices, error


def aligned_average(times, values, arrivals, window=None):
    times, values, arrivals = np.asarray(times), np.asarray(values), np.asarray(arrivals)
    if (times.ndim != 1 or arrivals.ndim != 1 or not len(arrivals) or len(times) < 3
            or values.shape != (len(times), len(arrivals))
            or not np.isfinite(times).all() or not np.isfinite(values).all()
            or not np.isfinite(arrivals).all() or np.any(np.diff(times) <= 0)):
        raise ValueError("节点温度、经过时刻或时间顺序无效")
    first, last = float(np.max(times[0] - arrivals)), float(np.min(times[-1] - arrivals))
    if window is not None:
        first, last = max(first, window[0]), min(last, window[1])
    if last <= first:
        raise ValueError("节点温度没有共同的有效相对时间范围，无法计算分段平均曲线")
    # 累加线性插值的斜率变化，保留全部折点，共同范围之外不进行外推。
    knots, changes = [np.asarray([first, last])], [np.zeros(2)]
    initial, slope = 0., 0.
    for index, arrival in enumerate(arrivals):
        temperatures = values[:, index]
        slopes = np.diff(temperatures) / np.diff(times) / len(arrivals)
        shifted = times[1:-1] - arrival
        included = (shifted > first) & (shifted < last)
        knots.append(shifted[included])
        changes.append(np.diff(slopes)[included])
        initial += np.interp(first + arrival, times, temperatures) / len(arrivals)
        interval = min(max(np.searchsorted(times, first + arrival, side="right") - 1, 0), len(slopes) - 1)
        slope += slopes[interval]
    relative, inverse = np.unique(np.concatenate(knots), return_inverse=True)
    delta = np.bincount(inverse, weights=np.concatenate(changes), minlength=len(relative))
    mean = np.r_[initial, initial + np.cumsum((slope + np.cumsum(delta))[:-1] * np.diff(relative))]
    return relative, mean


def material_temperature(data):
    root = ET.fromstring(data)
    element = root.find("melting_point")
    if element is None or not element.text:
        raise ValueError("材料文件缺少 melting_point，请填写材料熔化温度")
    value = float(element.text)
    unit = element.get("unit_symbol")
    if unit == "K":
        value -= 273.15
    elif unit not in ("°C", "degC", "C"):
        raise ValueError("材料熔化温度的单位需要为 °C 或 K")
    if not math.isfinite(value) or value <= -273.15:
        raise ValueError("材料熔化温度无效")
    return {"melting_c": value, "material": root.findtext("name") or "已导入材料"}


class ThermalConversion:
    def __init__(self, model: SurfaceModel, unit: str, sampling=None):
        if unit not in ("m", "mm"):
            raise ValueError("请选择 DAT 长度单位")
        self.model = model
        self.plan = read_weld_paths(model.deck, unit)
        self.points = model.points * (1000 if unit == "m" else 1)
        self.node_index = {int(node): index for index, node in enumerate(model.node_ids)}
        known = {}
        self.components = {}
        for name, family, tokens, where in model.deck.mesh.sets:
            if family != "node":
                continue
            members = _marc._expand(tokens, name, known, _marc._DAT)
            known[name.lower()] = members
            if name.startswith("T_cpt_") and name.endswith("_n_set"):
                ids = sorted(set(members))
                if any(node not in self.node_index for node in ids):
                    raise ValueError(f"组件 {name} 引用了不存在的节点")
                self.components[name[6:-6]] = ids
        if not self.components:
            raise ValueError("DAT 中没有可识别的 Simufact 组件节点集（T_cpt_…_n_set）")
        self.exported = False
        self.temperatures = None
        self._temperature_file = None
        self.summary = None
        self.selected = {}
        self.resample(sampling)

    def resample(self, sampling=None):
        parameters = sampling_parameters(self.plan["paths"], sampling)
        paths = [dict(path) for path in self.plan["paths"]]
        selected, missing = select_nodes(self.model, self.points, self.node_index, self.components,
                                         paths, parameters)
        if self.selected.keys() != selected.keys():
            self.exported = False
            self.clear_temperatures()
        self.plan["paths"] = paths
        self.sampling, self.selected, self.missing = parameters, selected, missing
        self.sampling_id = sha256(json.dumps({"parameters": parameters, "nodes": sorted(selected),
                                              "source": self.model.deck.digest}, sort_keys=True).encode()).hexdigest()
        if self.summary is not None:
            self.refresh_summary()
        return self.metadata()

    def clear_temperatures(self):
        if self.temperatures is not None:
            self.temperatures[2]._mmap.close()
        self.temperatures = None
        self.summary = None
        if self._temperature_file is not None:
            self._temperature_file.close()
        self._temperature_file = None

    def close(self):
        self.clear_temperatures()

    def sampling_ready(self):
        return bool(self.selected) and not self.missing and all(
            segment["samples"] for path in self.plan["paths"] for segment in path["segments"])

    def metadata(self):
        return {"source_process": self.plan["source_process"], "source_sha256": self.model.deck.digest,
                "sampling": self.sampling, "sampling_id": self.sampling_id, "missing": self.missing,
                "node_count": len(self.selected), "missing_count": len(self.missing),
                "missing_length_mm": sum(item["length_mm"] for item in self.missing),
                "segment_count": sum(len(path["segments"]) for path in self.plan["paths"]),
                "exported": self.exported, "temperatures": self.summary,
                "can_export": self.sampling_ready(),
                "can_create": self.sampling_ready() and self.summary is not None and all(
                    row["complete"] for row in self.summary["coverage"]),
                "room_c": self.plan.get("room_c"), "end_s": self.plan["end_s"],
                "paths": self.plan["paths"], "nodes": [{**node, "paths": sorted(node["paths"])} for node in self.selected.values()]}

    def script(self, kind, config):
        template = Path(__file__).with_name("templates") / ("tc_export.py" if kind == "export" else "tc_create.py")
        code = "CONFIG = " + repr(config) + "\n\n" + template.read_text(encoding="utf-8")
        ast.parse(code)
        return code.encode("utf-8")

    def export_script(self):
        if not self.sampling_ready():
            raise ValueError("存在未覆盖的焊线或没有采样节点的分段，请调整搜索半径或分段设置")
        groups = [{"name": name, "node_count": len(ids),
                   "node_ids": sorted(node for component, node in self.selected if component == name)}
                  for name, ids in self.components.items()]
        groups = [group for group in groups if group["node_ids"]]
        config = {"source_process": self.plan["source_process"], "source_sha256": self.model.deck.digest,
                  "groups": groups}
        result = self.script("export", config)
        self.exported = True
        return result

    def import_temperatures(self, stream):
        if not self.exported:
            raise ValueError("请生成当前 DAT 的温度导出脚本")
        self.clear_temperatures()
        try:
            self.read_temperatures(stream)
            self.refresh_summary()
            return self.summary
        finally:
            if self.summary is None or "coverage" not in self.summary:
                self.clear_temperatures()

    def read_temperatures(self, stream):
        reader = csv.DictReader(stream)
        required = {"Increment", "Time_s", "Component", "SolverNodeID", "NodeID", "Temperature_C"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("温度 CSV 需要包含 Increment、Time_s、Component、SolverNodeID、NodeID、Temperature_C")
        times, increments, rows = [], [], []
        cohort = None
        current_increment = None
        current_time = None
        seen = set()
        components = {}
        digest_seen = False
        valid_components = {name: set(ids) for name, ids in self.components.items()}
        keys = sorted(self.selected)
        directory = (Path(sys._sd_root) if hasattr(sys, "_sd_root") else Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
                     else Path(__file__).resolve().parents[2]) / "work" / "thermal"
        directory.mkdir(parents=True, exist_ok=True)
        self._temperature_file = TemporaryFile(mode="w+b", dir=directory)

        def finish():
            nonlocal cohort
            if cohort is None:
                cohort = set(seen)
                for component in {key[0] for key in cohort}:
                    ids = {key[1] for key in cohort if key[0] == component}
                    candidates = [name for name, expected in valid_components.items() if ids.issubset(expected)]
                    if len(candidates) != 1:
                        raise ValueError(f"CSV 组件 {component} 无法唯一匹配当前 DAT 的真实节点")
                    components[component] = candidates[0]
                matched = {(components[name], node) for name, node in cohort}
                if len(set(components.values())) != len(components):
                    raise ValueError("温度 CSV 的多个组件对应同一 DAT 组件")
                if self.selected.keys() != matched:
                    absent, extra = len(self.selected.keys() - matched), len(matched - self.selected.keys())
                    raise ValueError(f"温度 CSV 与当前采样节点集合不一致：缺少 {absent} 个，多出 {extra} 个，请运行当前导出脚本")
            if seen != cohort:
                raise ValueError(f"增量 {current_increment} 的节点集合不完整或与其他增量不一致")
            if times and current_time < times[-1]:
                raise ValueError("温度 CSV 的时间必须递增")
            if increments and current_increment <= increments[-1]:
                raise ValueError("温度 CSV 的增量编号必须严格递增")
            row_values = {(components[name], node): temperature for (name, node), temperature in rows}
            values = np.asarray([row_values[key] for key in keys], dtype=np.float64)
            if times and current_time == times[-1]:
                times[-1], increments[-1] = current_time, current_increment
                self._temperature_file.seek(-values.nbytes, 1)
            else:
                times.append(current_time)
                increments.append(current_increment)
            self._temperature_file.write(values.tobytes())

        row_count = 0
        for row_count, row in enumerate(reader, 1):
            if None in row or any(row.get(key) in (None, "") for key in required):
                raise ValueError(f"温度 CSV 第 {reader.line_num} 行字段不完整")
            increment, node = int(row["Increment"]), int(row["SolverNodeID"])
            time, temperature = float(row["Time_s"]), float(row["Temperature_C"])
            if increment < 0 or node <= 0 or node != int(row["NodeID"]) or not math.isfinite(time) or time < 0 or not math.isfinite(temperature) or temperature < -273.15:
                raise ValueError(f"温度 CSV 第 {reader.line_num} 行数值无效")
            digest = row.get("ModelSHA256")
            if digest:
                if digest.lower() != self.model.deck.digest.lower():
                    raise ValueError("温度数据属于其他 DAT，请选择当前模型导出的 CSV")
                digest_seen = True
            if current_increment is not None and increment != current_increment:
                finish()
                rows, seen = [], set()
            if not rows:
                current_increment, current_time = increment, time
            if time != current_time:
                raise ValueError(f"增量 {increment} 包含不同时间")
            key = row["Component"], node
            if key in seen:
                raise ValueError(f"增量 {increment} 的节点 {node} 重复")
            seen.add(key)
            rows.append((key, temperature))
        if not rows:
            raise ValueError("温度 CSV 没有数据")
        finish()
        if "ModelSHA256" in reader.fieldnames and not digest_seen:
            raise ValueError("温度 CSV 缺少当前 DAT 的文件摘要")
        if len(times) < 3 or times[0] > 1e-6:
            raise ValueError("温度数据需要包含初始时刻以及加热、冷却期间的已保存增量")
        self._temperature_file.flush()
        values = np.memmap(self._temperature_file, dtype=np.float64, mode="r", shape=(len(times), len(keys)))
        times = np.asarray(times)
        self.temperatures = times, keys, values
        self.summary = {"rows": row_count, "increments": len(times), "nodes": len(cohort), "end_s": float(times[-1]),
                        "initial_c": float(values[0].mean()), "digest_verified": digest_seen}
        return self.summary

    def refresh_summary(self):
        times, _, _ = self.temperatures
        coverage = []
        curves = self.curves(tolerance=5, normalized=False)
        by_segment = {(curve["path_id"], curve["index"]): curve for curve in curves}
        for path in self.plan["paths"]:
            for segment in path["segments"]:
                curve = by_segment.get((path["id"], segment["index"]))
                reason = ("本分段没有采样节点" if not segment["samples"] else
                          "温度记录未覆盖本条焊路及其冷却过程" if times[-1] < path["cooling_end_s"] - 1e-6 else
                          curve["reason"] if curve else "温度记录没有共同的有效时间范围")
                coverage.append({"path_id": path["id"], "index": segment["index"], "label": segment["label"],
                                 "complete": not reason, "weld_complete": bool(times[-1] >= segment["end_s"] - 1e-6),
                                 "reason": reason})
        self.summary.update(coverage=coverage, curves=curves,
                            can_create=self.sampling_ready() and all(row["complete"] for row in coverage))

    def curves(self, room=None, melting=None, tolerance=5, normalized=True):
        if self.temperatures is None:
            raise ValueError("请导入温度数据")
        times, keys, values = self.temperatures
        columns = {key: index for index, key in enumerate(keys)}
        result = []
        for path in self.plan["paths"]:
            first, last = path["start_s"], min(times[-1], path["cooling_end_s"])
            if last <= first:
                continue
            source_times = np.unique(np.r_[first, times[(times > first) & (times < last)], last])
            if len(source_times) < 3:
                continue
            for segment in path["segments"]:
                samples = segment["samples"]
                if not samples:
                    continue
                arrivals = np.asarray([sample["arrival_s"] for sample in samples])
                if source_times[-1] - arrivals.max() <= source_times[0] - arrivals.min():
                    continue
                measured = np.column_stack([np.interp(source_times, times, values[:, columns[
                    sample["component"], sample["node_id"]]]) for sample in samples])
                half_duration = (segment["end_s"] - segment["start_s"]) / 2
                first_relative = max(float(source_times[0] - arrivals.min()), -half_duration)
                last_relative = min(float(source_times[-1] - arrivals.max()), half_duration)
                if last_relative <= first_relative:
                    continue
                relative, mean = aligned_average(source_times, measured, arrivals, (-half_duration, half_duration))
                peak = int(np.argmax(mean))
                reason = ("分段时段内未包含升温、峰值和降温趋势，请增加分段长度或减少段数" if peak == 0 or peak == len(mean) - 1
                          or mean[peak] <= mean[0] + 1 or relative[-1] <= 0 else "")
                indices, error = simplify_curve(relative, mean, tolerance)
                temperatures = (mean[indices] - room) / (melting - room) if normalized else mean[indices]
                if normalized and (np.any(temperatures < -1e-6) or np.any(temperatures > 2)):
                    raise ValueError(f"{segment['label']} 的归一化温度超出 [0, 2]，请核对温度基准与材料")
                result.append({"path_id": path["id"], "index": segment["index"], "label": segment["label"],
                               "node_count": len(samples), "peak_c": float(mean[peak]),
                               "peak_time_s": float(relative[peak]), "error_c": error, "reason": reason,
                               "curve": np.c_[relative[indices], np.maximum(temperatures, 0) if normalized else temperatures].tolist()})
        return result

    def create_script(self, room, melting, tolerance=5):
        if not all(math.isfinite(value) for value in (room, melting, tolerance)) or not room < melting or tolerance <= 0:
            raise ValueError("请核对初始温度、熔化温度和曲线误差")
        if self.summary is None:
            raise ValueError("请导入温度数据")
        if not self.sampling_ready():
            raise ValueError("存在未覆盖的焊线或没有采样节点的分段，无法生成 TC")
        incomplete = [item["label"] + "：" + item["reason"] for item in self.summary["coverage"] if not item["complete"]]
        if incomplete:
            raise ValueError("分段温度数据不完整：" + "；".join(incomplete[:6]))
        curves = self.curves(room, melting, tolerance)
        by_segment = {(curve["path_id"], curve["index"]): curve for curve in curves}
        paths = [{**path, "segments": [{**segment,
                 "speed_mm_s": segment["length_mm"] / (segment["end_s"] - segment["start_s"]),
                 "curve": by_segment[path["id"], segment["index"]]["curve"]}
                 for segment in path["segments"]]} for path in self.plan["paths"]]
        config = {"source_process": self.plan["source_process"], "target_process": self.plan["source_process"] + "_TC",
                  "source_sha256": self.model.deck.digest, "paths": paths, "room_c": room, "melting_c": melting,
                  "heat_source_xml": Path(__file__).with_name("templates").joinpath("thermal_cycle_source.xml").read_text(encoding="utf-8"),
                  "coverage_note": "各段使用全部成员节点的温度，按照热源经过时刻平移后等权平均。"}
        return self.script("create", config)
