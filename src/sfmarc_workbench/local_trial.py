import json
from collections import defaultdict
from hashlib import sha256

import numpy as np
from meshioplusplus.marc import _marc
from scipy.spatial import ConvexHull

from .geometry import FACE_NODES
from .thermal_deck import read_weld_paths


def _arcs(points):
    lengths = np.linalg.norm(np.diff(points, axis=0), axis=1)
    if len(lengths) == 0 or not np.isfinite(points).all() or np.any(lengths <= 0):
        raise ValueError("焊路包含无效坐标或零长度线段")
    return np.r_[0., np.cumsum(lengths)]


def _slice(points, normals, start, end):
    arcs = _arcs(points)
    if not 0 <= start < end <= arcs[-1] + 1e-8:
        raise ValueError("焊路截取范围无效")
    positions = np.r_[start, arcs[(arcs > start) & (arcs < end)], min(end, arcs[-1])]
    coords = np.column_stack([np.interp(positions, arcs, points[:, axis]) for axis in range(3)])
    directions = np.column_stack([np.interp(positions, arcs, normals[:, axis]) for axis in range(3)])
    sizes = np.linalg.norm(directions, axis=1)
    if np.any(sizes <= 1e-12) or not np.isfinite(sizes).all():
        raise ValueError("插值位置无法确定焊路法向")
    return coords, directions / sizes[:, None], positions


def _project(point, points):
    vectors = np.diff(points, axis=0)
    fractions = np.clip(np.einsum("ij,ij->i", point - points[:-1], vectors)
                        / np.einsum("ij,ij->i", vectors, vectors), 0, 1)
    distances = np.linalg.norm(points[:-1] + vectors * fractions[:, None] - point, axis=1)
    index = int(np.argmin(distances))
    arcs = _arcs(points)
    return float(arcs[index] + fractions[index] * (arcs[index + 1] - arcs[index])), float(distances[index])


def _project_options(point, points, tolerance):
    closest = _project(point, points)
    options = [closest]
    for position, endpoint in ((0., points[0]), (float(_arcs(points)[-1]), points[-1])):
        distance = float(np.linalg.norm(point - endpoint))
        if distance <= tolerance and abs(position - closest[0]) > 1e-9:
            options.append((position, distance))
    return options


def _quadratic_interval(offset, vector, radius, first, last):
    a = float(vector @ vector)
    b = float(offset @ vector)
    c = float(offset @ offset - radius * radius)
    if a <= 1e-28:
        return (first, last) if c <= 1e-14 else None
    discriminant = b * b - a * c
    if discriminant < 0:
        return None
    root = np.sqrt(max(0., discriminant))
    lower, upper = max(first, (-b - root) / a), min(last, (-b + root) / a)
    return (lower, upper) if lower <= upper else None


def _segment_covered(first, last, targets, tolerance):
    vector = last - first
    intervals = []
    for a, b in zip(targets[:-1], targets[1:]):
        axis = b - a
        size = float(axis @ axis)
        position = float((first - a) @ axis / size)
        slope = float(vector @ axis / size)
        cuts = [0., 1.]
        if abs(slope) > 1e-15:
            cuts.extend(value for value in (-position / slope, (1 - position) / slope) if 0 < value < 1)
        cuts.sort()
        for lower, upper in zip(cuts[:-1], cuts[1:]):
            center = position + slope * (lower + upper) / 2
            if center <= 0:
                offset, direction = first - a, vector
            elif center >= 1:
                offset, direction = first - b, vector
            else:
                offset, direction = first - a - position * axis, vector - slope * axis
            interval = _quadratic_interval(offset, direction, tolerance, lower, upper)
            if interval is not None:
                intervals.append(interval)
    covered = 0.
    for lower, upper in sorted(intervals):
        if lower > covered + 1e-10:
            return False
        covered = max(covered, upper)
        if covered >= 1 - 1e-10:
            return True
    return False


def _same_trace(first, second, tolerance):
    return all(_segment_covered(a, b, second, tolerance) for a, b in zip(first[:-1], first[1:])) and all(
        _segment_covered(a, b, first, tolerance) for a, b in zip(second[:-1], second[1:]))


