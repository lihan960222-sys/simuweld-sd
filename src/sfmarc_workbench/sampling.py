import math

import numpy as np
from scipy.spatial import cKDTree


def path_geometry(path):
    points = np.asarray(path["points_mm"], dtype=float)
    vectors = np.diff(points, axis=0)
    lengths = np.linalg.norm(vectors, axis=1)
    if np.any(lengths <= 1e-10):
        raise ValueError(f"焊路 {path['id']} 包含重复位置，无法判断转弯")
    directions = vectors / lengths[:, None]
    angles = np.degrees(np.arccos(np.clip(np.sum(directions[:-1] * directions[1:], axis=1), -1, 1)))
    return points, vectors, lengths, np.r_[0., np.cumsum(lengths)], angles


def sampling_parameters(paths, values=None):
    angles = np.concatenate([path_geometry(path)[4] for path in paths])
    positive = angles[angles > .01]
    angle = float(np.clip(np.median(positive) * .5, .1, 45)) if len(positive) else 15.
    result = dict(spacing_mm=2., offsets_mm=[-.75, -.25, 0., .25, .75], surface="both",
                  search_mm=.25, include_turns=True, turn_angle_deg=round(angle, 3))
    if values is not None:
        if not isinstance(values, dict) or set(values) - set(result):
            raise ValueError("采样参数包含未知字段")
        result.update(values)
    if result["turn_angle_deg"] is None:
        result["turn_angle_deg"] = round(angle, 3)
    for key in ("spacing_mm", "search_mm", "turn_angle_deg"):
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("采样间距、搜索距离和转弯角度必须为有限正数")
    if result["turn_angle_deg"] > 180 or result["surface"] not in ("upper", "lower", "both") or not isinstance(result["include_turns"], bool):
        raise ValueError("采样表面或转弯参数无效")
    offsets = result["offsets_mm"]
    if not isinstance(offsets, list) or not offsets or len(offsets) > 101 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in offsets):
        raise ValueError("横向距离需要包含 1 至 101 个有限数值")
    result["offsets_mm"] = sorted(set(float(v) for v in offsets))
    return result


class ComponentSurface:
    def __init__(self, model, points, indices):
        self.indices = np.asarray(indices)
        self.xyz = points[self.indices]
        self.center = self.xyz.mean(axis=0)
        eigenvalues, basis = np.linalg.eigh(np.cov((self.xyz - self.center).T))
        if eigenvalues[1] <= 0 or eigenvalues[0] > eigenvalues[1] * .2:
            raise ValueError("组件几何无法确定板件厚度方向，当前采样需要具有明确上下表面的板件")
        self.normal = basis[:, 0]
        if self.normal[np.argmax(np.abs(self.normal))] < 0:
            self.normal = -self.normal
        self.axes = basis[:, 1:]
        included = np.zeros(len(points), dtype=bool)
        included[self.indices] = True
        triangles = model.triangles[np.all(included[model.triangles], axis=1)]
        xyz = points[triangles]
        projected = (xyz - self.center) @ self.axes
        first = projected[:, 1] - projected[:, 0]
        second = projected[:, 2] - projected[:, 0]
        determinant = first[:, 0] * second[:, 1] - first[:, 1] * second[:, 0]
        valid = np.abs(determinant) > 1e-12
        self.triangles = triangles[valid]
        self.projected = projected[valid]
        self.xyz_triangles = xyz[valid]
        self.first, self.second, self.determinant = first[valid], second[valid], determinant[valid]
        if not len(self.triangles):
            raise ValueError("组件没有可用于采样的板件表面")
        centers = self.projected.mean(axis=1)
        self.radius = float(np.linalg.norm(self.projected - centers[:, None], axis=2).max())
        self.tree = cKDTree(centers)
        normals = np.cross(self.xyz_triangles[:, 1] - self.xyz_triangles[:, 0], self.xyz_triangles[:, 2] - self.xyz_triangles[:, 0])
        orientation = normals @ self.normal
        self.side_indices = {side: np.unique(self.triangles[mask]) for side, mask in
                             (("upper", orientation > 1e-12), ("lower", orientation < -1e-12))}
        if any(not len(indices) for indices in self.side_indices.values()):
            raise ValueError("组件缺少可识别的上下外表面")
        self.side_points = {side: points[indices] for side, indices in self.side_indices.items()}
        self.node_trees = {side: cKDTree(xyz) for side, xyz in self.side_points.items()}

    def intersections(self, point):
        projected = (point - self.center) @ self.axes
        candidates = np.asarray(self.tree.query_ball_point(projected, self.radius + 1e-8), dtype=int)
        relative = projected - self.projected[candidates, 0]
        first, second = self.first[candidates], self.second[candidates]
        u = (relative[:, 0] * second[:, 1] - relative[:, 1] * second[:, 0]) / self.determinant[candidates]
        v = (first[:, 0] * relative[:, 1] - first[:, 1] * relative[:, 0]) / self.determinant[candidates]
        valid = (u >= -1e-8) & (v >= -1e-8) & (u + v <= 1 + 1e-8)
        candidates, u, v = candidates[valid], u[valid], v[valid]
        if not len(candidates):
            return {}
        xyz = self.xyz_triangles[candidates]
        hits = xyz[:, 0] + u[:, None] * (xyz[:, 1] - xyz[:, 0]) + v[:, None] * (xyz[:, 2] - xyz[:, 0])
        heights = hits @ self.normal
        result = {}
        for side, index in (("lower", int(np.argmin(heights))), ("upper", int(np.argmax(heights)))):
            normal = np.cross(xyz[index, 1] - xyz[index, 0], xyz[index, 2] - xyz[index, 0])
            normal /= np.linalg.norm(normal)
            if normal @ self.normal < 0:
                normal = -normal
            result[side] = hits[index], normal
        return result


