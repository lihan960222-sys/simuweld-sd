import math

import numpy as np
from scipy.spatial import cKDTree


TOLERANCE_MM = 1e-9


def path_geometry(path):
    points = np.asarray(path["points_mm"], dtype=float)
    if points.ndim != 2 or points.shape[1] != 3 or len(points) < 2 or not np.isfinite(points).all():
        raise ValueError(f"焊路 {path['id']} 的坐标无效")
    vectors = np.diff(points, axis=0)
    lengths = np.linalg.norm(vectors, axis=1)
    if np.any(lengths <= 1e-10):
        raise ValueError(f"焊路 {path['id']} 包含重复位置，无法计算连续采样")
    directions = vectors / lengths[:, None]
    angles = np.degrees(np.arccos(np.clip(np.sum(directions[:-1] * directions[1:], axis=1), -1, 1)))
    return points, vectors, lengths, np.r_[0., np.cumsum(lengths)], angles


def sampling_parameters(paths, values=None):
    result = {"search_mm": .25, "divisions": {
        str(path["id"]): {"mode": "length", "value": 2.} for path in paths}}
    if values is not None:
        if not isinstance(values, dict) or set(values) - set(result):
            raise ValueError("采样参数包含未知字段")
        divisions = values.get("divisions", {})
        if not isinstance(divisions, dict) or set(divisions) - set(result["divisions"]):
            raise ValueError("分段参数包含未知焊路")
        result["search_mm"] = values.get("search_mm", result["search_mm"])
        result["divisions"].update(divisions)
    radius = result["search_mm"]
    if isinstance(radius, bool) or not isinstance(radius, (int, float)) or not math.isfinite(radius) or radius <= 0:
        raise ValueError("搜索距离必须为有限正数")
    for path in paths:
        division = result["divisions"][str(path["id"])]
        if not isinstance(division, dict) or set(division) != {"mode", "value"}:
            raise ValueError("分段参数需要包含 mode 和 value")
        mode, value = division["mode"], division["value"]
        if (mode not in ("length", "count") or isinstance(value, bool)
                or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0):
            raise ValueError("分段方式或数值无效")
        if mode == "count" and (not isinstance(value, int) or value > 250000):
            raise ValueError("分段数量必须为不超过 250000 的正整数")
        length = path_geometry(path)[3][-1]
        count = length / value if mode == "length" else value
        if not math.isfinite(count) or count > 250000:
            raise ValueError("分段数量超过 250000，请增加分段长度")
        result["divisions"][str(path["id"])] = {"mode": mode, "value": value}
    return result


def nearest_intervals(tree, points, start, end, radius):
    vector = end - start
    length = float(np.linalg.norm(vector))
    direction = vector / length
    candidates = np.asarray(sorted(tree.query_ball_point(
        (start + end) / 2, length / 2 + radius + TOLERANCE_MM, eps=0)), dtype=int)
    if not len(candidates):
        return []
    relative = points[candidates] - start
    along = relative @ direction
    perpendicular = relative - along[:, None] * direction
    height_squared = np.einsum("ij,ij->i", perpendicular, perpendicular)
    closest = np.clip(along, 0, length)
    distance = np.linalg.norm(relative - closest[:, None] * direction, axis=1)
    valid = distance <= radius + TOLERANCE_MM
    candidates, relative, along, height_squared = candidates[valid], relative[valid], along[valid], height_squared[valid]
    if not len(candidates):
        return []
    intercepts = np.einsum("ij,ij->i", relative, relative)
    envelope, beginnings = [], []
    # 距离平方的二次项相同；剩余一次函数直接给出连续最近区间。
    for index in np.lexsort((candidates, intercepts, along)):
        if envelope and along[index] == along[envelope[-1]]:
            continue
        crossing = -math.inf
        while envelope:
            previous = envelope[-1]
            crossing = (intercepts[index] - intercepts[previous]) / (2 * (along[index] - along[previous]))
            if crossing > beginnings[-1]:
                break
            envelope.pop()
            beginnings.pop()
        envelope.append(int(index))
        beginnings.append(float(crossing) if len(envelope) > 1 else -math.inf)
    boundaries = [0., *[value for value in beginnings[1:] if 0 < value < length], length]

    def winner(position):
        distances = np.linalg.norm(relative - position * direction, axis=1)
        minimum = float(distances.min())
        return int(np.flatnonzero(distances <= minimum + TOLERANCE_MM)[0]), minimum

    result = []
    for lower, upper in zip(boundaries[:-1], boundaries[1:]):
        index, _ = winner((lower + upper) / 2)
        if height_squared[index] > (radius + TOLERANCE_MM) ** 2:
            continue
        reach = math.sqrt(max(0., radius ** 2 - float(height_squared[index])))
        first = max(lower, float(along[index]) - reach)
        last = min(upper, float(along[index]) + reach)
        if first <= last:
            result.append((first, last, int(candidates[index])))
    # 区间交点也执行相同的等距编号规则，保留只在端点最近的节点。
    for position in boundaries:
        index, distance = winner(position)
        if distance <= radius + TOLERANCE_MM:
            result.append((position, position, int(candidates[index])))
    return sorted(result)