def _box_intersects(vertices, lower, upper):
    if np.any(vertices.max(axis=0) < lower) or np.any(vertices.min(axis=0) > upper):
        return False
    if np.any(np.all((vertices >= lower) & (vertices <= upper), axis=1)):
        return True
    hull = ConvexHull(vertices)
    axes = [*np.eye(3), *hull.equations[:, :3]]
    edges = {tuple(sorted((int(a), int(b)))) for face in hull.simplices
             for a, b in zip(face, np.roll(face, -1))}
    for a, b in edges:
        axes.extend(np.cross(vertices[b] - vertices[a], np.eye(3)))
    axes = np.asarray(axes)
    axes = axes[np.linalg.norm(axes, axis=1) > 1e-12]
    projections = vertices @ axes.T
    center, radius = ((lower + upper) / 2) @ axes.T, ((upper - lower) / 2) @ np.abs(axes).T
    return bool(np.all(projections.max(axis=0) >= center - radius - 1e-9)
                and np.all(projections.min(axis=0) <= center + radius + 1e-9))


class LocalTrial:
    def __init__(self, model, unit):
        if unit not in ("m", "mm"):
            raise ValueError("请选择 DAT 长度单位")
        self.model = model
        self.plan = read_weld_paths(model.deck, unit)
        self.points = model.points * (1000 if unit == "m" else 1)
        self.node_index = {int(node): index for index, node in enumerate(model.node_ids)}
        self.components = {}
        known = {}
        for name, family, tokens, _ in model.deck.mesh.sets:
            if family != "node":
                continue
            members = _marc._expand(tokens, name, known, _marc._DAT)
            known[name.lower()] = members
            if name.startswith("T_cpt_") and name.endswith("_n_set"):
                nodes = set(members)
                if not nodes or not nodes.issubset(self.node_index):
                    raise ValueError(f"组件 {name} 的节点集合无效")
                self.components[name[6:-6]] = nodes
        if not self.components:
            raise ValueError("DAT 中没有可识别的 Simufact 组件节点集")
        self.node_components = defaultdict(list)
        for name, nodes in self.components.items():
            for node in nodes:
                self.node_components[node].append(name)
        self.elements = {}
        self.component_elements = {name: [] for name in self.components}
        self.face_owners = defaultdict(list)
        for ident, kind, nodes in model.deck.mesh.elements:
            owners = set(self.node_components[nodes[0]])
            for node in nodes[1:]:
                owners.intersection_update(self.node_components[node])
            if len(owners) != 1:
                raise ValueError(f"单元 {ident} 无法唯一确定所属组件")
            owner = owners.pop()
            self.elements[ident] = (kind, tuple(nodes), owner)
            self.component_elements[owner].append(ident)
            for indices in FACE_NODES[kind]:
                face = tuple(dict.fromkeys(nodes[index] for index in indices))
                if len(face) >= 3:
                    self.face_owners[tuple(sorted(face))].append((ident, owner, face))
        self.model_id = sha256((model.deck.digest + ":" + unit).encode()).hexdigest()

    def metadata(self):
        used_nodes = {node for _, nodes, _ in self.elements.values() for node in nodes}
        component_points = self.points[[self.node_index[node] for node in used_nodes]]
        return {"source_process": self.plan["source_process"], "source_sha256": self.model.deck.digest,
                "model_id": self.model_id,
                "paths": [dict(path, label=path.get("label", f"焊路 {path['id']}")) for path in self.plan["paths"]],
                "components": [{"name": name, "node_count": len(nodes),
                                "element_count": len(self.component_elements[name]),
                                "keep_whole": name.lower() == "base"} for name, nodes in self.components.items()],
                "nodes": [{"id": int(node), "xyz_mm": self.points[index].tolist(),
                           "component": ", ".join(self.node_components[int(node)])}
                          for index, node in enumerate(self.model.node_ids)],
                "bounds_mm": [component_points.min(axis=0).tolist(), component_points.max(axis=0).tolist()]}

    def _crop(self, box, keep_whole):
        lower, upper = np.asarray(box["min"], dtype=float), np.asarray(box["max"], dtype=float)
        if lower.shape != (3,) or upper.shape != (3,) or not np.isfinite([lower, upper]).all() or np.any(lower >= upper):
            raise ValueError("截取包围盒必须包含有限坐标，并且每个方向具有正长度")
        unknown = set(keep_whole) - self.components.keys()
        if unknown:
            raise ValueError(f"整件保留的组件不存在：{sorted(unknown)}")
        if any(name.lower() == "base" and name not in keep_whole for name in self.components):
            raise ValueError("Base 必须完整保留")
        selected, components = set(), []
        for name, identifiers in self.component_elements.items():
            ids = [ident for ident in identifiers if name in keep_whole or _box_intersects(
                self.points[[self.node_index[n] for n in self.elements[ident][1]]], lower, upper)]
            nodes = sorted({node for ident in ids for node in self.elements[ident][1]})
            selected.update(ids)
            components.append({"name": name, "node_ids": nodes, "element_ids": ids,
                               "keep_whole": name in keep_whole, "has_new_cut_faces": False})
        if not any(row["element_ids"] and row["name"].lower() != "base" for row in components):
            raise ValueError("截取范围没有保留任何活动板件")
        nodes = sorted({node for ident in selected for node in self.elements[ident][1]})
        points = self.points[[self.node_index[node] for node in nodes]]
        faces, boundary = [], []
        for owners in self.face_owners.values():
            retained = [owner for owner in owners if owner[0] in selected]
            if len(retained) == 1:
                _, name, face = retained[0]
                boundary.append({"node_ids": list(face), "component": name})
            if retained and len(retained) < len(owners):
                for _, name, face in retained:
                    faces.append({"node_ids": list(face), "component": name})
        cut_components = {face["component"] for face in faces}
        for component in components:
            component["has_new_cut_faces"] = component["name"] in cut_components
        return {"components": components, "node_ids": nodes, "element_ids": sorted(selected),
                "points_mm": points.tolist(), "node_count": len(nodes), "element_count": len(selected),
                "cut_faces": faces, "faces": boundary,
                "bounds_mm": [points.min(axis=0).tolist(), points.max(axis=0).tolist()]}

    def preview(self, spec):
        paths = {path["id"]: path for path in self.plan["paths"]}
        path_id = int(spec["path_id"])
        if path_id not in paths:
            raise ValueError("选择的焊路不存在")
        reference = paths[path_id]
        points, normals = np.asarray(reference["points_mm"]), np.asarray(reference["normals"])
        length = float(_arcs(points)[-1])
        start, end = float(spec["start_mm"]), float(spec["end_mm"])
        tolerance = float(spec.get("tolerance_mm", .01))
        if not np.isfinite([start, end, tolerance]).all() or tolerance <= 0 or not 0 <= start <= length or not 0 <= end <= length:
            raise ValueError("焊路范围或匹配容差无效")
        if spec.get("wrap", False):
            if np.linalg.norm(points[-1] - points[0]) > 1e-9 or start <= end:
                raise ValueError("跨接缝截取需要闭合焊路，并且起始弧长大于结束弧长")
            ranges = [(start, length), (0., end)]
            ranges = [(a, b) for a, b in ranges if b > a]
        else:
            if start >= end:
                raise ValueError("结束弧长必须大于起始弧长")
            ranges = [(start, end)]
        pieces = [_slice(points, normals, a, b) for a, b in ranges]
        selected_points = np.concatenate([piece[0] if index == 0 else piece[0][1:] for index, piece in enumerate(pieces)])
        selected_normals = np.concatenate([piece[1] if index == 0 else piece[1][1:] for index, piece in enumerate(pieces)])
        selected_length = float(_arcs(selected_points)[-1])
        trim_start, trim_end = float(spec.get("trim_start_mm", 0)), float(spec.get("trim_end_mm", 0))
        if not np.isfinite([trim_start, trim_end]).all() or min(trim_start, trim_end) < 0 or trim_start + trim_end >= selected_length:
            raise ValueError("温度提取端部缩短量必须非负，且不能清除整条局部焊路")
        extraction, _, _ = _slice(selected_points, selected_normals, trim_start, selected_length - trim_end)
        included = spec.get("included_path_ids")
        if included is not None and (not included or set(included) - paths.keys() or path_id not in included):
            raise ValueError("历史焊路选择必须包含当前焊路，并且所有编号有效")
        matches = []
        for candidate in self.plan["paths"]:
            if included is not None and candidate["id"] not in included:
                continue
            candidate_points = np.asarray(candidate["points_mm"])
            candidate_normals = np.asarray(candidate["normals"])
            for piece, (source_start, source_end) in zip(pieces, ranges):
                target = piece[0]
                first_options = _project_options(target[0], candidate_points, tolerance)
                last_options = _project_options(target[-1], candidate_points, tolerance)
                if candidate["id"] == path_id:
                    first_options, last_options = [(source_start, 0.)], [(source_end, 0.)]
                first, first_error = first_options[0]
                last, last_error = last_options[0]
                row = {"path_id": candidate["id"], "label": str(candidate["id"]),
                       "points_mm": [], "normals": [], "times_s": [], "matched": False,
                       "reason": "历史焊路未完整覆盖所选路径", "source_start_mm": min(first, last),
                       "source_end_mm": max(first, last), "endpoint_adjustment_mm": [first_error, last_error],
                       "start_s": None, "end_s": None}
                combinations = [(a, b, ae, be) for a, ae in first_options for b, be in last_options
                                if max(ae, be) <= tolerance and abs(b - a) > 1e-10]
                combinations.sort(key=lambda values: abs(abs(values[1] - values[0]) - (source_end - source_start)))
                attempts = [([(min(a, b), max(a, b))], ae, be) for a, b, ae, be in combinations]
                candidate_length = float(_arcs(candidate_points)[-1])
                if candidate["id"] != path_id and np.linalg.norm(candidate_points[0] - candidate_points[-1]) <= 1e-9:
                    for a, b, ae, be in combinations:
                        cyclic = [(max(a, b), candidate_length), (0., min(a, b))]
                        cyclic = [(lower, upper) for lower, upper in cyclic if upper - lower > 1e-10]
                        if cyclic:
                            attempts.append((cyclic, ae, be))
                    if np.linalg.norm(target[0] - target[-1]) <= 1e-9:
                        attempts.append(([(0., candidate_length)], first_error, last_error))
                for intervals, first_error, last_error in attempts:
                    slices = [_slice(candidate_points, candidate_normals, lower, upper) for lower, upper in intervals]
                    trace = np.concatenate([part[0] if index == 0 else part[0][1:] for index, part in enumerate(slices)])
                    if candidate["id"] == path_id or _same_trace(target, trace, tolerance):
                        forward = np.linalg.norm(trace[0] - target[0]) <= np.linalg.norm(trace[0] - target[-1])
                        endpoints = target[[0, -1] if forward else [-1, 0]]
                        corrections = np.linalg.norm(trace[[0, -1]] - endpoints, axis=1)
                        if np.any(corrections > 1e-9):
                            row["reason"] = "历史焊路端点无法与参考端点重合，不能自动改变路径或焊接时间"
                            continue
                        # 仅消除浮点插值误差，共用参考坐标，同时保留来源速度和经过时刻。
                        slices[0][0][0] = endpoints[0]
                        slices[-1][0][-1] = endpoints[-1]
                        if any(abs(_arcs(part[0])[-1] - (upper - lower)) > 1e-9
                               for part, (lower, upper) in zip(slices, intervals)):
                            row["reason"] = "共同端点导致路径长度发生变化，不能自动改变焊接速度或时间"
                            continue
                        accepted = []
                        for (lower, upper), (coords, directions, positions) in zip(intervals, slices):
                            times = candidate["start_s"] + positions / candidate["speed_mm_s"]
                            accepted.append(dict(row, points_mm=coords.tolist(), normals=directions.tolist(), times_s=times.tolist(),
                                                 start_s=float(times[0]), end_s=float(times[-1]), matched=True, reason="",
                                                 source_start_mm=lower, source_end_mm=upper,
                                                 endpoint_adjustment_mm=[first_error, last_error]))
                        row = accepted[0]
                        matches.extend(accepted[1:])
                        break
                matches.append(row)
        matches.sort(key=lambda row: (row["start_s"] is None, row["start_s"] or 0, row["path_id"]))
        keep_whole = spec.get("keep_whole", [name for name in self.components if name.lower() == "base"])
        crop = self._crop(spec["box"], keep_whole)
        lower, upper = np.asarray(spec["box"]["min"]), np.asarray(spec["box"]["max"])
        for row in matches:
            if row["matched"]:
                coords = np.asarray(row["points_mm"])
                if np.any(coords < lower - tolerance) or np.any(coords > upper + tolerance):
                    raise ValueError(f"焊路 {row['path_id']} 超出截取包围盒")
        group_id = sha256(json.dumps({"model_id": self.model_id, "spec": spec}, sort_keys=True).encode()).hexdigest()
        return {"group_id": group_id, "matches": matches, "crop": crop,
                "selection": {"points_mm": selected_points.tolist(), "normals": selected_normals.tolist(),
                              "length_mm": selected_length, "extraction_points_mm": extraction.tolist()}, "warnings": []}
