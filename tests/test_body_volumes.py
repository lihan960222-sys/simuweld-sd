from pathlib import Path

from fastapi.testclient import TestClient
import meshioplusplus as meshio
from meshioplusplus import abaqus
import numpy as np
import pytest

from sfmarc_workbench.background import BackgroundModel
from sfmarc_workbench.geometry import body_volumes
from sfmarc_workbench.server import create_app


@pytest.mark.parametrize("extension", ["inp", "bdf", "stp"])
def test_real_background_volume_metadata(extension):
    path = Path("work/geometry-tests") / ("two_blocks." + extension)
    with TestClient(create_app("volume"), base_url="http://127.0.0.1:8765",
                    headers={"X-Session-Token": "volume"}) as client:
        response = client.post("/api/background", files={"file": (path.name, path.read_bytes())})
        assert response.status_code == 200, response.text
        metadata = response.json()
        assert sorted(body["volume"] for body in metadata["bodies"]) == pytest.approx([24, 1000])
        assert {body["id"] for body in metadata["bodies"]} == {0, 1}
        refreshed = client.get("/api/model", headers={"X-Model-Id": metadata["id"]})
        assert refreshed.json()["bodies"] == metadata["bodies"]


def test_tetrahedron_volume_and_translation():
    mesh = meshio.Mesh(np.array([[0, 0, 0], [2, 0, 0], [0, 3, 0], [0, 0, 4]], dtype=float),
                       [("tetra", [[0, 1, 2, 3]])])
    surface = meshio.extract_skin(mesh)
    triangles = surface.cells[0].data
    bodies = np.zeros(len(triangles), dtype=int)
    for offset in (0, 1e12):
        assert body_volumes(surface.points + offset, triangles, bodies) == pytest.approx({0: 4})
        assert body_volumes(surface.points + offset, triangles[:, ::-1], bodies) == pytest.approx({0: 4})


@pytest.mark.parametrize("condition", ["open", "reversed_face", "duplicate_face", "degenerate"])
def test_unreliable_surface_volume_is_null(condition):
    surface = meshio.extract_skin(meshio.box(size=(2, 3, 4)))
    quads = surface.cells[0].data.copy()
    if condition == "open":
        quads = quads[:-1]
    elif condition == "reversed_face":
        quads[0] = quads[0, ::-1]
    elif condition == "duplicate_face":
        quads = np.concatenate([quads, quads[:1]])
    else:
        surface.points[:, 2] = 0
    path = Path("work/geometry-tests") / (condition + ".inp")
    abaqus.write(path, meshio.Mesh(surface.points, [("quad", quads)]))
    model = BackgroundModel(path.read_bytes(), path.name, "inp")
    assert model.body_volumes == {0: None}


def test_marc_volume_metadata_and_preview_face_ids(model):
    node_index = {int(node): index for index, node in enumerate(model.node_ids)}
    cells = [[node_index[node] for node in element[2]] for element in model.deck.mesh.elements]
    expected = meshio.compute_stats(meshio.Mesh(model.points, [("hexahedron", cells)]))["unsigned_volume"]
    assert all(value is not None and value > 0 for value in model.body_volumes.values())
    assert sum(model.body_volumes.values()) == pytest.approx(expected)
    with TestClient(create_app("volume"), base_url="http://127.0.0.1:8765",
                    headers={"X-Session-Token": "volume"}) as client:
        response = client.post("/api/model", data={"root": model.deck.root},
                               files=[("files", (name, data)) for name, data in model.deck.files.items()])
        assert response.status_code == 200, response.text
        metadata = response.json()
        assert {body["id"]: body["volume"] for body in metadata["bodies"]} == model.body_volumes
        faces = model.select(0, 20)
        response = client.post("/api/preview", headers={"X-Model-Id": metadata["id"]},
                               json={"faces": faces, "magnitude": 2, "mode": "inward", "cases": [1]})
        assert response.status_code == 200, response.text
        preview = response.json()
        assert set(preview["face_ids"]) <= set(faces)
        np.testing.assert_allclose(preview["centers"], model.centers[preview["face_ids"]])
        assert len(preview["face_ids"]) == len(preview["directions"])


def test_clear_imported_model_preserves_source_and_rejects_stale_requests():
    path = Path("work/geometry-tests/two_blocks.inp")
    original = path.read_bytes()
    with TestClient(create_app("volume"), base_url="http://127.0.0.1:8765",
                    headers={"X-Session-Token": "volume"}) as client:
        response = client.post("/api/background", files={"file": (path.name, original)})
        assert response.status_code == 200, response.text
        headers = {"X-Model-Id": response.json()["id"]}
        assert client.post("/api/model/clear", headers={"X-Model-Id": "stale"}).status_code == 409
        assert client.get("/api/model", headers=headers).status_code == 200
        assert client.post("/api/model/clear", headers={**headers, "X-Session-Token": "invalid"}).status_code == 401
        response = client.post("/api/model/clear", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json() == {"cleared": True}
        assert client.get("/api/model", headers=headers).status_code == 409
        assert client.get("/api/mesh", headers=headers).status_code == 409
        assert path.read_bytes() == original
        response = client.post("/api/background", files={"file": (path.name, original)})
        assert response.status_code == 200, response.text
        assert response.json()["id"] != headers["X-Model-Id"]
        assert client.post("/api/model/clear", headers=headers).status_code == 409