def select_nodes(model, points, node_index, components, paths, parameters, surfaces):
    selected, missing = {}, []
    total_targets = 0
    for path in paths:
        xyz, vectors, lengths, arc, angles = path_geometry(path)
        turns = arc[1:-1][angles >= parameters["turn_angle_deg"]]
        ratio = float(arc[-1]) / parameters["spacing_mm"]
        if not math.isfinite(ratio) or ratio > 250000:
            raise ValueError("采样目标超过 250000 个，请增加采样间距或减少横向距离")
        count = math.ceil(ratio)
        total_targets += (count + len(turns) + 1) * len(parameters["offsets_mm"]) * len(components) * (2 if parameters["surface"] == "both" else 1)
        if total_targets > 250000:
            raise ValueError("采样目标超过 250000 个，请增加采样间距或减少横向距离")
        stations = np.r_[np.arange(count) * parameters["spacing_mm"], arc[-1], turns if parameters["include_turns"] else []]
        stations = np.unique(np.round(stations, 10))
        path["stations_mm"], path["turn_positions_mm"] = stations.tolist(), turns.tolist()
        path["turn_angles_deg"] = angles.tolist()
        path["representative_node"] = None
        path["representative_component"] = None
        path["arrival_s"] = None
        beginning_missing = len(missing)
        representatives = []
        for distance in stations:
            segment = min(max(int(np.searchsorted(arc, distance, side="right") - 1), 0), len(lengths) - 1)
            station = xyz[segment] + vectors[segment] * ((distance - arc[segment]) / lengths[segment])
            tangent = vectors[segment] / lengths[segment]
            for component, surface in surfaces.items():
                intersections = surface.intersections(station)
                normal = intersections.get("upper", (None, surface.normal))[1]
                lateral = np.cross(normal, tangent)
                norm = np.linalg.norm(lateral)
                if norm < 1e-8:
                    raise ValueError(f"焊路 {path['id']} 的切向垂直于板件表面，无法确定横向采样位置")
                lateral /= norm
                for offset in parameters["offsets_mm"]:
                    target = station + lateral * offset
                    hits = surface.intersections(target)
                    for side in ("upper", "lower") if parameters["surface"] == "both" else (parameters["surface"],):
                        details = dict(component=component, path_id=path["id"], station_mm=float(distance), offset_mm=offset, surface=side)
                        if side not in hits:
                            missing.append(dict(**details, xyz_mm=target.tolist(), reason="目标位置没有板件表面交点"))
                            continue
                        target = hits[side][0]
                        error, local = surface.node_trees[side].query(target)
                        if error > parameters["search_mm"] + 1e-10:
                            missing.append(dict(**details, xyz_mm=target.tolist(), nearest_distance_mm=float(error), reason="最近节点超过搜索距离"))
                            continue
                        node = int(model.node_ids[surface.side_indices[side][int(local)]])
                        key = component, node
                        record = selected.setdefault(key, dict(component=component, node_id=node, xyz_mm=surface.side_points[side][local].tolist(), paths=set(), targets=[]))
                        record["paths"].add(path["id"])
                        record["targets"].append(dict(**details, xyz_mm=target.tolist(), distance_mm=float(error)))
                        representatives.append((abs(distance - arc[-1] / 2), abs(offset), 0 if side == "upper" else 1, -float(target @ normal), component, node, float(distance)))
        path["node_count"] = sum(path["id"] in item["paths"] for item in selected.values())
        path["missing_count"] = len(missing) - beginning_missing
        if representatives:
            *_, component, node, position = min(representatives)
            path["representative_component"], path["representative_node"] = component, node
            normal = surfaces[component].normal
            relative = points[node_index[node]] - xyz[:-1]
            relative -= np.outer(relative @ normal, normal)
            projected = vectors - np.outer(vectors @ normal, normal)
            squared = np.einsum("ij,ij->i", projected, projected)
            if np.any(squared <= 1e-16):
                raise ValueError(f"焊路 {path['id']} 包含垂直板面的运动，无法计算代表节点经过时间")
            parameters_on_path = np.clip(np.einsum("ij,ij->i", relative, projected) / squared, 0, 1)
            errors = np.linalg.norm(relative - parameters_on_path[:, None] * projected, axis=1)
            candidates = np.flatnonzero(errors <= errors.min() + 1e-7)
            positions = arc[:-1] + parameters_on_path * lengths
            index = min(candidates, key=lambda value: abs(positions[value] - position))
            path["arrival_s"] = path["start_s"] + float(positions[index]) / path["speed_mm_s"]
    return selected, missing
