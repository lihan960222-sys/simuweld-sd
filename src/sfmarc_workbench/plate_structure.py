import numpy as np
import shapely
from scipy.sparse import coo_matrix, eye
from scipy.sparse.csgraph import connected_components

from .contact_geometry import read_rigid_surfaces
from .plate_geometry import PlateGrid
from .plate_shell import MITC4Shell


def matrix(rows, ndof):
    indices, columns, values = [], [], []
    for index, row in enumerate(rows):
        for column, value in row.items():
            if value:
                indices.append(index)
                columns.append(column)
                values.append(value)
    return coo_matrix((values, (indices, columns)), shape=(len(rows), ndof)).tocsr()


class Structure:
    def __init__(self, fine, material, spacing_mm=1., model=None, length_unit='m', fixture_body_names=None, fixture_model=None):
        if length_unit not in ('m', 'mm'):
            raise ValueError('长度单位要求 m 或 mm')
        regions = [(layer.name, part.polygon, part.bottom, part.top, part.bottom_plane, part.top_plane)
                   for layer in fine.layers for part in layer.regions]
        same_spacing = (np.isclose(spacing_mm / 1000, fine.spacing, rtol=1e-12, atol=0)
                        and min(np.diff(fine.x).min(), np.diff(fine.y).min()) >= fine.spacing * (1 - 1e-9))
        self.grid = (fine if same_spacing
                     else PlateGrid.from_regions(regions, spacing_mm, fine.origin, fine.basis))
        self.grid.plan = fine.plan
        grid = self.grid
        ny, nx = grid.cells.shape
        self.lookups, self.element_lookups, self.layer_nodes = [], [], []
        xy, connectivity, thickness, fractions, offsets, references = [], [], [], [], [], []
        node_layers, element_layers, cells = [], [], []
        for layer_index, layer in enumerate(grid.layers):
            reference = float(np.sum(layer.area * (layer.top + layer.bottom) / 2) / layer.area.sum())
            references.append(reference)
            lookup = np.full((ny + 1, nx + 1), -1, dtype=int)
            element_lookup = np.full((ny, nx), -1, dtype=int)
            for j, i in np.argwhere(layer.mask):
                nodes = []
                for dj, di in ((0, 0), (0, 1), (1, 1), (1, 0)):
                    if lookup[j + dj, i + di] < 0:
                        lookup[j + dj, i + di] = len(xy)
                        xy.append((grid.x[i + di], grid.y[j + dj]))
                        node_layers.append(layer_index)
                    nodes.append(lookup[j + dj, i + di])
                element_lookup[j, i] = len(connectivity)
                connectivity.append(nodes)
                thickness.append(layer.thickness[j, i])
                fractions.append(layer.fraction[j, i])
                offsets.append((layer.top[j, i] + layer.bottom[j, i]) / 2 - reference)
                element_layers.append(layer_index)
                cells.append((j, i))
            self.lookups.append(lookup)
            self.element_lookups.append(element_lookup)
            self.layer_nodes.append(lookup[lookup >= 0])
        self.references = np.asarray(references)
        self.xy = np.asarray(xy)
        self.node_layers = np.asarray(node_layers)
        self.element_layers = np.asarray(element_layers)
        self.element_cells = self.cells = np.asarray(cells)
        self.connectivity = np.asarray(connectivity)
        self.shell = MITC4Shell(self.xy, self.connectivity, thickness,
                               material.elastic_modulus(298.15), material.poisson,
                               area_fraction=np.minimum(fractions, 1.), section_offset=offsets)
        self.area = np.array([grid.layers[layer].area[j, i] for layer, (j, i) in zip(element_layers, cells)])
        self.volumes = self.area * thickness
        self.ndof = self.shell.ndof
        self.gravity = np.zeros(self.ndof)
        acceleration = grid.basis @ np.array([0., 0., -9.80665])
        for corner in range(4):
            for axis in range(3):
                np.add.at(self.gravity, 5 * self.connectivity[:, corner] + axis,
                          material.density * acceleration[axis] * self.volumes / 4)
        self.map = self.thermal_map(fine)
        self.anchors, self.rigid_mode_dofs = [], []
        for nodes in self.layer_nodes:
            graph = self.connectivity[self.node_layers[self.connectivity[:, 0]] == self.node_layers[nodes[0]]]
            rows = np.repeat(graph[:, 0], 3)
            columns = graph[:, 1:].ravel()
            adjacency = coo_matrix((np.ones(len(rows)), (rows, columns)), shape=(len(xy), len(xy))).tocsr()
            _, labels = connected_components(adjacency[nodes][:, nodes], directed=False)
            anchors = []
            for component in np.unique(labels):
                selected = nodes[labels == component]
                a = int(selected[np.argmin(self.xy[selected].sum(axis=1))])
                span = np.ptp(self.xy[selected], axis=0)
                axis = int(np.argmax(span))
                b = int(selected[np.argmax(self.xy[selected, axis])])
                in_plane = [5 * a, 5 * a + 1, 5 * b + 1 - axis]
                anchors.extend(in_plane)
                self.rigid_mode_dofs.append(in_plane + [5 * a + 2, 5 * a + 3, 5 * a + 4])
            self.anchors.append(anchors)
        fixture_source = model if fixture_model is None else fixture_model
        fixture_unit = getattr(fixture_source, 'length_unit', None) or length_unit
        self.fixture_surfaces = self._fixtures(fixture_source, fixture_unit, fixture_body_names, self.grid)
        self.cover_dofs = self.cover_constraints()
        self.contact, self.gaps, self.contact_k, self.contact_types = self.contacts()
        self.weld, self.weld_times, self.weld_points, self.weld_weights = self.weld_constraints()

    def thermal_map(self, fine):
        if fine is self.grid:
            return eye(len(self.connectivity), format='csr')
        rows, columns, values = [], [], []
        cursor = 0
        names = {layer.name: index for index, layer in enumerate(self.grid.layers)}
        for layer in fine.layers:
            layer_index = names[layer.name]
            for j, i in np.argwhere(layer.mask):
                polygon = layer.polygons[j, i]
                window = self.grid.window(polygon.bounds)
                for jj in range(window[0].start, window[0].stop):
                    for ii in range(window[1].start, window[1].stop):
                        index = self.element_lookups[layer_index][jj, ii]
                        if index >= 0:
                            area = polygon.intersection(self.grid.layers[layer_index].polygons[jj, ii]).area
                            if area > 0:
                                rows.append(index)
                                columns.append(cursor)
                                values.append(area / self.area[index])
                cursor += 1
        result = coo_matrix((values, (rows, columns)), shape=(len(self.connectivity), cursor)).tocsr()
        np.testing.assert_allclose(np.asarray(result.sum(axis=1)).ravel(), 1., atol=1e-7, rtol=0)
        return result

    def at(self, layer, point, height=None, cell=None):
        grid = self.grid
        if cell is None:
            i = min(int(np.searchsorted(grid.x, point[0], side='right') - 1), len(grid.x) - 2)
            j = min(int(np.searchsorted(grid.y, point[1], side='right') - 1), len(grid.y) - 2)
            candidates = [(j, i), (j, i - 1), (j - 1, i), (j - 1, i - 1)]
            cell = next(((jj, ii) for jj, ii in candidates if 0 <= jj < len(grid.y)-1 and 0 <= ii < len(grid.x)-1
                         and self.element_lookups[layer][jj, ii] >= 0
                         and grid.cells[jj, ii].covers(shapely.Point(point))), None)
            if cell is None:
                raise ValueError('插值位置没有对应的 shell 单元')
        j, i = cell
        index = self.element_lookups[layer][j, i]
        if index < 0:
            raise ValueError('插值位置没有对应的 shell 单元')
        xi, eta = (point - np.array([grid.x[i], grid.y[j]])) / np.array([grid.x[i+1] - grid.x[i], grid.y[j+1] - grid.y[j]])
        if not (-1e-6 <= xi <= 1+1e-6 and -1e-6 <= eta <= 1+1e-6):
            raise ValueError('插值位置超出单元范围')
        z = 0. if height is None else height - self.references[layer]
        rows = [{}, {}, {}]
        for node, value in zip(self.connectivity[index], ((1-xi)*(1-eta), xi*(1-eta), xi*eta, (1-xi)*eta)):
            rows[0].update({5 * node: value, 5 * node + 4: value * z})
            rows[1].update({5 * node + 1: value, 5 * node + 3: -value * z})
            rows[2][5 * node + 2] = value
        return rows

    @classmethod
    def _fixtures(cls, model, unit, names, grid):
        surfaces = {'base': [], 'cover': []}
        if model is None:
            return surfaces
        if isinstance(model, dict):
            if not model or set(model) - {'base', 'cover'}:
                raise ValueError('夹具模型要求 base 或 cover 角色')
            for role, item in model.items():
                selected = {role: list(item.body_names.values())}
                result = cls._fixtures(item, getattr(item, 'length_unit', None) or unit, selected, grid)
                surfaces[role].extend(result[role])
            return surfaces
        automatic = names is None
        names = {'base': ['Base'], 'cover': ['Cover']} if automatic else names
        if not isinstance(names, dict) or set(names) - {'base', 'cover'}:
            raise ValueError('fixture_body_names 要求 base 和 cover 名称映射')
        wanted = {str(name).casefold(): role for role, values in names.items()
                  for name in ([values] if isinstance(values, str) else values)}
        if unit not in ('m', 'mm'):
            raise ValueError('夹具长度单位要求 m 或 mm')
        if hasattr(model, 'deck'):
            geometries = read_rigid_surfaces(model.deck)
        else:
            owners = [model.body_names[int(body)] for body in model.body_ids]
            geometries = [(owner, model.points, [face]) for face, owner in zip(model.faces, owners)]
        for owner, coordinates, faces in geometries:
            normalized = owner.casefold()
            role = wanted.get(normalized)
            if automatic and role is None:
                role = next((name for name in ('base', 'cover') if normalized.startswith(name)
                             and normalized[len(name):].isdigit()), None)
            if role is None:
                continue
            points = grid.project(coordinates * (1. if unit == 'm' else .001))
            for face in faces:
                vertices = points[face]
                for index in range(1, len(vertices) - 1):
                    triangle = vertices[[0, index, index + 1]]
                    normal = np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])
                    size = np.linalg.norm(normal)
                    if size == 0:
                        raise ValueError('夹具表面存在零面积三角形')
                    value = normal[2] / size
                    if abs(value) <= .9:
                        continue
                    polygon = shapely.Polygon(triangle[:, :2])
                    if polygon.area <= 1e-20:
                        continue
                    plane = np.linalg.solve(np.c_[triangle[:, :2], np.ones(3)], triangle[:, 2])
                    surfaces[role].append((polygon, plane))
        if not any(surfaces.values()):
            raise ValueError('模型没有可识别的 Base/Cover 刚体夹具表面')
        for role, records in surfaces.items():
            groups = {}
            for polygon, plane in records:
                key = tuple(np.round(plane, 12))
                groups.setdefault(key, []).append(polygon)
            surfaces[role] = [(shapely.union_all(polygons), np.asarray(plane))
                              for plane, polygons in groups.items()]
            # CONTACT 的 patch 方向不决定支撑侧；按高度保留面向板件的包络。
            envelope = []
            for polygon, plane in surfaces[role]:
                visible = polygon
                for other, other_plane in surfaces[role]:
                    difference = (other_plane - plane) * (1 if role == 'base' else -1)
                    overlap = visible.intersection(other)
                    if overlap.area <= 1e-20:
                        continue
                    coordinates = shapely.get_coordinates(overlap)
                    delta = coordinates @ difference[:2] + difference[2]
                    if delta.max() <= 1e-10:
                        continue
                    if delta.min() > 1e-10:
                        visible = visible.difference(overlap)
                    else:
                        length = np.linalg.norm(difference[:2])
                        normal = difference[:2] / length
                        basis = np.array([normal, [-normal[1], normal[0]]])
                        transformed = shapely.transform(overlap, lambda xy: xy @ basis.T)
                        a, b, c, d = transformed.bounds
                        limit = (1e-10 - difference[2]) / length
                        blocked = shapely.clip_by_rect(transformed, max(a, limit), b - 1., c + 1., d + 1.)
                        visible = visible.difference(shapely.transform(blocked, lambda xy: xy @ basis))
                    if visible.area <= 1e-20:
                        break
                if visible.area > 1e-20:
                    envelope.append((visible, plane))
            surfaces[role] = envelope
        return surfaces

    def cover_constraints(self):
        selected = set()
        for layer_index, layer in enumerate(self.grid.layers):
            nodes = self.layer_nodes[layer_index]
            for polygon, plane in self.fixture_surfaces['cover']:
                for part in layer.regions:
                    mask = shapely.covers(polygon.intersection(part.polygon), shapely.points(self.xy[nodes]))
                    candidates = nodes[mask]
                    gap = self.xy[candidates] @ plane[:2] + plane[2] - part.height(self.xy[candidates], 'top')
                    selected.update(int(node) for node in candidates[np.abs(gap) <= 2e-8])
        return 5 * np.asarray(sorted(selected), dtype=int) + 2

    def contacts(self):
        rows, gaps, weights, kinds = [], [], [], []
        for layer_index, layer in enumerate(self.grid.layers):
            for fixture, plane in self.fixture_surfaces['base']:
                for part in layer.regions:
                    overlap = fixture.intersection(part.polygon)
                    if overlap.area <= 0:
                        continue
                    window = self.grid.window(overlap.bounds)
                    for local_j, local_i in np.argwhere(layer.mask[window]):
                        j, i = local_j + window[0].start, local_i + window[1].start
                        clipped = overlap.intersection(layer.polygons[j, i])
                        for triangle in shapely.constrained_delaunay_triangles(clipped).geoms:
                            point = np.asarray(triangle.centroid.coords[0])
                            gap = float(part.height(point, 'bottom') - (point @ plane[:2] + plane[2]))
                            if gap < -2e-8:
                                raise ValueError(f'板件 {layer.name} 与 Base 初始穿入 {-gap*1000:.6g} mm，请检查夹具坐标与长度单位')
                            # 只连接能够直接看到 Base 的板底面。
                            blocked = any(other.polygon.covers(shapely.Point(point)) and other.top[j, i] <= part.height(point, 'bottom')
                                          and other.top[j, i] > point @ plane[:2] + plane[2] + 2e-8
                                          for other in self.grid.layers[:layer_index])
                            if blocked:
                                continue
                            rows.append(self.at(layer_index, point, cell=(j, i))[2])
                            gaps.append(max(0., gap))
                            weights.append(triangle.area / 1e-6)
                            kinds.append(0)
        for contact in self.grid.contacts:
            locations = {}
            tree = shapely.STRtree([record[0] for record in contact.regions])
            for j, i in np.argwhere(contact.area > 1e-16):
                polygon = contact.polygons[j, i]
                for region_index in tree.query(polygon, predicate='intersects'):
                    region, _, _, lower, upper = contact.regions[region_index]
                    clipped = polygon.intersection(region)
                    if clipped.area <= 0:
                        continue
                    for triangle in shapely.constrained_delaunay_triangles(clipped).geoms:
                        for point in np.asarray(triangle.exterior.coords)[:3]:
                            gap = max(0., float(upper.height(point, 'bottom') - lower.height(point, 'top')))
                            key = (*np.round(point, 12), round(gap, 12))
                            if key not in locations:
                                locations[key] = [point, (j, i), 0., gap]
                            locations[key][2] += triangle.area / 3
            for point, cell, area, gap in locations.values():
                row = self.at(contact.upper, point, cell=cell)[2]
                for dof, value in self.at(contact.lower, point, cell=cell)[2].items():
                    row[dof] = row.get(dof, 0.) - value
                rows.append(row)
                gaps.append(gap)
                weights.append(area / 1e-6)
                kinds.append(1)
        return matrix(rows, self.ndof), np.asarray(gaps), np.asarray(weights), np.asarray(kinds, dtype=int)

    def weld_constraints(self):
        rows, times, coordinates, weights = [], [], [], []
        joined = set()
        plan = self.grid.plan or {'paths': []}
        for path in sorted(plan['paths'], key=lambda item: item['start_s']):
            points = self.grid.project(np.asarray(path['points_mm']) / 1000)
            distances = np.r_[0., np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
            if np.any(np.diff(distances) <= 0):
                raise ValueError('焊路包含零长度线段')
            sources = [source for source in path['sources'] if source['kind'] == 5]
            if len(sources) != 1:
                raise ValueError('结构焊接连接要求一个圆锥热源')
            source = sources[0]
            count = max(1, int(np.ceil(distances[-1] / self.grid.spacing)))
            step = distances[-1] / count
            for distance in (np.arange(count) + .5) * step:
                segment = min(np.searchsorted(distances, distance, side='right') - 1, len(points)-2)
                tangent = points[segment+1, :2] - points[segment, :2]
                if np.linalg.norm(tangent) == 0:
                    raise ValueError('焊路在板平面的投影长度为零')
                tangent /= np.linalg.norm(tangent)
                side = np.array([-tangent[1], tangent[0]])
                position = np.array([np.interp(distance, distances, points[:, axis]) for axis in range(3)])
                for contact_index, contact in enumerate(self.grid.contacts):
                    if not contact.polygon.covers(shapely.Point(position[:2])):
                        continue
                    depth = position[2] - float(contact.height_at(position[:2], 'lower'))
                    depth_limit, cutoff = source['depth_mm'] / 1000, source['cutoff_mm'] / 1000
                    if not 0 <= depth < min(depth_limit, cutoff):
                        continue
                    fraction = depth / depth_limit
                    radius = min(((1-fraction)*source['upper_radius_mm'] + fraction*source['lower_radius_mm']) / 1000,
                                 np.sqrt(cutoff**2-depth**2))
                    for relative, weight in zip((-np.sqrt(.6), 0., np.sqrt(.6)), (5/18, 4/9, 5/18)):
                        point = position[:2] + radius * relative * side
                        key = (contact_index, *np.round(point, 12))
                        if key in joined or not contact.polygon.covers(shapely.Point(point)):
                            continue
                        local_depth = position[2] - float(contact.height_at(point, 'lower'))
                        if not 0 <= local_depth < min(depth_limit, cutoff):
                            continue
                        local_fraction = local_depth / depth_limit
                        local_radius = min(((1-local_fraction)*source['upper_radius_mm'] + local_fraction*source['lower_radius_mm']) / 1000,
                                           np.sqrt(cutoff**2-local_depth**2))
                        if abs(radius * relative) > local_radius:
                            continue
                        joined.add(key)
                        lower = self.at(contact.lower, point, float(contact.height_at(point, 'lower')))
                        upper = self.at(contact.upper, point, float(contact.height_at(point, 'upper')))
                        for axis in range(3):
                            row = dict(upper[axis])
                            for dof, value in lower[axis].items():
                                row[dof] = row.get(dof, 0.) - value
                            rows.append(row)
                            passage = min(distances[-1], distance + local_radius)
                            times.append(path['start_s'] + passage / distances[-1] * (path['end_s'] - path['start_s']))
                            coordinates.append(point)
                            weights.append(step * 2 * local_radius * weight / 1.2e-7)
        order = np.argsort(times, kind='stable')
        return (matrix(rows, self.ndof)[order], np.asarray(times)[order],
                np.asarray(coordinates).reshape(-1, 2)[order], np.asarray(weights)[order])

    def metadata(self):
        return dict(nodes=len(self.xy), elements=len(self.connectivity), ndof=self.ndof,
                    spacing_mm=self.grid.spacing * 1000, area_mm2=float(self.area.sum()*1e6),
                    volume_mm3=float(self.volumes.sum()*1e9), thickness_points=5,
                    cover_nodes=len(self.cover_dofs), base_nodes=int(np.count_nonzero(self.contact_types == 0)),
                    weld_constraints=len(self.weld_times), contacts=len(self.gaps),
                    minimum_area_fraction=float(self.shell.area_fraction.min()),
                    fixture_surfaces={name: len(values) for name, values in self.fixture_surfaces.items()},
                    fixture_assumption='仅使用当前模型中识别的 Base 和 Cover 表面；缺少表面时没有相应夹具约束',
                    rigid_mode_dofs=self.rigid_mode_dofs)
