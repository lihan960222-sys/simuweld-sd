from collections import defaultdict
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).with_name('dependencies')))
from shapely.geometry import LineString, Polygon, GeometryCollection
from shapely.ops import substring, unary_union
from shapely.strtree import STRtree

from .templates.shell_mapping import element_key


def band_polygon(line, first, last):
    empty = GeometryCollection()
    lower = line.buffer(first, single_sided=True, cap_style='flat', join_style='round') if first else empty
    upper = line.buffer(last, single_sided=True, cap_style='flat', join_style='round') if last else empty
    if first >= 0:
        return upper.difference(lower)
    if last <= 0:
        return lower.difference(upper)
    return lower.union(upper)


def pieces(line, first, last, length):
    complete = band_polygon(line, first, last)
    claimed, result = GeometryCollection(), []
    count = int(np.ceil(line.length / length - 1e-10))
    for index in range(count):
        start, end = index * length, min((index + 1) * length, line.length)
        region = band_polygon(substring(line, start, end), first, last).intersection(complete).difference(claimed)
        if not region.is_empty:
            claimed = claimed.union(region)
            result.append((index, start, end, region))
    remainder = complete.difference(claimed)
    fragments = list(remainder.geoms) if hasattr(remainder, 'geoms') else [remainder]
    for region in fragments:
        if region.area <= 1e-14:
            continue
        position = line.project(region.representative_point())
        index = min(count - 1, int(position / length))
        result.append((index, index * length, min((index + 1) * length, line.length), region))
    return complete, result


def active_band(band, thickness):
    return max(np.max(np.abs(band['membrane'])), thickness * np.max(np.abs(band['curvature_per_mm']))) >= 1e-8


