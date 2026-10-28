from collections import defaultdict, deque

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from .deck import Deck


# Marc Volume C 的 FACE IDS；编号始终对应原始单元连接关系。
FACE_NODES = {
    7: ((0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7), (0, 1, 2, 3), (5, 4, 7, 6)),
    134: ((0, 1, 3), (1, 2, 3), (2, 0, 3), (0, 1, 2)),
    136: ((0, 1, 4, 3), (1, 2, 5, 4), (2, 0, 3, 5), (0, 2, 1), (3, 4, 5)),
}


def body_volumes(points, triangles, triangle_bodies):
    volumes = {}
    for body in np.unique(triangle_bodies):
        volumes[int(body)] = None
        cells = triangles[triangle_bodies == body]
        edges = np.concatenate((cells[:, [0, 1]], cells[:, [1, 2]], cells[:, [2, 0]]))
        _, inverse, counts = np.unique(np.sort(edges, axis=1), axis=0, return_inverse=True, return_counts=True)
        directions = np.zeros(len(counts), dtype=np.int64)
        np.add.at(directions, inverse, np.where(edges[:, 0] < edges[:, 1], 1, -1))
        if np.any(counts != 2) or np.any(directions != 0):
            continue
        # 平移到实体自身的节点，减少远离坐标原点时的数值误差。
        local = points[cells] - points[cells[0, 0]]
        cross = np.cross(local[:, 1] - local[:, 0], local[:, 2] - local[:, 0])
        if np.any(np.linalg.norm(cross, axis=1) == 0):
            continue
        terms = np.einsum("ij,ij->i", local[:, 0], cross) / 6
        volume = abs(float(np.sum(terms)))
        if np.isfinite(volume) and volume > np.sum(np.abs(terms)) * np.finfo(float).eps * 64:
            volumes[int(body)] = volume
    return volumes


def integrate_face(points):
    if len(points) == 3:
        vector = np.cross(points[1] - points[0], points[2] - points[0]) / 2
        return float(np.linalg.norm(vector)), vector
    area = 0.0
    vector = np.zeros(3)
    for u in (-1 / np.sqrt(3), 1 / np.sqrt(3)):
        for v in (-1 / np.sqrt(3), 1 / np.sqrt(3)):
            du = np.array([-(1-v), 1-v, 1+v, -(1+v)]) / 4
            dv = np.array([-(1-u), -(1+u), 1+u, 1-u]) / 4
            cross = np.cross(du @ points, dv @ points)
            if np.linalg.norm(cross) == 0:
                raise ValueError("表面存在零面积积分位置")
            if np.linalg.norm(vector) and np.dot(vector, cross) <= 0:
                raise ValueError("表面发生自交或无法确定法向")
            vector += cross
            area += np.linalg.norm(cross)
    return float(area), vector


class SurfaceModel:
    def __init__(self, deck: Deck):
        self.deck = deck
        self.node_ids = np.array(deck.mesh.node_order, dtype=np.int64)
        self.points = np.array([deck.mesh.nodes[int(n)] for n in self.node_ids], dtype=np.float64)
        if not np.isfinite(self.points).all():
            raise ValueError("节点坐标包含非有限数值")
        node_index = {int(n): i for i, n in enumerate(self.node_ids)}
        elements = deck.mesh.elements
        unsupported = sorted({e[1] for e in elements} - FACE_NODES.keys())
        if unsupported:
            raise ValueError(f"尚未验证这些 Marc 单元类型的面号：{unsupported}；支持 7、134、136")
        shared = {}
        interior = set()
        rows, cols = [], []
        centers = []
        for cell, (element_id, kind, nodes) in enumerate(elements):
            conn = [node_index[n] for n in nodes]
            centers.append(self.points[list(set(conn))].mean(axis=0))
            for local, indices in enumerate(FACE_NODES[kind], 1):
                face = list(dict.fromkeys(conn[i] for i in indices))
                if len(face) < 3:
                    continue
                key = tuple(sorted(face))
                if key in interior:
                    raise ValueError(f"单元 {element_id} 的表面被三个以上单元共享")
                if key in shared:
                    other = shared.pop(key)
                    rows.append(other[0])
                    cols.append(cell)
                    interior.add(key)
                else:
                    shared[key] = (cell, element_id, local, face)
        graph = coo_matrix((np.ones(len(rows)), (rows, cols)), shape=(len(elements), len(elements))).tocsr()
        _, cell_bodies = connected_components(graph, directed=False)
        records = list(shared.values())
        self.faces = [record[3] for record in records]
        self.element_ids = np.array([r[1] for r in records], dtype=np.int64)
        self.local_faces = np.array([r[2] for r in records], dtype=np.int32)
        self.body_ids = np.array([cell_bodies[r[0]] for r in records], dtype=np.int32)
        self.centers = np.array([self.points[face].mean(axis=0) for face in self.faces])
        areas, vectors = [], []
        for i, record in enumerate(records):
            area, vector = integrate_face(self.points[self.faces[i]])
            outward = self.centers[i] - centers[record[0]]
            alignment = np.dot(vector, outward)
            if area <= 0 or abs(alignment) <= area * np.linalg.norm(outward) * 1e-10:
                raise ValueError(f"单元 {record[1]} 的第 {record[2]} 面无法确定方向")
            if alignment < 0:
                vector = -vector
                self.faces[i].reverse()
            areas.append(area)
            vectors.append(vector)
        self.areas = np.array(areas)
        self.area_vectors = np.array(vectors)
        self.normals = self.area_vectors / np.linalg.norm(self.area_vectors, axis=1)[:, None]
        edges = defaultdict(list)
        self.neighbors = [[] for _ in self.faces]
        for i, face in enumerate(self.faces):
            for a, b in zip(face, face[1:] + face[:1]):
                edges[(int(self.body_ids[i]), min(a, b), max(a, b))].append(i)
        for adjacent in edges.values():
            if len(adjacent) == 2:
                a, b = adjacent
                self.neighbors[a].append(b)
                self.neighbors[b].append(a)
        self.edges = np.array([(a, b) for _, a, b in edges], dtype=np.uint32)
        self.edge_bodies = np.array([body for body, _, _ in edges], dtype=np.int32)
        self.body_names = {int(i): f"实体 {int(i) + 1}" for i in np.unique(self.body_ids)}
        self.triangles, self.triangle_faces = [], []
        for i, face in enumerate(self.faces):
            for k in range(1, len(face) - 1):
                self.triangles.append((face[0], face[k], face[k+1]))
                self.triangle_faces.append(i)
        self.triangles = np.array(self.triangles, dtype=np.uint32)
        self.triangle_faces = np.array(self.triangle_faces, dtype=np.uint32)
        self.body_volumes = body_volumes(self.points, self.triangles, self.body_ids[self.triangle_faces])

    def select(self, seed: int, angle: float):
        if seed < 0 or seed >= len(self.faces) or not np.isfinite(angle) or not 0 <= angle <= 180:
            raise ValueError("起始面或角度无效")
        threshold = np.cos(np.deg2rad(angle)) - 1e-12
        selected = {seed}
        queue = deque([seed])
        while queue:
            current = queue.popleft()
            for neighbor in self.neighbors[current]:
                if neighbor not in selected and np.dot(self.normals[current], self.normals[neighbor]) >= threshold:
                    selected.add(neighbor)
                    queue.append(neighbor)
        return sorted(selected)
