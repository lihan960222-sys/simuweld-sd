import json
import os
from pathlib import Path

from sfmarc_workbench.deck import Deck
from sfmarc_workbench.geometry import SurfaceModel

path = Path(os.environ["SFMARC_TEST_DAT"])
model = SurfaceModel(Deck({path.name: path.read_bytes()}, path.name))
data = {"points": model.points.ravel().tolist(), "triangles": model.triangles.ravel().tolist(),
        "triangle_faces": model.triangle_faces.tolist(), "body_ids": model.body_ids.tolist()}
Path("work").mkdir(exist_ok=True)
Path("work/viewer-mesh.json").write_text(json.dumps(data), encoding="utf-8")
