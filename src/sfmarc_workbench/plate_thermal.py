from functools import lru_cache

import numpy as np
import shapely
from numpy.polynomial.legendre import leggauss
from scipy.integrate import quad
from scipy.sparse import coo_matrix, diags
from scipy.sparse.linalg import spsolve
from shapely.geometry import LineString, Point, Polygon


_GX, _GW = leggauss(16)
_GX = (_GX + 1) / 2
_GW = _GW / 2
_TX, _TW = leggauss(4)
_ZX, _ZW = leggauss(12)


def _boundary_integral(geometry, gaussian, circle_radius=None, circle_edge=None):
    value = 0.
    for part in shapely.get_parts(shapely.orient_polygons(geometry)):
        if part.geom_type != 'Polygon':
            continue
        for ring in [part.exterior, *part.interiors]:
            points = np.asarray(ring.coords)
            a, b = points[:-1], points[1:]
            cross = a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]
            if circle_radius is not None:
                # 完整圆边的 Gauss 积分相同，仅对截断边和材料边计算积分。
                squared_radius = circle_radius ** 2
                circle_edges = ((np.abs(np.sum(a * a, axis=1) - squared_radius) < squared_radius * 1e-12)
                                & (np.abs(np.sum(b * b, axis=1) - squared_radius) < squared_radius * 1e-12)
                                & (np.abs(np.sum((b - a) ** 2, axis=1) - 4 * squared_radius * np.sin(np.pi / 1024) ** 2)
                                   < squared_radius * 1e-12))
                value += np.sum(cross[circle_edges]) * circle_edge / (squared_radius * np.sin(2 * np.pi / 1024))
                a, b, cross = a[~circle_edges], b[~circle_edges], cross[~circle_edges]
                if not len(a):
                    continue
            if gaussian > 0 and (circle_radius is None or circle_radius > 2 / np.sqrt(gaussian)):
                direction = b - a
                squared_length = np.sum(direction ** 2, axis=1)
                closest = np.divide(-np.sum(a * direction, axis=1), squared_length,
                                    out=np.zeros(len(a)), where=squared_length > 0).clip(0., 1.)
                distance = np.sum((a + closest[:, None] * direction) ** 2, axis=1)
                adaptive = squared_length > 16 * (distance + 1 / gaussian)
                for index in np.flatnonzero(adaptive & (cross != 0)):
                    # 按距中心最近位置和 Gaussian 尺度设置区间，控制归一后的积分误差。
                    width = np.sqrt((distance[index] + 1 / gaussian) / squared_length[index])
                    offsets = width * np.exp2(np.arange(int(np.ceil(-np.log2(width))) + 1))
                    points = np.r_[closest[index], closest[index] - offsets, closest[index] + offsets]
                    points = np.unique(points[(points > 0) & (points < 1)])

                    def integrand(t):
                        position = a[index] + t * direction[index]
                        squared = float(position @ position)
                        return -np.expm1(-gaussian * squared) * (cross[index] / squared) / 2

                    result = quad(integrand, 0., 1., points=points, epsabs=1e-12 / len(a),
                                  epsrel=1e-12, limit=max(100, 2 * len(points)), full_output=1)
                    if len(result) != 3:
                        raise RuntimeError(f'Gaussian 边界积分未达到精度要求：{result[3]}')
                    value += result[0] / gaussian
                if np.any(adaptive):
                    a, b, cross = a[~adaptive], b[~adaptive], cross[~adaptive]
                    if not len(a):
                        continue
            positions = a[:, None, :] + (b - a)[:, None, :] * _GX[None, :, None]
            squared = np.sum(positions ** 2, axis=-1)
            if gaussian == 0:
                primitive = np.full(squared.shape, .5)
            else:
                primitive = np.divide(-np.expm1(-gaussian * squared), 2 * gaussian * squared,
                                      out=np.full(squared.shape, .5), where=squared > 1e-30)
            value += np.sum(cross * (primitive @ _GW))
    return float(value)


@lru_cache(maxsize=256)
def _circle(gaussian, limit):
    polygon = Point(0, 0).buffer(limit, quad_segs=256)
    return polygon, _boundary_integral(polygon, gaussian)


