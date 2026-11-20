from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry import Polygon

from .local_trial import LocalTrial
from .thermal_deck import read_weld_paths


@dataclass
class PlateRegion:
    polygon: object
    bottom: float
    top: float
    bottom_plane: object = None
    top_plane: object = None

    def height(self, coordinates, side):
        plane = getattr(self, side + '_plane')
        return np.full(np.asarray(coordinates).shape[:-1], getattr(self, side)) if plane is None else np.asarray(coordinates) @ plane[:2] + plane[2]

    def section(self, height):
        polygon = self.polygon
        for side, direction in (('bottom', 1), ('top', -1)):
            plane = getattr(self, side + '_plane')
            if plane is None or np.linalg.norm(plane[:2]) < 1e-14:
                if direction * (getattr(self, side) - height) > 1e-14:
                    return Polygon()
                continue
            gradient = direction * plane[:2]
            length = np.linalg.norm(gradient)
            normal = gradient / length
            tangent = np.array([-normal[1], normal[0]])
            basis = np.array([normal, tangent])
            transformed = shapely.transform(polygon, lambda xy: xy @ basis.T)
            a, b, c, d = transformed.bounds
            limit = direction * (height - plane[2]) / length
            if limit <= a:
                return Polygon()
            if limit < c:
                transformed = shapely.clip_by_rect(transformed, a - 1, b - 1, limit, d + 1)
            polygon = shapely.transform(transformed, lambda xy: xy @ basis)
        return polygon


@dataclass
class PlateLayer:
    name: str
    regions: list
    polygon: object
    polygons: np.ndarray
    mask: np.ndarray
    fraction: np.ndarray
    bottom: np.ndarray
    top: np.ndarray
    thickness: np.ndarray
    area: np.ndarray
    edges: tuple


@dataclass
class PlateContact:
    lower: int
    upper: int
    polygon: object
    gap: float
    area: np.ndarray
    polygons: np.ndarray
    height: float
    regions: list

    def height_at(self, coordinates, side):
        if side not in ('lower', 'upper'):
            raise ValueError('接触面高度要求 lower 或 upper')
        coordinates = np.asarray(coordinates, dtype=float)
        if coordinates.shape[-1] != 2 or not np.isfinite(coordinates).all():
            raise ValueError('接触面查询坐标无效')
        points = shapely.points(coordinates)
        result = np.full(coordinates.shape[:-1], np.nan)
        for polygon, _, _, lower, upper in self.regions:
            selected = shapely.covers(polygon, points)
            region, face = (lower, 'top') if side == 'lower' else (upper, 'bottom')
            result = np.where(selected, region.height(coordinates, face), result)
        if np.isnan(result).any():
            raise ValueError('查询坐标位于接触区域以外')
        return result

    def gap_at(self, coordinates):
        return np.maximum(0., self.height_at(coordinates, 'upper') - self.height_at(coordinates, 'lower'))


def _refined_axis(lower, upper, features, fine_spacing, spacing, band):
    intervals = []
    for start, end in sorted((max(lower, min(a, b) - band), min(upper, max(a, b) + band)) for a, b in features):
        if end <= start:
            continue
        if intervals and start <= intervals[-1][1]:
            intervals[-1][1] = max(intervals[-1][1], end)
        else:
            intervals.append([start, end])
    values = [lower]

    def uniform(start, end):
        count = max(1, int(np.ceil((end - start) / fine_spacing - 1e-9)))
        values.extend(np.linspace(start, end, count + 1)[1:])

    def graded(start, end, left_spacing, right_spacing):
        length = end - start
        if length <= 1e-14:
            return
        left, right = [], []
        used = 0.
        while True:
            left_spacing = min(spacing, left_spacing * 1.3)
            right_spacing = min(spacing, right_spacing * 1.3)
            if used + left_spacing + right_spacing > length:
                break
            left.append(left_spacing)
            right.append(right_spacing)
            used += left_spacing + right_spacing
            if left_spacing >= spacing and right_spacing >= spacing:
                break
        middle = length - used
        count = max(0, int(np.floor(middle / spacing + .5)))
        widths = left + ([middle / count] * count if count else []) + right[::-1]
        if widths:
            values.extend(start + np.cumsum(widths) * length / sum(widths))
            values[-1] = end
        else:
            values.append(end)

    cursor = lower
    for start, end in intervals:
        graded(cursor, start, fine_spacing, fine_spacing)
        uniform(max(start, cursor), end)
        cursor = end
    graded(cursor, upper, fine_spacing, spacing if intervals else fine_spacing)
    return np.asarray(values)