def path_point(points, normals, arc, position):
    index = min(max(int(np.searchsorted(arc, position, side="right") - 1), 0), len(arc) - 2)
    ratio = (position - arc[index]) / (arc[index + 1] - arc[index])
    point = points[index] + ratio * (points[index + 1] - points[index])
    normal = normals[index] + ratio * (normals[index + 1] - normals[index])
    size = float(np.linalg.norm(normal))
    if not math.isfinite(size) or size <= 1e-12:
        raise ValueError("焊路方向插值产生了零长度方向")
    return point, normal / size


def select_nodes(model, points, node_index, components, paths, parameters, surfaces=None):
    keys = sorted({(name, int(node)) for name, ids in components.items() for node in ids})
    if not keys:
        raise ValueError("组件没有可用于采样的真实节点")
    if any(node not in node_index for _, node in keys):
        raise ValueError("组件引用了不存在的真实节点")
    node_points = np.asarray([points[node_index[node]] for _, node in keys], dtype=float)
    if not np.isfinite(node_points).all():
        raise ValueError("组件节点坐标包含非有限数值")
    tree = cKDTree(node_points)
    selected, missing = {}, []
    total_segments = 0
    for path_number, path in enumerate(paths, 1):
        xyz, vectors, lengths, arc, _ = path_geometry(path)
        normals = np.asarray(path["normals"], dtype=float)
        if normals.shape != xyz.shape or not np.isfinite(normals).all():
            raise ValueError(f"焊路 {path['id']} 的方向无效")
        normal_lengths = np.linalg.norm(normals, axis=1)
        if np.any(normal_lengths <= 1e-12):
            raise ValueError(f"焊路 {path['id']} 包含零长度方向")
        normals = normals / normal_lengths[:, None]
        duration = path["end_s"] - path["start_s"]
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError(f"焊路 {path['id']} 的持续时间无效")
        path["label"] = f"焊路 {path_number}"
        intervals = []
        for index, (first, last) in enumerate(zip(xyz[:-1], xyz[1:])):
            intervals.extend((lower + arc[index], upper + arc[index], node)
                             for lower, upper, node in nearest_intervals(tree, node_points, first, last, parameters["search_mm"]))
        intervals.sort()
        unique = sorted({node for _, _, node in intervals})
        for node in unique:
            component, node_id = keys[node]
            record = selected.setdefault(keys[node], {"component": component, "node_id": node_id,
                "xyz_mm": node_points[node].tolist(), "paths": set(), "segments": []})
            record["paths"].add(path["id"])
        gaps, covered = [], 0.
        for lower, upper, _ in intervals:
            if lower > covered + TOLERANCE_MM:
                gaps.append((covered, lower))
            covered = max(covered, upper)
        if covered < arc[-1] - TOLERANCE_MM:
            gaps.append((covered, float(arc[-1])))
        for first, last in gaps:
            position, _ = path_point(xyz, normals, arc, (first + last) / 2)
            missing.append({"path_id": path["id"], "label": path["label"], "component": "",
                            "xyz_mm": position.tolist(), "start_mm": float(first), "end_mm": float(last),
                            "length_mm": float(last - first)})
        division = parameters["divisions"][str(path["id"])]
        count = division["value"] if division["mode"] == "count" else math.ceil(arc[-1] / division["value"])
        total_segments += count
        if total_segments > 250000:
            raise ValueError("全部焊路的分段数量超过 250000")
        boundaries = np.linspace(0, arc[-1], count + 1)
        segments = []
        for index, (first, last) in enumerate(zip(boundaries[:-1], boundaries[1:]), 1):
            first_point, first_normal = path_point(xyz, normals, arc, first)
            last_point, last_normal = path_point(xyz, normals, arc, last)
            inside = np.flatnonzero((arc > first + TOLERANCE_MM) & (arc < last - TOLERANCE_MM))
            segment_points = np.vstack((first_point, xyz[inside], last_point))
            segment_normals = np.vstack((first_normal, normals[inside], last_normal))
            segment_vectors = np.diff(segment_points, axis=0)
            segment_lengths = np.linalg.norm(segment_vectors, axis=1)
            segment_arc = np.r_[first, first + np.cumsum(segment_lengths)]
            members = sorted({node for lower, upper, node in intervals
                              if lower <= last + TOLERANCE_MM and upper >= first - TOLERANCE_MM})
            samples, depths = [], []
            label = f"{path['label']} · 第 {index} 段"
            for node in members:
                relative = node_points[node] - segment_points[:-1]
                ratios = np.clip(np.einsum("ij,ij->i", relative, segment_vectors) / segment_lengths ** 2, 0, 1)
                projections = segment_points[:-1] + ratios[:, None] * segment_vectors
                distances = np.linalg.norm(node_points[node] - projections, axis=1)
                positions = segment_arc[:-1] + ratios * segment_lengths
                closest = np.flatnonzero(distances <= distances.min() + TOLERANCE_MM)
                hit = min(closest, key=lambda item: (abs(positions[item] - (first + last) / 2), positions[item]))
                station = float(np.clip(positions[hit], first, last))
                normal = segment_normals[hit] + ratios[hit] * (segment_normals[hit + 1] - segment_normals[hit])
                size = float(np.linalg.norm(normal))
                if size <= 1e-12:
                    raise ValueError(f"{label} 的采样位置包含无效方向")
                depths.append(float((projections[hit] - node_points[node]) @ (normal / size)))
                component, node_id = keys[node]
                samples.append({"component": component, "node_id": node_id, "xyz_mm": node_points[node].tolist(),
                                "station_mm": station, "arrival_s": float(path["start_s"] + station / arc[-1] * duration),
                                "distance_mm": float(distances[hit])})
                selected[keys[node]]["segments"].append({"path_id": path["id"], "index": index, "label": label})
            segments.append({"path_id": path["id"], "index": index, "label": label,
                             "start_mm": float(first), "end_mm": float(last), "length_mm": float(last - first),
                             "points_mm": segment_points.tolist(), "normals": segment_normals.tolist(),
                             "start_s": float(path["start_s"] + first / arc[-1] * duration),
                             "end_s": float(path["start_s"] + last / arc[-1] * duration),
                             "cooling_end_s": path["cooling_end_s"], "samples": samples, "node_count": len(samples),
                             "radius_mm": max((item["distance_mm"] for item in samples), default=0.) + TOLERANCE_MM,
                             "depth_mm": max([0., *depths]) + TOLERANCE_MM})
        path.update(division=dict(division), segments=segments, node_count=len(unique), missing_count=len(gaps),
                    missing_length_mm=float(sum(last - first for first, last in gaps)))
    return selected, missing