def conservative_loading(preview, equivalent, paths, body_components):
    points = np.asarray(preview['points_mm'])
    states = [state for state in equivalent['states'] if state['kind'] == 'cooled']
    normal = np.asarray(states[0]['components'][0]['profile']['normal'], dtype=float)
    normal /= np.linalg.norm(normal)
    axis = np.asarray([1., 0., 0.]) if abs(normal[0]) < .9 else np.asarray([0., 1., 0.])
    axis -= (axis @ normal) * normal
    axis /= np.linalg.norm(axis)
    basis = np.asarray([axis, np.cross(normal, axis)])
    plane = points @ basis.T
    polygons, faces, areas, keys, components = [], [], [], [], []
    known_keys = set()
    for face in preview['faces']:
        component = body_components.get(str(face['body']))
        if not component:
            continue
        xyz = points[face['node_ids']]
        if np.ptp(xyz @ normal) > 1e-3:
            raise ValueError('当前面积等效需要板件与代表数据具有平行的平面方向。')
        polygon = Polygon(plane[face['node_ids']])
        if not polygon.is_valid or polygon.area <= 1e-14:
            raise ValueError('目标 shell 单元含无效或零面积几何。')
        key = element_key(xyz)
        if key in known_keys:
            raise ValueError('目标 shell 含完全重合的重复单元。')
        known_keys.add(key)
        keys.append(key)
        faces.append(face)
        polygons.append(polygon)
        areas.append(polygon.area)
        components.append(component)
    if not polygons:
        raise ValueError('没有选择目标板件。')
    trees = {}
    for component in set(components):
        indices = [index for index, name in enumerate(components) if name == component]
        geometries = [polygons[index] for index in indices]
        if sum(polygon.area for polygon in geometries) - unary_union(geometries).area > sum(polygon.area for polygon in geometries) * 1e-5:
            raise ValueError('目标 shell 单元存在面积重叠，请检查网格。')
        trees[component] = (STRtree(geometries), indices)
    loading = defaultdict(dict)
    expected_membrane, expected_bending = np.zeros(6), np.zeros(6)
    length = equivalent['representative_length_mm']
    for component, (tree, face_indices) in trees.items():
        profiles = [next(row['profile'] for row in state['components'] if row['name'] == component) for state in states]
        history = [GeometryCollection() for _ in profiles]
        for path in sorted(paths, key=lambda value: value['start_s']):
            coordinates = np.asarray(path['points_mm'], dtype=float)
            if np.ptp(coordinates @ normal) > 1e-3:
                raise ValueError('当前代表数据需要位于同一板面方向的焊路。')
            line = LineString(coordinates @ basis.T)
            if not line.is_simple:
                raise ValueError('同一条焊路存在自交，需要明确重复经过的工艺顺序。')
            bands = profiles[0]['bands']
            geometries = {}
            for band_index, band in enumerate(bands):
                if any(active_band(profile['bands'][band_index], profile['thickness_mm']) for profile in profiles):
                    geometries[band_index] = pieces(line, band['r_min_mm'], band['r_max_mm'], length)
            footprint = unary_union([row[0] for row in geometries.values()])
            if not history[-1].intersection(footprint).is_empty and history[-1].intersection(footprint).area > 1e-9:
                raise ValueError('目标区域的重复焊接次数超过代表数据的次数。')
            territories = []
            for ordinal in range(len(profiles)):
                territory = footprint if ordinal == 0 else footprint.intersection(history[ordinal - 1])
                territories.append(territory.difference(history[ordinal]))
            for ordinal in range(len(history) - 1, 0, -1):
                history[ordinal] = history[ordinal].union(history[ordinal - 1].intersection(footprint))
            history[0] = history[0].union(footprint)
            for ordinal, (territory, profile) in enumerate(zip(territories, profiles)):
                if territory.area <= 1e-14:
                    continue
                for band_index, (_, patches) in geometries.items():
                    band = profile['bands'][band_index]
                    if not active_band(band, profile['thickness_mm']):
                        continue
                    grouped_area = defaultdict(float)
                    for index, _, _, region in patches:
                        grouped_area[index] += region.area
                    for index, start, end, region in patches:
                        region = region.intersection(territory)
                        if region.area <= 1e-14:
                            continue
                        middle = (start + end) / 2
                        delta = min(1e-5, (end - start) / 4)
                        direction = np.asarray(line.interpolate(middle + delta).coords[0]) - line.interpolate(middle - delta).coords[0]
                        direction /= np.linalg.norm(direction)
                        tangent = direction @ basis
                        frame = np.asarray([tangent, np.cross(normal, tangent), normal])
                        factor = (end - start) * (band['r_max_mm'] - band['r_min_mm']) / grouped_area[index]
                        tensors = []
                        for field in ('membrane', 'curvature_per_mm'):
                            first, second, shear = band[field]
                            tensor = frame.T @ np.asarray([[first, shear / 2, 0.], [shear / 2, second, 0.], [0., 0., 0.]]) @ frame
                            tensors.append(tensor[[0, 1, 2, 0, 0, 1], [0, 1, 2, 1, 2, 2]] * factor)
                        for local_index in tree.query(region):
                            face_index = face_indices[int(local_index)]
                            area = polygons[face_index].intersection(region).area
                            if area <= 1e-14:
                                continue
                            event_key = (path['id'], ordinal, index)
                            events = loading[face_index]
                            if event_key not in events:
                                events[event_key] = {'start_s': path['start_s'] + start / path['speed_mm_s'],
                                                     'end_s': path['start_s'] + end / path['speed_mm_s'],
                                                     'path_id': path['id'], 'pass': ordinal + 1,
                                                     'normal': normal.tolist(), 'source_thickness_mm': profile['thickness_mm'],
                                                     'membrane_global': np.zeros(6), 'curvature_global_per_mm': np.zeros(6)}
                            events[event_key]['membrane_global'] += tensors[0] * area / areas[face_index]
                            events[event_key]['curvature_global_per_mm'] += tensors[1] * area / areas[face_index]
                            expected_membrane += tensors[0] * area
                            expected_bending += tensors[1] * area
    output, membrane, bending = {}, np.zeros(6), np.zeros(6)
    for index, events in loading.items():
        result = []
        for event in sorted(events.values(), key=lambda row: row['start_s']):
            membrane += event['membrane_global'] * areas[index]
            bending += event['curvature_global_per_mm'] * areas[index]
            event['membrane_global'] = event['membrane_global'].tolist()
            event['curvature_global_per_mm'] = event['curvature_global_per_mm'].tolist()
            result.append(event)
        output[keys[index]] = {'component': components[index], 'events': result}
    if not output:
        raise ValueError('焊路影响区域与所选 shell 板件没有面积交集。')
    error = float(max(np.max(np.abs(membrane - expected_membrane)), np.max(np.abs(bending - expected_bending))))
    return output, {'affected_elements': len(output), 'area_integral_error': error,
                    'method': '目标单元与各段影响区域交集的面积加权；保持面内及弯曲等效作用',
                    'source_target_node_matching': False, 'zero_strain_tolerance': 1e-8}
