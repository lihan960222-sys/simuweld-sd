# -*- coding: utf-8 -*-
import math

import numpy as np
from hashlib import sha256


def element_key(coordinates_mm):
    values = sorted(tuple(int(value) for value in row) for row in np.rint(np.asarray(coordinates_mm) * 1e6))
    encoded = ';'.join(','.join(str(value) for value in row) for row in values)
    return sha256(encoded.encode('ascii')).hexdigest()


def path_segments(paths, length_mm):
    if not np.isfinite(length_mm) or length_mm <= 0:
        raise ValueError('代表长度无效。')
    result = []
    for path in paths:
        points = np.asarray(path['points_mm'], dtype=float)
        vectors = np.diff(points, axis=0)
        lengths = np.sqrt(np.sum(vectors * vectors, axis=1))
        arcs = np.concatenate([[0.], np.cumsum(lengths)])
        if np.any(lengths <= 0):
            raise ValueError('焊路包含重复点。')
        count = int(math.ceil(arcs[-1] / length_mm - 1e-10))
        for index in range(count):
            start, end = index * length_mm, min((index + 1) * length_mm, arcs[-1])
            selected = np.concatenate([[start], arcs[(arcs > start) & (arcs < end)], [end]])
            coords = np.column_stack([np.interp(selected, arcs, points[:, axis]) for axis in range(3)])
            result.append({'path_id': path['id'], 'index': index + 1, 'length_mm': end - start,
                           'start_s': path['start_s'] + start / path['speed_mm_s'],
                           'end_s': path['start_s'] + end / path['speed_mm_s'], 'points_mm': coords.tolist()})
    return result


def smooth_fraction(time, start, end):
    if end <= start:
        raise ValueError('加载区间无效。')
    ratio = min(1., max(0., (time - start) / (end - start)))
    return ratio * ratio * (3 - 2 * ratio)
