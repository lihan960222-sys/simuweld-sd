from pathlib import Path

from fastapi.testclient import TestClient
import meshioplusplus as meshio
from meshioplusplus import abaqus, nastran, step
import numpy as np
import pytest

from sfmarc_workbench.server import create_app


@pytest.mark.parametrize("extension,module", [("inp", abaqus), ("bdf", nastran), ("stp", step)])
def test_background_geometry_and_load_rejection(extension, module):
    directory = Path("work/geometry-tests")
    directory.mkdir(parents=True, exist_ok=True)
    filename = directory / ("two_blocks." + extension)
    first = meshio.box(size=(10, 20, 5), dims=(2, 1, 1))
    second = meshio.box(size=(2, 3, 4), origin=(30, 0, 0))
    mesh = meshio.merge([first, second])
    module.write(filename, meshio.extract_skin(mesh) if extension == "stp" else mesh)
    with TestClient(create_app("geometry"), base_url="http://127.0.0.1:8765",
                    headers={"X-Session-Token": "geometry"}) as client:
        response = client.post("/api/background", files={"file": (filename.name, filename.read_bytes())})
        assert response.status_code == 200, response.text
        metadata = response.json()
        assert metadata["supports_loads"] is False
        assert len(metadata["bodies"]) == 2
        headers = {"X-Model-Id": metadata["id"]}
        data = client.get("/api/mesh", headers=headers).json()
        points = np.array(data["points"]).reshape(-1, 3)
        np.testing.assert_allclose(points.min(axis=0), [0, 0, 0])
        np.testing.assert_allclose(points.max(axis=0), [32, 20, 5])
        if extension != "stp":
            assert metadata["faces"] == 16
            assert len(data["triangles"]) // 3 == 32
            assert len(data["edges"]) // 2 == 32
        assert client.post("/api/select", headers=headers, json={"seed": 0, "angle": 20}).status_code == 409


def test_external_include_is_rejected_before_reading():
    with TestClient(create_app("geometry"), base_url="http://127.0.0.1:8765",
                    headers={"X-Session-Token": "geometry"}) as client:
        response = client.post("/api/background", files={"file": ("external.inp", b"*INCLUDE, INPUT=C:/secret.inp\n")})
        assert response.status_code == 400
        assert "INCLUDE" in response.text


def test_local_coordinate_metadata_requires_conversion():
    from sfmarc_workbench.background import BackgroundModel

    mesh = meshio.box()
    mesh.point_data["nastran:ref"] = np.ones(len(mesh.points), dtype=np.int64)
    filename = Path("work/geometry-tests/local_coordinates.bdf")
    nastran.write(filename, mesh)
    with pytest.raises(ValueError, match="局部坐标系"):
        BackgroundModel(filename.read_bytes(), filename.name, "bdf")
    with pytest.raises(ValueError, match="INSTANCE"):
        BackgroundModel(b"*INSTANCE, NAME=Moved, PART=Block\n10,0,0\n", "assembly.inp", "inp")
