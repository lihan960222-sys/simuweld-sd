import json
from pathlib import Path

import meshioplusplus as meshio
from meshioplusplus import step

from sfmarc_workbench.background import BackgroundModel


directory = Path("work/edge-feature-check")
directory.mkdir(parents=True, exist_ok=True)
path = directory / "box.stp"
mesh = meshio.box(size=(10, 20, 5), dims=(4, 4, 4))
step.write(path, meshio.extract_skin(mesh))
model = BackgroundModel(path.read_bytes(), path.name, "stp")
data = {
    "points": model.points.ravel().tolist(),
    "triangles": model.triangles.ravel().tolist(),
    "triangle_faces": model.triangle_faces.tolist(),
    "body_ids": model.body_ids.tolist(),
    "edges": model.edges.ravel().tolist(),
    "edge_bodies": model.edge_bodies.tolist(),
}
(directory / "box.json").write_text(json.dumps(data), encoding="utf-8")
