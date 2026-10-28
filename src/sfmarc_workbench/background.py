from collections import defaultdict
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory

import meshioplusplus as meshio
from meshioplusplus import abaqus, nastran, step
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from .geometry import body_volumes


class BackgroundModel:
    def __init__(self, data: bytes, filename: str, file_format: str):
        readers = {"inp": abaqus.read, "bdf": nastran.read, "nas": nastran.read,
                   "stp": step.read, "step": step.read}
        if file_format not in readers:
            raise ValueError("几何背景支持 INP、BDF、NAS、STP 和 STEP")
        # 文件读取由第三方库完成；入口阻止引用未选择的本机文件。
        if file_format in ("inp", "bdf", "nas") and re.search(rb"(?im)^\s*\*?INCLUDE\b", data):
            raise ValueError("几何背景请使用包含完整网格的文件；当前入口不读取 INCLUDE")
        if file_format == "inp" and re.search(rb"(?im)^\s*\*(?:PART|INSTANCE|SYSTEM)\b", data):
            raise ValueError("INP 请导出全局坐标下的独立网格；当前读取器不处理 PART、INSTANCE 或 SYSTEM 变换")
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
        scratch = base / "work" / "imports"
        scratch.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=scratch) as directory:
            path = Path(directory) / ("geometry." + file_format)
            path.write_bytes(data)
            mesh = readers[file_format](path)
        if file_format in ("bdf", "nas") and np.any(mesh.point_data.get("nastran:ref", 0)):
            raise ValueError("BDF 请导出全局坐标下的网格；当前读取器不转换 GRID 引用的局部坐标系")
        self.filename = filename
        self.element_count = sum(len(block.data) for block in mesh.cells)
        dimensions = {block.dim for block in mesh.cells}
        if not dimensions or max(dimensions) < 2:
            raise ValueError("模型没有可显示的表面或体积单元")
        volume = [block for block in mesh.cells if block.dim == 3]
        shells = [block for block in mesh.cells if block.dim == 2]
        surfaces = []
        if volume:
            surfaces.append(meshio.extract_skin(meshio.Mesh(mesh.points, volume), linearize=True))
        if shells:
            surfaces.append(meshio.Mesh(mesh.points, shells))
        surface = meshio.merge(surfaces, source_tag=False) if len(surfaces) > 1 else surfaces[0]
        self.points = np.asarray(surface.points, dtype=np.float64)
        if self.points.ndim != 2 or self.points.shape[1] != 3 or not np.isfinite(self.points).all():
            raise ValueError("模型坐标必须为有限的三维数值")
        self.faces = []
        for block in surface.cells:
            if block.type.startswith("triangle"):
                self.faces.extend(block.data[:, :3].tolist())
            elif block.type.startswith("quad"):
                self.faces.extend(block.data[:, :4].tolist())
            else:
                raise ValueError(f"当前显示尚不支持单元：{block.type}")
        if not self.faces:
            raise ValueError("模型没有可显示的表面")
        edge_faces = defaultdict(list)
        for index, face in enumerate(self.faces):
            for a, b in zip(face, face[1:] + face[:1]):
                edge_faces[tuple(sorted((a, b)))].append(index)
        rows, columns = [], []
        for faces in edge_faces.values():
            rows.extend([faces[0]] * (len(faces) - 1))
            columns.extend(faces[1:])
        graph = coo_matrix((np.ones(len(rows)), (rows, columns)), shape=(len(self.faces), len(self.faces))).tocsr()
        _, self.body_ids = connected_components(graph, directed=False)
        self.body_names = {int(body): f"实体 {int(body) + 1}" for body in np.unique(self.body_ids)}
        self.edges = np.array(list(edge_faces), dtype=np.uint32)
        self.edge_bodies = np.array([self.body_ids[faces[0]] for faces in edge_faces.values()])
        triangles, triangle_faces = [], []
        for index, face in enumerate(self.faces):
            for vertex in range(1, len(face) - 1):
                triangles.append((face[0], face[vertex], face[vertex + 1]))
                triangle_faces.append(index)
        self.triangles = np.array(triangles, dtype=np.uint32)
        self.triangle_faces = np.array(triangle_faces, dtype=np.uint32)
        self.body_volumes = body_volumes(self.points, self.triangles, self.body_ids[self.triangle_faces])
        self.length_unit = "mm" if file_format in ("stp", "step") else None
