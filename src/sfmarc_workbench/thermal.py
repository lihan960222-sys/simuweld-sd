import ast
import csv
import math
import json
from hashlib import sha256
from pathlib import Path
import xml.etree.ElementTree as ET

from meshioplusplus.marc import _marc
import numpy as np

from .geometry import SurfaceModel
from .thermal_deck import read_weld_paths
from .sampling import ComponentSurface, sampling_parameters, select_nodes


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
        self.surfaces = {name: ComponentSurface(model, self.points, [self.node_index[node] for node in ids])
                         for name, ids in self.components.items()}
        self.resample(sampling)

    def resample(self, sampling=None):
        self.exported = False
        self.temperatures = None
        self.summary = None
        parameters = sampling_parameters(self.plan["paths"], sampling)
        paths = [dict(path) for path in self.plan["paths"]]
        selected, missing = select_nodes(self.model, self.points, self.node_index, self.components,
                                         paths, parameters, self.surfaces)
        self.plan["paths"] = paths
        self.sampling, self.selected, self.missing = parameters, selected, missing
        self.sampling_id = sha256(json.dumps({"parameters": parameters, "nodes": sorted(selected),
                                              "source": self.model.deck.digest}, sort_keys=True).encode()).hexdigest()
        return self.metadata()

    def metadata(self):
        return {"source_process": self.plan["source_process"], "source_sha256": self.model.deck.digest,
                "sampling": self.sampling, "sampling_id": self.sampling_id, "missing": self.missing,
                "node_count": len(self.selected), "missing_count": len(self.missing),
                "angle_definition": "相邻线段的方向变化角：直线为 0°，直角转弯为 90°。默认阈值取大于 0.01° 的转角中位数的一半，并限制在 0.1° 至 45°；达到阈值的路径顶点补充采样。",
                "surface_definition": "使用组件几何的厚度方向投影到真实外表面；厚度方向绝对值最大的坐标分量朝正方向作为上表面方向。",
                "room_c": self.plan.get("room_c"), "end_s": self.plan["end_s"],
                "paths": self.plan["paths"], "nodes": [{**node, "paths": sorted(node["paths"])} for node in self.selected.values()]}

    def script(self, kind, config):
        template = Path(__file__).with_name("templates") / ("tc_export.py" if kind == "export" else "tc_create.py")
        code = "CONFIG = " + repr(config) + "\n\n" + template.read_text(encoding="utf-8")
        ast.parse(code)
        return code.encode("utf-8")

    def export_script(self):
        if not self.selected:
            raise ValueError("当前采样没有匹配真实节点，请调整采样参数")
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
        self.temperatures = None
        self.summary = None
        reader = csv.DictReader(stream)
        required = {"Increment", "Time_s", "Component", "SolverNodeID", "NodeID", "Temperature_C"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("温度 CSV 需要包含 Increment、Time_s、Component、SolverNodeID、NodeID、Temperature_C")
        records, times, increments, rows = [], [], [], []
        cohort = None
        current_increment = None
        current_time = None
        seen = set()
        components = {}
        digest_seen = False
        representative_ids = {path["representative_node"] for path in self.plan["paths"]}
        if None in representative_ids:
            raise ValueError("部分焊路没有匹配代表节点，请调整采样参数并重新导出温度数据")
        valid_components = {name: set(ids) for name, ids in self.components.items()}

        def finish():
            nonlocal cohort
            if cohort is None:
                cohort = set(seen)
                if not representative_ids.issubset({key[1] for key in cohort}):
                    raise ValueError("温度 CSV 缺少当前 DAT 自动选择的代表节点，请运行当前导出脚本")
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
            row_values = {node: temperature for (_, node), temperature in rows}
            if len(row_values) != len(rows):
                raise ValueError("多个 CSV 组件使用了相同 SolverNodeID，无法唯一关联当前 DAT")
            representative_values = {node: row_values[node] for node in representative_ids}
            if times and current_time == times[-1]:
                times[-1], increments[-1], records[-1] = current_time, current_increment, representative_values
            else:
                times.append(current_time)
                increments.append(current_increment)
                records.append(representative_values)

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
        ids = sorted(representative_ids)
        values = np.array([[record[node] for node in ids] for record in records])
        times = np.asarray(times)
        self.temperatures = times, ids, values
        coverage = [{"id": path["id"], "complete": bool(times[-1] >= path["cooling_end_s"] - 1e-6),
                     "weld_complete": bool(times[-1] >= path["end_s"] - 1e-6)} for path in self.plan["paths"]]
        self.summary = {"rows": row_count, "increments": len(times), "nodes": len(cohort), "end_s": float(times[-1]),
                        "initial_c": float(values[0].mean()), "coverage": coverage,
                        "digest_verified": digest_seen, "curves": self.curves(tolerance=5, normalized=False)}
        return self.summary

    def curves(self, room=None, melting=None, tolerance=5, normalized=True):
        if self.temperatures is None:
            raise ValueError("请导入温度数据")
        times, ids, values = self.temperatures
        result = []
        for path in self.plan["paths"]:
            mask = np.flatnonzero(times <= path["cooling_end_s"] + 1e-6)
            end = int(mask[-1])
            source_times = times[:end + 1]
            temperatures = values[:end + 1, ids.index(path["representative_node"])]
            # 保留本条焊路之前的真实预热记录，时间以热源经过采样节点为零点。
            current = np.flatnonzero((source_times >= path["start_s"] - 1e-6) & (source_times <= path["cooling_end_s"] + 1e-6))
            if len(current) < 3 or source_times[-1] <= path["arrival_s"]:
                raise ValueError(f"焊路 {path['id']} 的代表节点缺少加热后的温度记录")
            peak = int(current[np.argmax(temperatures[current])])
            if peak >= end or temperatures[peak] <= temperatures[current[0]] + 1:
                raise ValueError(f"焊路 {path['id']} 的代表节点缺少完整峰值和冷却记录")
            indices, error = simplify_curve(source_times, temperatures, tolerance)
            normalized_values = (temperatures[indices] - room) / (melting - room) if normalized else temperatures[indices]
            if normalized and (np.any(normalized_values < -1e-6) or np.any(normalized_values > 2)):
                raise ValueError(f"焊路 {path['id']} 的归一化温度超出 [0, 2]，请核对温度基准与材料")
            if normalized:
                normalized_values = np.maximum(normalized_values, 0)
            result.append({"id": path["id"], "node_id": path["representative_node"], "peak_c": float(temperatures[peak]),
                           "peak_time_s": float(source_times[peak]), "error_c": error,
                           "curve": np.c_[source_times[indices] - path["arrival_s"], normalized_values].tolist()})
        return result

    def create_script(self, room, melting, maximum, tolerance, allow_partial):
        if not all(math.isfinite(value) for value in (room, melting, maximum, tolerance)) or not room < melting or maximum <= 0 or tolerance <= 0:
            raise ValueError("请核对初始温度、熔化温度、最大分段长度和曲线误差")
        if self.summary is None:
            raise ValueError("请导入温度数据")
        incomplete = [str(item["id"]) for item in self.summary["coverage"] if not item["complete"]]
        if incomplete and not allow_partial:
            raise ValueError("温度记录未覆盖全部焊程或冷却：" + "、".join(incomplete) + "。请补充数据，或明确选择仅使用已有代表曲线")
        curves = self.curves(room, melting, tolerance)
        paths = [{**path, "segment_count": math.ceil(path["length_mm"] / maximum), "curve": curve["curve"]}
                 for path, curve in zip(self.plan["paths"], curves)]
        config = {"source_process": self.plan["source_process"], "target_process": self.plan["source_process"] + "_TC",
                  "source_sha256": self.model.deck.digest, "paths": paths, "room_c": room, "melting_c": melting,
                  "heat_source_xml": Path(__file__).with_name("templates").joinpath("thermal_cycle_source.xml").read_text(encoding="utf-8"),
                  "coverage_note": ("温度数据未覆盖全部焊程或冷却；仅使用已记录的代表节点曲线，没有补充温度数据。" if incomplete else "温度数据覆盖全部焊程与计划冷却。")}
        return self.script("create", config)
