import numpy as np


def path_coordinates(positions, points, normal):
    positions = np.asarray(positions, dtype=float)
    points = np.asarray(points, dtype=float)
    normal = np.asarray(normal, dtype=float)
    if normal.shape != (3,) or not np.isfinite(normal).all() or np.linalg.norm(normal) < 1e-12:
        raise ValueError('板件法向无效。')
    normal = normal / np.linalg.norm(normal)
    lengths = np.linalg.norm(np.diff(points, axis=0), axis=1)
    if len(lengths) < 1 or np.any(lengths <= 0):
        raise ValueError('代表焊路至少需要两个不同的坐标点。')
    distance = np.full(len(positions), np.inf)
    along, across = np.zeros(len(positions)), np.zeros(len(positions))
    frames = np.zeros((len(positions), 3, 3))
    cursor = 0.
    for first, last, length in zip(points[:-1], points[1:], lengths):
        tangent = (last - first) / length
        lateral = np.cross(normal, tangent)
        if np.linalg.norm(lateral) < 1e-8 or abs(tangent @ normal) > .05:
            raise ValueError('当前代表区间需要板面内焊路及一致的板件法向。')
        lateral /= np.linalg.norm(lateral)
        tangent = np.cross(lateral, normal)
        ratio = np.clip((positions - first) @ ((last - first) / length) / length, 0, 1)
        closest = first + ratio[:, None] * (last - first)
        delta = positions - closest
        planar = delta - (delta @ normal)[:, None] * normal
        metric = np.sum(planar ** 2, axis=1)
        chosen = metric < distance - 1e-15
        distance[chosen] = metric[chosen]
        along[chosen] = cursor + ratio[chosen] * length
        across[chosen] = delta[chosen] @ lateral
        frames[chosen] = np.asarray([tangent, lateral, normal])
        cursor += length
    return along, across, frames, cursor


def representative(value, points, length_mm, normal, bins=24, center_mm=None, edges_mm=None):
    if not np.isfinite(length_mm) or length_mm <= 0 or not 2 <= bins <= 256:
        raise ValueError('代表长度或横向统计区间数量无效。')
    positions = value['positions_mm'].reshape(-1, 3)
    along, across, frames, total_length = path_coordinates(positions, points, normal)
    if length_mm > total_length + 1e-8:
        raise ValueError('代表长度超过局部焊路长度。')
    center_mm = total_length / 2 if center_mm is None else float(center_mm)
    start, end = center_mm - length_mm / 2, center_mm + length_mm / 2
    if start < -1e-8 or end > total_length + 1e-8:
        raise ValueError('代表区间超出局部焊路。')
    selected = (along >= start) & (along <= end)
    if not selected.any():
        raise ValueError('代表区间内没有积分点，请增加区间长度。')
    normal = np.asarray(normal, dtype=float)
    normal /= np.linalg.norm(normal)
    heights = value['points_mm'] @ normal
    thickness = float(heights.max() - heights.min())
    if thickness <= 1e-8:
        raise ValueError('组件没有可识别的板件厚度。')
    if thickness > .2 * np.linalg.norm(np.ptp(value['points_mm'], axis=0)):
        raise ValueError('组件不满足当前薄板代表区间的几何条件。')
    z = positions[selected] @ normal - (heights.max() + heights.min()) / 2
    tensor = value['plastic'].reshape(-1, 3, 3)[selected]
    frame = frames[selected]
    local = np.einsum('nai,nij,nbj->nab', frame, tensor, frame)
    strains = np.column_stack([local[:, 0, 0], local[:, 1, 1], 2 * local[:, 0, 1]])
    transverse = across[selected]
    weights = value['weights_mm3'].reshape(-1)[selected]
    edges = (np.linspace(transverse.min() - 1e-9, transverse.max() + 1e-9, bins + 1)
             if edges_mm is None else np.asarray(edges_mm, dtype=float))
    if edges.shape != (bins + 1,) or not np.all(np.diff(edges) > 0):
        raise ValueError('横向统计区间边界无效。')
    indices = np.clip(np.searchsorted(edges, transverse, side='right') - 1, 0, bins - 1)
    bands, maximum_error = [], 0.
    for index in range(bins):
        mask = indices == index
        if not mask.any():
            continue
        w, height, strain = weights[mask], z[mask], strains[mask]
        gram = np.asarray([[w.sum(), w @ height], [w @ height, w @ (height ** 2)]])
        if np.linalg.det(gram) <= 1e-22:
            raise ValueError('横向区间缺少厚度方向结果，请减少统计区间数量。')
        moments = np.stack([np.einsum('n,ni->i', w, strain), np.einsum('n,n,ni->i', w, height, strain)])
        area = length_mm * (edges[index + 1] - edges[index])
        coefficients = np.stack([moments[0] / (thickness * area),
                                 12 * moments[1] / (thickness ** 3 * area)])
        reconstructed = np.stack([coefficients[0] * thickness * area,
                                   coefficients[1] * thickness ** 3 * area / 12])
        error = float(np.max(np.abs(reconstructed - moments)))
        maximum_error = max(maximum_error, error)
        bands.append({'r_min_mm': float(edges[index]), 'r_max_mm': float(edges[index + 1]),
                      'r_mm': float(np.average(transverse[mask], weights=w)),
                      'volume_mm3': float(w.sum()), 'membrane': coefficients[0].tolist(),
                      'curvature_per_mm': coefficients[1].tolist(),
                      'integrated_strain_mm3': moments[0].tolist(),
                      'integrated_first_moment_mm4': moments[1].tolist()})
    return {'length_mm': float(length_mm), 'start_mm': start, 'end_mm': end,
            'thickness_mm': thickness, 'normal': normal.tolist(),
            'sample_volume_mm3': float(weights.sum()), 'moment_error': maximum_error,
            'components': ['longitudinal', 'transverse', 'engineering_shear'],
            'bands': bands, 'temperature_max_c': value['temperature_max_c'],
            'edges_mm': edges.tolist(),
            'strain_measure': 'plastic-small-strain-equivalent',
            'basis': '体积加权，保持各横向区间的应变零阶及一阶矩'}