def _radial_area(radius, limit, gaussian):
    if gaussian == 0:
        return np.pi * limit ** 2
    return np.pi * radius ** 2 * -np.expm1(-gaussian * (limit / radius) ** 2) / gaussian


@lru_cache(maxsize=256)
def _cone_profile(depth, upper, lower, cutoff, gaussian):
    slope = (lower - upper) / depth
    roots = np.roots([1 + slope ** 2, 2 * upper * slope, upper ** 2 - cutoff ** 2])
    breaks = sorted([0., min(depth, cutoff)] + [float(root.real) for root in roots if abs(root.imag) < 1e-14 and 0 < root.real < min(depth, cutoff)])

    def section(z):
        radius = upper + slope * z
        return _radial_area(radius, min(radius, np.sqrt(max(0., cutoff ** 2 - z ** 2))), gaussian)

    normalization = sum(quad(section, a, b, epsabs=1e-25, epsrel=1e-11)[0] for a, b in zip(breaks[:-1], breaks[1:]))
    return breaks, normalization


def _fraction(polygon, center, radius, limit, gaussian):
    if polygon.is_empty or limit <= 0:
        return 0.
    circle, normalization = _circle(float(gaussian), float(limit / radius))
    local = shapely.transform(polygon, lambda coordinates: (coordinates - center) / radius)
    if local.covers(circle):
        return 1.
    intersection = local.intersection(circle)
    if intersection.is_empty:
        return 0.
    return max(0., min(1., _boundary_integral(intersection, gaussian, limit / radius, normalization / 1024) / normalization))


