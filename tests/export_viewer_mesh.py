import json
import os
from pathlib import Path

from sfmarc_workbench.deck import Deck
from sfmarc_workbench.geometry import SurfaceModel
from sfmarc_workbench.loads import LoadSpec, preview

path = Path(os.environ["SFMARC_TEST_DAT"])
model = SurfaceModel(Deck({path.name: path.read_bytes()}, path.name))
data = {"points": model.points.ravel().tolist(), "triangles": model.triangles.ravel().tolist(),
        "triangle_faces": model.triangle_faces.tolist(), "body_ids": model.body_ids.tolist(),
        "edges": model.edges.ravel().tolist(), "edge_bodies": model.edge_bodies.tolist(),
        "faces": model.faces}
Path("work").mkdir(exist_ok=True)
Path("work/viewer-mesh.json").write_text(json.dumps(data), encoding="utf-8")
loads = [preview(model, LoadSpec(faces=faces, magnitude=1, mode="inward", cases=[1]))
         for faces in ([0], model.select(int(model.areas.argmax()), 20))]
Path("work/viewer-loads.json").write_text(json.dumps(loads), encoding="utf-8")