class PlateGrid:
    @classmethod
    def from_model(cls, model, unit, spacing_mm, weld_spacing_mm=.05, refine_distance_mm=.5, checkpoint=None):
        def check(message):
            if checkpoint is not None:
                checkpoint(message)
        check('恢复工件外形与厚度')
        trial = LocalTrial(model, unit)
        # mm·t·s·N 单位制的功率单位为 N·mm/s。
        plan = read_weld_paths(model.deck, unit, power_scale_W=1. if unit == 'm' else .001)
        directions = np.concatenate([path['normals'] for path in trial.plan['paths']])
        normal = np.asarray(model.normals[np.argmax(model.areas)], dtype=float)
        if normal @ directions.mean(axis=0) < 0:
            normal = -normal
        if np.min(directions @ normal) < 1 - 1e-8:
            raise ValueError('焊路方向与公共板法向不一致')
        axis = np.eye(3)[np.argmin(np.abs(normal))]
        first = axis - (axis @ normal) * normal
        first /= np.linalg.norm(first)
        basis = np.array([first, np.cross(normal, first), normal])
        used = sorted({node for _, nodes, _ in trial.elements.values() for node in nodes})
        origin = trial.points[[trial.node_index[node] for node in used]].mean(axis=0) / 1000
        points = (trial.points / 1000 - origin) @ basis.T
        groups = {}
        reference = {}
        for index, (face, ident, vector, area) in enumerate(zip(model.faces, model.element_ids, model.normals, model.areas)):
            if index % 500 == 0:
                check(f'恢复工件表面 {index} / {len(model.faces)}')
            owner = trial.elements[int(ident)][2]
            value = vector @ normal
            projected = points[face]
            if abs(value) < .01:
                continue
            if abs(value) < .9 or (value > 0 and np.ptp(projected[:, 2]) > 2e-8):
                raise ValueError(f'组件 {owner} 包含不支持的非平面板表面')
            polygon = Polygon(projected[:, :2])
            if not polygon.is_valid or polygon.area <= 0:
                raise ValueError(f'组件 {owner} 的投影面无效')
            height = float(np.mean(projected[:, 2]))
            if np.ptp(projected[:, 2]) > 2e-8:
                for index in range(1, len(projected) - 1):
                    triangle = projected[[0, index, index + 1]]
                    plane = np.linalg.solve(np.c_[triangle[:, :2], np.ones(3)], triangle[:, 2])
                    groups[(owner, 1 if value > 0 else -1, ('plane', int(ident), index))] = [(Polygon(triangle[:, :2]), plane)]
                continue
            # DAT 输出精度范围内的表面高度归组，保留面积加权高度。
            key = (owner, 1 if value > 0 else -1, round(height / 5e-8))
            groups.setdefault(key, []).append((polygon, height))
            if value > 0:
                reference[owner] = reference.get(owner, 0.) + polygon.area
        surfaces = {}
        for (owner, side, _), values in groups.items():
            check(f'合并工件轮廓 {owner}')
            if np.ndim(values[0][1]) == 1:
                surfaces.setdefault(owner, {1: [], -1: []})[side].append(values[0])
                continue
            polygons = np.array([item[0] for item in values], dtype=object)
            areas = shapely.area(polygons)
            polygon = shapely.union_all(polygons)
            if abs(polygon.area - areas.sum()) > max(1e-16, polygon.area * 1e-6):
                raise ValueError(f'组件 {owner} 包含重叠投影表面')
            height = float(np.sum(areas * [item[1] for item in values]) / areas.sum())
            surfaces.setdefault(owner, {1: [], -1: []})[side].append((polygon, height))
        regions = []
        for owner, sides in surfaces.items():
            for top_polygon, top in sides[1]:
                for bottom_polygon, bottom in sides[-1]:
                    polygon = top_polygon.intersection(bottom_polygon)
                    if polygon.area <= 1e-18:
                        continue
                    centroid = np.array(polygon.centroid.coords[0])
                    top_value = top if np.ndim(top) == 0 else centroid @ top[:2] + top[2]
                    bottom_value = bottom if np.ndim(bottom) == 0 else centroid @ bottom[:2] + bottom[2]
                    if top_value <= bottom_value:
                        raise ValueError(f'组件 {owner} 存在不支持的多层柱状区域')
                    regions.append((owner, polygon, bottom_value, top_value,
                                    None if np.ndim(bottom) == 0 else bottom,
                                    None if np.ndim(top) == 0 else top))
        paths = [(np.asarray(path['points_mm']) / 1000 - origin) @ basis.T for path in plan['paths']]
        result = cls.from_regions(regions, spacing_mm, origin=origin, basis=basis,
                                  refinement_paths=paths, weld_spacing_mm=weld_spacing_mm,
                                  refine_distance_mm=refine_distance_mm, checkpoint=checkpoint)
        result.plan = plan
        scale = 1 if unit == 'm' else 1e-3
        for item in result.diagnostics:
            name = item['name']
            bodies = {model.element_bodies[ident] for ident in trial.component_elements[name]}
            if any(model.body_volumes[body] is None for body in bodies):
                raise ValueError(f'组件 {name} 不能确定封闭体积')
            original = sum(model.body_volumes[body] for body in bodies) * scale ** 3
            item.update(original_area_m2=reference[name], original_volume_m3=original,
                        area_relative_error=item['area_m2'] / reference[name] - 1,
                        volume_relative_error=item['volume_m3'] / original - 1)
        return result

    @classmethod
    def from_regions(cls, regions, spacing_mm, origin=None, basis=None,
                     refinement_paths=None, weld_spacing_mm=.05, refine_distance_mm=.5, checkpoint=None):
        if not np.isfinite(spacing_mm) or spacing_mm <= 0 or not regions:
            raise ValueError('规则网格间距和板区域必须有效')
        result = cls()
        result.origin = np.zeros(3) if origin is None else np.asarray(origin, dtype=float)
        result.basis = np.eye(3) if basis is None else np.asarray(basis, dtype=float)
        if result.origin.shape != (3,) or result.basis.shape != (3, 3) or not np.isfinite(result.origin).all() or not np.allclose(result.basis @ result.basis.T, np.eye(3), atol=1e-10):
            raise ValueError('平面坐标基底无效')
        names = {}
        for row in regions:
            name, polygon, bottom, top = row[:4]
            if not polygon.is_valid or polygon.is_empty or polygon.area <= 0 or not np.isfinite([bottom, top]).all() or bottom >= top:
                raise ValueError('板区域、厚度或高度无效')
            part = PlateRegion(polygon, bottom, top, *row[4:])
            for plane in (part.bottom_plane, part.top_plane):
                if plane is not None and (np.shape(plane) != (3,) or not np.isfinite(plane).all()):
                    raise ValueError('板高度平面系数无效')
            coordinates = shapely.get_coordinates(polygon)
            if np.any(part.height(coordinates, 'top') <= part.height(coordinates, 'bottom')):
                raise ValueError('板区域局部厚度必须为正数')
            names.setdefault(name, []).append(part)
        bounds = shapely.total_bounds(np.array([row[1] for row in regions], dtype=object))
        size = spacing_mm / 1000
        refined = refinement_paths is not None and weld_spacing_mm is not None and refine_distance_mm is not None
        if refined:
            if not np.isfinite([weld_spacing_mm, refine_distance_mm]).all() or not 0 < weld_spacing_mm <= spacing_mm or refine_distance_mm < 0:
                raise ValueError('焊路网格间距或加密距离无效')
            paths = [np.asarray(path, dtype=float) for path in refinement_paths]
            if any(path.ndim != 2 or path.shape[1] not in (2, 3) or len(path) < 2 or not np.isfinite(path).all() for path in paths):
                raise ValueError('加密焊路要求平面坐标米单位数组')
            axes = []
            for axis in (0, 1):
                features = [(a[axis], b[axis]) for path in paths for a, b in zip(path[:-1], path[1:])]
                axes.append(_refined_axis(bounds[axis], bounds[axis + 2], features,
                                          weld_spacing_mm / 1000, size, refine_distance_mm / 1000))
            result.x, result.y = axes
        else:
            result.x = bounds[0] + np.arange(int(np.ceil((bounds[2] - bounds[0]) / size)) + 1) * size
            result.y = bounds[1] + np.arange(int(np.ceil((bounds[3] - bounds[1]) / size)) + 1) * size
        estimate = (len(result.x) - 1) * (len(result.y) - 1) * len(names)
        if estimate > 2_000_000:
            raise ValueError(f'预计生成 {estimate:,} 个计算单元，超过当前 2,000,000 个单元资源上限；请在高级参数中调整网格尺寸')
        if checkpoint is not None:
            checkpoint(f'生成独立网格，预计 {estimate:,} 个单元')
        result.dx, result.dy = np.diff(result.x), np.diff(result.y)
        result.cell_area = result.dy[:, None] * result.dx[None, :]
        xx, yy = np.meshgrid(result.x[:-1], result.y[:-1])
        right, upper = np.meshgrid(result.x[1:], result.y[1:])
        result.cells = shapely.box(xx, yy, right, upper)
        result.centers = np.stack(((xx + right) / 2, (yy + upper) / 2), axis=-1)
        result.spacing = size
        result.layers = []
        result.diagnostics = []
        for name, sections in sorted(names.items(), key=lambda row: min(p.bottom for p in row[1])):
            if checkpoint is not None:
                checkpoint(f'计算工件覆盖面积 {name}')
            polygon = shapely.union_all([part.polygon for part in sections])
            if abs(sum(part.polygon.area for part in sections) - polygon.area) > max(1e-16, polygon.area * 1e-8):
                raise ValueError(f'组件 {name} 包含复杂重叠柱状区域')
            pieces = shapely.intersection(result.cells, polygon)
            area = shapely.area(pieces)
            mask = area > result.cell_area * 1e-12
            area = np.where(mask, area, 0.)
            weighted_bottom = np.zeros(area.shape)
            weighted_top = np.zeros(area.shape)
            for part in sections:
                window = result.window(part.polygon.bounds)
                intersection = shapely.intersection(result.cells[window], part.polygon)
                weights = shapely.area(intersection)
                centroids = shapely.get_coordinates(shapely.centroid(intersection), return_index=True)
                coordinates = np.zeros((*weights.shape, 2))
                coordinates.reshape(-1, 2)[centroids[1]] = centroids[0]
                weighted_bottom[window] += weights * part.height(coordinates, 'bottom')
                weighted_top[window] += weights * part.height(coordinates, 'top')
            bottom = np.divide(weighted_bottom, area, out=np.zeros_like(area), where=mask)
            top = np.divide(weighted_top, area, out=np.zeros_like(area), where=mask)
            thickness = top - bottom
            edges = []
            ids = np.arange(area.size).reshape(area.shape)
            for axis in (1, 0):
                a = (slice(None), slice(None, -1)) if axis == 1 else (slice(None, -1), slice(None))
                b = (slice(None), slice(1, None)) if axis == 1 else (slice(1, None), slice(None))
                active = mask[a] & mask[b]
                length = shapely.length(shapely.intersection(pieces[a], pieces[b]))
                active &= length > size * 1e-12
                widths = np.broadcast_to(result.dx if axis == 1 else result.dy[:, None], area.shape)
                edges.append((ids[a][active], ids[b][active], length[active],
                              widths[a][active] / (2 * thickness[a][active]),
                              widths[b][active] / (2 * thickness[b][active])))
            result.layers.append(PlateLayer(name, sections, polygon, pieces, mask, area / result.cell_area, bottom, top, thickness, area, tuple(edges)))
            result.diagnostics.append(dict(name=name, area_m2=float(area.sum()), volume_m3=float(np.sum(area * thickness))))
        result.contacts = []
        contact_groups = {}
        for lower, first in enumerate(result.layers):
            for upper in range(lower + 1, len(result.layers)):
                second = result.layers[upper]
                for a in first.regions:
                    for b in second.regions:
                        polygon = a.polygon.intersection(b.polygon)
                        for middle in result.layers[lower + 1:upper]:
                            polygon = polygon.difference(middle.polygon)
                        if polygon.area <= 1e-18:
                            continue
                        gap = b.bottom - a.top
                        coordinates = shapely.get_coordinates(polygon)
                        if np.any(b.height(coordinates, 'bottom') - a.height(coordinates, 'top') < -2e-8):
                            raise ValueError('不同板件的材料体积发生重叠')
                        key = (lower, upper, 'variable' if a.top_plane is not None or b.bottom_plane is not None else round(max(0., gap), 8))
                        contact_groups.setdefault(key, []).append((polygon, max(0., gap), a.top, a, b))
        for (lower, upper, _), parts in contact_groups.items():
            polygon = shapely.union_all([part[0] for part in parts])
            pieces = shapely.intersection(result.cells, polygon)
            weights = np.array([part[0].area for part in parts])
            gap = float(weights @ np.array([part[1] for part in parts]) / weights.sum())
            height = float(weights @ np.array([part[2] for part in parts]) / weights.sum())
            result.contacts.append(PlateContact(lower, upper, polygon, gap, shapely.area(pieces), pieces, height, parts))
        result.plan = None
        return result

    def project(self, points_m):
        return (np.asarray(points_m) - self.origin) @ self.basis.T

    def window(self, bounds):
        a, b, c, d = bounds
        x0 = max(0, np.searchsorted(self.x, a, side='right') - 1)
        x1 = min(len(self.x) - 1, np.searchsorted(self.x, c, side='left') + 1)
        y0 = max(0, np.searchsorted(self.y, b, side='right') - 1)
        y1 = min(len(self.y) - 1, np.searchsorted(self.y, d, side='left') + 1)
        return slice(y0, y1), slice(x0, x1)