class ThermalSolver:
    def __init__(self, grid, material, settings=None, paths=None):
        defaults = dict(ambient=298.15, initial_temperature=298.15, convection_h=0.,
                        emissivity=0., contact_h=0., gap_h=0., weld_h=10000.,
                        weld_radius_mm=None, external_area=None, fixture_mask=None,
                        fixture_temperature=298.15, fixture_h=0., max_iterations=60,
                        movement_fraction=.5)
        settings = {} if settings is None else dict(settings)
        if set(settings) - set(defaults):
            raise ValueError(f'未知温度计算设置：{sorted(set(settings) - set(defaults))}')
        defaults.update(settings)
        self.settings = defaults
        self.grid, self.material = grid, material
        self.shape = (len(grid.layers), *grid.cells.shape)
        self.mask = np.array([layer.mask for layer in grid.layers])
        self.mass = material.density * np.array([layer.area * layer.thickness for layer in grid.layers])
        for name in ('convection_h', 'contact_h', 'gap_h', 'weld_h', 'fixture_h', 'emissivity'):
            value = np.asarray(defaults[name], dtype=float)
            if not np.isfinite(value).all() or np.any(value < 0):
                raise ValueError(f'{name} 必须是有限非负数值')
        if np.any(np.asarray(defaults['emissivity']) > 1) or not 0 < defaults['movement_fraction'] <= 1:
            raise ValueError('emissivity 或移动积分间距无效')
        if defaults['weld_radius_mm'] is not None and (not np.isfinite(defaults['weld_radius_mm']) or defaults['weld_radius_mm'] <= 0):
            raise ValueError('焊接半径必须为有限正数')
        if not isinstance(defaults['max_iterations'], int) or defaults['max_iterations'] < 1:
            raise ValueError('非线性迭代次数无效')
        for name in ('ambient', 'initial_temperature', 'fixture_temperature'):
            material.enthalpy(defaults[name])
        self.temperature = np.full(self.shape, float(defaults['initial_temperature']))
        self.time = 0.
        self._cancelled = None
        self.energy = dict(input=0., absorbed=0., outside=0., loss=0.)
        self.welded = np.zeros(self.shape, dtype=bool)
        self.welded_events = []
        self.events = self.welded_events
        self.new_welded_events = []
        self._weld_geometry = [Polygon() for _ in grid.contacts]
        self._weld_corridors = {}
        self._weld_finished = set()
        self._weld_area = [np.zeros(grid.cells.shape) for _ in grid.contacts]
        self.welded_area = self._weld_area
        self.welded_polygons = self._weld_geometry
        self._active = np.flatnonzero(self.mask.ravel())
        self._boundary = {name: np.broadcast_to(defaults[name], self.shape).ravel()[self._active]
                          for name in ('ambient', 'convection_h', 'emissivity', 'fixture_h', 'fixture_temperature')}
        self._contact = {name: np.broadcast_to(defaults[name], (len(grid.contacts),))
                         for name in ('contact_h', 'gap_h', 'weld_h')}
        self._indices = np.full(self.mask.size, -1, dtype=int)
        self._indices[self._active] = np.arange(len(self._active))
        self._m = self.mass.ravel()[self._active]
        self._plane_size = grid.cells.size
        self.external_area = np.zeros(self.shape)
        self._regions = []
        for index, layer in enumerate(grid.layers):
            for region in layer.regions:
                upper = region.polygon
                lower = region.polygon
                visible = region.polygon
                for other_index, other in enumerate(grid.layers):
                    if other_index == index:
                        continue
                    if min(part.bottom for part in other.regions) >= region.top - 2e-8:
                        upper = upper.difference(other.polygon)
                    if max(part.top for part in other.regions) <= region.bottom + 2e-8:
                        lower = lower.difference(other.polygon)
                    if max(part.top for part in other.regions) > region.top + 2e-8:
                        visible = visible.difference(other.polygon)
                window = grid.window(region.polygon.bounds)
                exposed = shapely.area(shapely.intersection(grid.cells[window], upper))
                exposed += shapely.area(shapely.intersection(grid.cells[window], lower))
                boundary = region.polygon.intersection(layer.polygon.boundary)
                exposed += shapely.length(shapely.intersection(grid.cells[window], boundary)) * (region.top - region.bottom)
                self.external_area[index][window] += np.where(layer.mask[window], exposed, 0.)
                self._regions.append((index, region, region.polygon, visible))
        self._region_tree = shapely.STRtree([row[2] for row in self._regions])
        self._cell_regions = {}
        if defaults['external_area'] is not None:
            self.external_area = np.broadcast_to(np.asarray(defaults['external_area'], dtype=float), self.shape).copy()
        if not np.isfinite(self.external_area).all() or np.any(self.external_area < 0) or np.any(self.external_area[~self.mask] > 0):
            raise ValueError('外表面面积无效')
        fixture = np.zeros(self.shape) if defaults['fixture_mask'] is None else np.broadcast_to(defaults['fixture_mask'], self.shape).astype(float)
        if not np.isfinite(fixture).all() or np.any((fixture < 0) | (fixture > 2)) or np.any(fixture[~self.mask]):
            raise ValueError('fixture mask 无效')
        self.fixture_area = fixture * np.array([layer.area for layer in grid.layers])
        if np.any(self.fixture_area > self.external_area + 1e-18):
            raise ValueError('夹具接触面积超过有效外表面面积')
        self.external_area = np.maximum(0., self.external_area - self.fixture_area)
        self.paths = []
        for original in ([] if paths is None else paths):
            path = dict(original)
            points = grid.project(np.asarray(path['points_mm'], dtype=float) / 1000)
            normals = np.asarray(path['normals'], dtype=float)
            lengths = np.linalg.norm(np.diff(points[:, :2], axis=0), axis=1)
            if points.ndim != 2 or points.shape[1] != 3 or len(points) < 2 or not np.isfinite(points).all() or np.any(lengths <= 0):
                raise ValueError('焊路点或长度无效')
            if normals.shape != points.shape or not np.isfinite(normals).all() or not np.allclose(np.linalg.norm(normals, axis=1), 1., atol=1e-8) or np.any(normals @ grid.basis[2] < 1 - 1e-8) or np.ptp(points[:, 2]) > 2e-8:
                raise ValueError('温度计算要求焊路位于公共板平面且法向一致')
            if not np.isfinite([path['start_s'], path['end_s']]).all() or path['start_s'] < 0 or path['end_s'] <= path['start_s']:
                raise ValueError('焊路时间无效')
            path['_points'] = points
            path['_distance'] = np.r_[0., np.cumsum(lengths)]
            path['_times'] = path['start_s'] + path['_distance'] / path['_distance'][-1] * (path['end_s'] - path['start_s'])
            path['_line'] = LineString(points[:, :2])
            for source in path['sources']:
                values = [source[name] for name in ('power_W', 'efficiency', 'gaussian', 'radius_mm', 'depth_mm', 'cutoff_mm')]
                if source['power_W'] is None or not np.isfinite(values).all() or min(values) < 0 or min(values[3:]) <= 0 or source['kind'] not in (5, 7) or source['efficiency'] > 1:
                    raise ValueError('热源参数或功率单位无效')
                if not np.isfinite([source['upper_radius_mm'], source['lower_radius_mm']]).all() or (source['kind'] == 5 and min(source['upper_radius_mm'], source['lower_radius_mm']) <= 0):
                    raise ValueError('圆锥热源半径无效')
                table = np.asarray(source['power_table'], dtype=float)
                if table.ndim != 2 or table.shape[1] != 2 or len(table) < 2 or not np.isfinite(table).all() or np.any(np.diff(table[:, 0]) <= 0) or np.any(table[:, 1] < 0):
                    raise ValueError('热源功率表无效')
                if table[0, 0] > path['start_s'] + 1e-9 or table[-1, 0] < path['end_s'] - 1e-9:
                    raise ValueError('功率表未覆盖焊路时间')
            self.paths.append(path)

    def _check_cancel(self):
        cancelled = self._cancelled
        if cancelled is not None and (cancelled() if callable(cancelled) else cancelled.is_set()):
            raise InterruptedError('温度计算已停止')

    def total_enthalpy(self):
        return float(np.sum(self.mass[self.mask] * self.material.enthalpy(self.temperature[self.mask])))

    def add_energy(self, joules):
        joules = np.broadcast_to(np.asarray(joules, dtype=float), self.shape)
        if not np.isfinite(joules).all() or np.any(joules[~self.mask] != 0):
            raise ValueError('单元热量无效')
        enthalpy = self.material.enthalpy(self.temperature[self.mask]) + joules[self.mask] / self.mass[self.mask]
        self.temperature[self.mask] = self.material.temperature(enthalpy)

    def _position(self, path, time):
        return np.array([np.interp(time, path['_times'], path['_points'][:, axis]) for axis in range(3)])

    def _candidates(self, center, radius):
        x0 = max(0, np.searchsorted(self.grid.x, center[0] - radius, side='right') - 1)
        x1 = min(len(self.grid.x) - 1, np.searchsorted(self.grid.x, center[0] + radius, side='left'))
        y0 = max(0, np.searchsorted(self.grid.y, center[1] - radius, side='right') - 1)
        y1 = min(len(self.grid.y) - 1, np.searchsorted(self.grid.y, center[1] + radius, side='left'))
        return [(j, i) for j in range(y0, y1) for i in range(x0, x1)]

    def _cell_region(self, region_index, j, i, visible=False):
        key = (region_index, j, i, visible)
        if key not in self._cell_regions:
            polygon = self._regions[region_index][3 if visible else 2]
            self._cell_regions[key] = polygon.intersection(self.grid.cells[j, i])
        return self._cell_regions[key]

    def _source_weights(self, source, center):
        self._check_cancel()
        result = np.zeros(self.shape)
        gaussian = source['gaussian']
        cutoff = source['cutoff_mm'] / 1000
        search_radius = min(cutoff, max(source['radius_mm'], source['upper_radius_mm'], source['lower_radius_mm']) / 1000)
        region_indices = self._region_tree.query(shapely.box(center[0] - search_radius, center[1] - search_radius, center[0] + search_radius, center[1] + search_radius))
        if source['kind'] == 7:
            radius = source['radius_mm'] / 1000
            limit = min(radius, cutoff)
            candidates = self._candidates(center, limit)
            for region_index in region_indices:
                self._check_cancel()
                index, region, _, visible = self._regions[region_index]
                if abs(center[2] - region.top) > source['depth_mm'] / 1000 + 2e-8:
                    continue
                for j, i in candidates:
                    result[index, j, i] += _fraction(self._cell_region(region_index, j, i, True), center[:2], radius, limit, gaussian)
            return result
        depth = source['depth_mm'] / 1000
        upper, lower = source['upper_radius_mm'] / 1000, source['lower_radius_mm'] / 1000

        def section(z):
            radius = upper + (lower - upper) * z / depth
            return radius, min(radius, np.sqrt(max(0., cutoff ** 2 - z ** 2)))

        breaks, normalization = _cone_profile(depth, upper, lower, cutoff, gaussian)
        if normalization <= 0:
            raise ValueError('圆锥热源作用体积为零')
        candidates = self._candidates(center, min(max(upper, lower), cutoff))
        for region_index in region_indices:
            self._check_cancel()
            index, region, polygon, _ = self._regions[region_index]
            footprint = polygon.intersection(shapely.box(center[0] - search_radius, center[1] - search_radius, center[0] + search_radius, center[1] + search_radius))
            vertices = shapely.get_coordinates(footprint)
            if not len(vertices):
                continue
            top_depth = center[2] - region.height(vertices, 'top')
            bottom_depth = center[2] - region.height(vertices, 'bottom')
            a, b = max(0., float(np.min(top_depth))), min(depth, cutoff, float(np.max(bottom_depth)))
            if b <= a:
                continue
            intervals = sorted(set([a, b] + [float(value) for value in [*breaks, *top_depth, *bottom_depth] if a < value < b]))
            for za, zb in zip(intervals[:-1], intervals[1:]):
                for z, weight in zip((za + zb) / 2 + (zb - za) / 2 * _ZX, _ZW * (zb - za) / 2):
                    radius, limit = section(z)
                    factor = weight * _radial_area(radius, limit, gaussian) / normalization
                    flat = region.bottom_plane is None and region.top_plane is None
                    section_polygon = None if flat else region.section(center[2] - z)
                    for j, i in candidates:
                        piece = self._cell_region(region_index, j, i) if flat else section_polygon.intersection(self.grid.cells[j, i])
                        result[index, j, i] += factor * _fraction(piece, center[:2], radius, limit, gaussian)
        return result

    def _source_energy(self, start, end):
        deposited = np.zeros(self.shape)
        incoming = 0.
        for path in self.paths:
            a, b = max(start, path['start_s']), min(end, path['end_s'])
            if b <= a:
                continue
            speed = path['_distance'][-1] / (path['end_s'] - path['start_s'])
            for source in path['sources']:
                table = np.asarray(source['power_table'])
                radius = source['radius_mm'] if source['kind'] == 7 else min(source['upper_radius_mm'], source['lower_radius_mm'])
                interval = self.settings['movement_fraction'] * min(radius, source['cutoff_mm']) / 1000 / speed
                boundaries = np.unique(np.r_[a, b, path['_times'][(path['_times'] > a) & (path['_times'] < b)], table[(table[:, 0] > a) & (table[:, 0] < b), 0], np.linspace(a, b, int(np.ceil((b - a) / interval)) + 1)])
                for first, last in zip(boundaries[:-1], boundaries[1:]):
                    self._check_cancel()
                    for time, weight in zip((first + last) / 2 + (last - first) / 2 * _TX, _TW * (last - first) / 2):
                        energy = weight * source['power_W'] * source['efficiency'] * np.interp(time, table[:, 0], table[:, 1])
                        incoming += energy
                        deposited += energy * self._source_weights(source, self._position(path, time))
        absorbed = float(deposited.sum())
        if absorbed > incoming + max(1e-12, incoming * 1e-7):
            raise RuntimeError('材料热源积分超过完整热源能量')
        return deposited, incoming

    def _activation_time(self, path, radius, coordinates):
        points = np.asarray(coordinates, dtype=float)
        output = np.full(points.shape[:-1], -np.inf)
        for a, b, start, end in zip(path['_points'][:-1, :2], path['_points'][1:, :2], path['_times'][:-1], path['_times'][1:]):
            direction = b - a
            delta = points - a
            projection = np.sum(delta * direction, axis=-1) / (direction @ direction)
            distance = np.sum(delta ** 2, axis=-1) - projection ** 2 * (direction @ direction)
            inside = distance <= radius ** 2 + 1e-24
            half = np.sqrt(np.maximum(0., radius ** 2 - distance) / (direction @ direction))
            entry, exit = projection - half, projection + half
            valid = inside & (exit >= 0) & (entry <= 1)
            output = np.maximum(output, np.where(valid, start + np.minimum(exit, 1) * (end - start), -np.inf))
        return output

    def _update_welds(self, start, end):
        self.new_welded_events = []
        average_area = [area.copy() for area in self._weld_area]
        for contact_index, contact in enumerate(self.grid.contacts):
            for path_index, path in enumerate(self.paths):
                key = (contact_index, path_index)
                if key in self._weld_finished:
                    continue
                if end <= path['start_s']:
                    continue
                cones = [source for source in path['sources'] if source['kind'] == 5]
                if not cones:
                    continue
                depth = path['_points'][0, 2] - contact.height
                source = cones[0]
                if depth < 0 or depth > source['depth_mm'] / 1000 or depth >= source['cutoff_mm'] / 1000:
                    continue
                fraction = depth / (source['depth_mm'] / 1000)
                radius = ((1 - fraction) * source['upper_radius_mm'] + fraction * source['lower_radius_mm']) / 1000
                radius = min(radius, np.sqrt((source['cutoff_mm'] / 1000) ** 2 - depth ** 2))
                if self.settings['weld_radius_mm'] is not None:
                    radius = self.settings['weld_radius_mm'] / 1000
                if key not in self._weld_corridors:
                    self._weld_corridors[key] = path['_line'].buffer(radius, quad_segs=256)
                complete = self._weld_corridors[key]
                if end < path['end_s']:
                    future = np.vstack((self._position(path, end)[:2], path['_points'][path['_times'] > end, :2]))
                    complete = complete.difference(LineString(future).buffer(radius / np.cos(np.pi / 1024), quad_segs=256))
                complete = complete.intersection(contact.polygon)
                delta = complete.difference(self._weld_geometry[contact_index])
                if end >= path['end_s']:
                    self._weld_finished.add(key)
                if delta.area <= 1e-24:
                    continue
                self._weld_geometry[contact_index] = self._weld_geometry[contact_index].union(complete)
                window = self.grid.window(delta.bounds)
                pieces = shapely.intersection(self.grid.cells[window], delta)
                areas = np.zeros(self.grid.cells.shape)
                areas[window] = shapely.area(pieces)
                areas[~(self.mask[contact.lower] & self.mask[contact.upper])] = 0.
                for j, i in zip(*np.nonzero(areas > 1e-24)):
                    geometry = pieces[j - window[0].start, i - window[1].start]
                    centroid = np.array(geometry.representative_point().coords[0])
                    activation = float(self._activation_time(path, radius, centroid))
                    elapsed = np.clip((end - activation) / (end - start), 0., 1.)
                    average_area[contact_index][j, i] += areas[j, i] * elapsed
                    event = dict(lower=contact.lower, upper=contact.upper, cell=(j, i), geometry=geometry,
                                 area_m2=float(areas[j, i]), time_s=end, representative_time_s=activation,
                                 interval_s=(max(start, path['start_s']), min(end, path['end_s'])),
                                 activation_time=lambda xy, p=path, r=radius: self._activation_time(p, r, xy))
                    self.new_welded_events.append(event)
                    self.welded[contact.lower, j, i] = True
                    self.welded[contact.upper, j, i] = True
                self._weld_area[contact_index] += areas
        self.welded_events.extend(self.new_welded_events)
        return average_area

    def _implicit(self, dt, edges, conductance, external=False):
        temperature = self.temperature.ravel()[self._active].copy()
        previous = self.material.enthalpy(temperature)
        reference = self.material.enthalpy(self._boundary['ambient'])
        first, second = edges
        mass = self._m
        area = self.external_area.ravel()[self._active]
        fixture = self.fixture_area.ravel()[self._active]
        def balance(temperature):
            g = conductance(temperature)
            difference = temperature[first] - temperature[second]
            residual = mass * (self.material.enthalpy(temperature) - previous)
            residual += dt * (np.bincount(first, g * difference, minlength=len(mass)) - np.bincount(second, g * difference, minlength=len(mass)))
            diagonal = mass * self.material.capacity(temperature)
            if external:
                ambient = self._boundary['ambient']
                radiation = self._boundary['emissivity'] * 5.670374419e-8
                residual += dt * (area * (self._boundary['convection_h'] * (temperature - ambient) + radiation * (temperature ** 4 - ambient ** 4)) + fixture * self._boundary['fixture_h'] * (temperature - self._boundary['fixture_temperature']))
                diagonal += dt * (area * (self._boundary['convection_h'] + 4 * radiation * temperature ** 3) + fixture * self._boundary['fixture_h'])
            return residual, g, diagonal

        for _ in range(self.settings['max_iterations']):
            self._check_cancel()
            residual, g, diagonal = balance(temperature)
            residual_norm = float(np.sum(np.abs(residual)))
            roundoff = 4 * np.finfo(float).eps * float(np.sum(np.abs(diagonal * temperature))
                                                      + 2 * dt * np.sum(g * (np.abs(temperature[first]) + np.abs(temperature[second]))))
            tolerance = max(roundoff, 1e-12 * float(np.sum(np.abs(mass * (previous - reference)))))
            if residual_norm <= tolerance:
                self.temperature.ravel()[self._active] = temperature
                return
            laplace = coo_matrix((dt * np.r_[g, g, -g, -g], (np.r_[first, second, first, second], np.r_[first, second, second, first])), shape=(len(mass), len(mass))).tocsr()
            change = spsolve(diags(diagonal) + laplace, -residual)
            factor = 1.
            positive, negative = change > 0, change < 0
            if np.any(positive):
                factor = min(factor, float(np.min((self.material.thermal_temperature_max - temperature[positive]) / change[positive])))
            if np.any(negative):
                factor = min(factor, float(np.min((self.material.temperature_min - temperature[negative]) / change[negative])))
            if factor <= 0 or not np.isfinite(change).all():
                raise RuntimeError('温度迭代超过材料有效范围')
            factor = min(1., factor * .999999 if factor < 1 else 1.)
            # 穿过固液相区间时，按能量残差选择迭代步长。
            for _ in range(30):
                candidate = temperature + factor * change
                candidate_norm = float(np.sum(np.abs(balance(candidate)[0])))
                if candidate_norm <= max(tolerance, (1 - 1e-4 * factor) * residual_norm):
                    temperature = candidate
                    break
                factor *= .5
            else:
                raise RuntimeError('温度迭代无法减小能量残差')
        raise RuntimeError('非线性焓迭代未收敛')

    def step(self, dt, source_energy=None):
        if not np.isfinite(dt) or dt <= 0:
            raise ValueError('时间步长必须为有限正数')
        end = self.time + dt
        self._check_cancel()
        deposited, incoming = self._source_energy(self.time, end) if source_energy is None else source_energy
        self.add_energy(deposited)
        average_weld = self._update_welds(self.time, end)
        for axis in (0, 1):
            self._check_cancel()
            edge_a, edge_b, lengths, halves_a, halves_b = [], [], [], [], []
            for index, layer in enumerate(self.grid.layers):
                a, b, length, half_a, half_b = layer.edges[axis]
                edge_a.extend(self._indices[index * self._plane_size + a])
                edge_b.extend(self._indices[index * self._plane_size + b])
                lengths.extend(length)
                halves_a.extend(half_a)
                halves_b.extend(half_b)
            a, b = np.asarray(edge_a, dtype=int), np.asarray(edge_b, dtype=int)
            lengths = np.asarray(lengths)
            halves_a, halves_b = np.asarray(halves_a), np.asarray(halves_b)

            def conduction(temperature, a=a, b=b, lengths=lengths, ha=halves_a, hb=halves_b):
                k = self.material.conductivity(temperature)
                return lengths / (ha / k[a] + hb / k[b])

            self._implicit(dt, (a, b), conduction)
        edge_a, edge_b, factors = [], [], []
        for index, contact in enumerate(self.grid.contacts):
            active = (contact.area > 0) & self.mask[contact.lower] & self.mask[contact.upper]
            cells = np.flatnonzero(active.ravel())
            edge_a.extend(self._indices[contact.lower * self._plane_size + cells])
            edge_b.extend(self._indices[contact.upper * self._plane_size + cells])
            h = self._contact['gap_h' if contact.gap > 2e-8 else 'contact_h'][index]
            factor = h * contact.area + (self._contact['weld_h'][index] - h) * average_weld[index]
            factors.extend(factor.ravel()[cells])
        edges = np.asarray(edge_a, dtype=int), np.asarray(edge_b, dtype=int)
        factors = np.asarray(factors)
        self._implicit(dt, edges, lambda temperature: factors)
        before_loss = self.total_enthalpy()
        empty = np.array([], dtype=int)
        self._implicit(dt, (empty, empty), lambda temperature: np.array([]), external=True)
        self.energy['input'] += incoming
        absorbed = float(deposited.sum())
        self.energy['absorbed'] += absorbed
        self.energy['outside'] += incoming - absorbed
        self.energy['loss'] += before_loss - self.total_enthalpy()
        self.time = end
        return self.new_welded_events

    def iter_steps(self, end, dt, cooling_tolerance=None, cooling_limit=100., cancelled=None, on_step=None, adaptive=False):
        if not np.isfinite([end, dt, cooling_limit]).all() or end < self.time or dt <= 0 or cooling_limit < 0:
            raise ValueError('计算结束时间、时间步长或冷却上限无效')
        if cooling_tolerance is not None and (not np.isfinite(cooling_tolerance) or cooling_tolerance <= 0):
            raise RuntimeError('冷却温度容差必须为有限正数')
        limit = end + cooling_limit
        origin, index = self.time, 0
        event_times = sorted({float(value) for path in self.paths for value in
                              [*path['_times'], *[row[0] for source in path['sources'] for row in source['power_table']]]})
        previous_dt, rate = None, 0.
        self._cancelled = cancelled
        try:
            while self.time < end - 1e-14 or (cooling_tolerance is not None and np.max(np.abs(self.temperature[self.mask] - self._boundary['ambient'])) > cooling_tolerance):
                self._check_cancel()
                if self.time >= limit - 1e-14:
                    raise RuntimeError(f'冷却达到时间上限 {limit:g} s，温度条件尚未满足')
                if self.time == end:
                    origin, index = end, 0
                index += 1
                target = min(end if self.time < end else limit, origin + index * dt)
                source_energy = None
                if adaptive:
                    active = [path for path in self.paths if path['start_s'] <= self.time < path['end_s']]
                    step = min(dt, .1 if active else .5, (end if self.time < end else limit) - self.time)
                    for path in active:
                        radius = min(source[key] for source in path['sources'] for key in
                                     (('radius_mm',) if source['kind'] == 7 else ('upper_radius_mm', 'lower_radius_mm')))
                        speed = path['_distance'][-1] / (path['end_s'] - path['start_s'])
                        step = min(step, .25 * min(radius / 1000, np.diff(self.grid.x).min(), np.diff(self.grid.y).min()) / speed)
                    if previous_dt is not None:
                        step = min(step, previous_dt * 1.5)
                    if rate > 0:
                        step = min(step, 5. / rate)
                    future = [value for value in event_times if value > self.time + 1e-14]
                    if future:
                        step = min(step, future[0] - self.time)
                    source_energy = self._source_energy(self.time, self.time + step)
                    temperature = self.temperature[self.mask]
                    allowance = self.mass[self.mask] * (self.material.enthalpy(np.minimum(temperature + 5., self.material.thermal_temperature_max)) - self.material.enthalpy(temperature))
                    energy = source_energy[0][self.mask]
                    heated = energy > 0
                    factor = min(1., float(np.min(allowance[heated] / energy[heated]))) if np.any(heated) else 1.
                    if factor < 1:
                        step *= max(factor, 1e-6)
                        source_energy = self._source_energy(self.time, self.time + step)
                    if not np.isfinite(step) or step <= 1e-14:
                        raise RuntimeError('自动时间步长低于有效数值范围，请检查热源与材料参数')
                    target = self.time + step
                previous = self.temperature.copy()
                actual_dt = target - self.time
                events = self.step(actual_dt, source_energy)
                self.time = target
                previous_dt = actual_dt
                rate = float(np.max(np.abs(self.temperature[self.mask] - previous[self.mask]))) / actual_dt
                if on_step is not None:
                    on_step(self.time, self.temperature, events)
                yield self.time, self.temperature.copy(), events
        finally:
            self._cancelled = None
