import ast
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from sfmarc_workbench.deck import Deck
from sfmarc_workbench.geometry import SurfaceModel
from sfmarc_workbench.server import create_app


SOURCE = Path(os.environ.get("SFMARC_TEST_TC_DAT", "work/thermal-sample/C0002.dat"))
pytestmark = pytest.mark.skipif(not SOURCE.is_file(), reason="需要真实 C0002.dat")


def test_sampling_revision_export_and_invalid_parameters():
    app = create_app("sampling-test")
    app.state.model = SurfaceModel(Deck({SOURCE.name: SOURCE.read_bytes()}, SOURCE.name))
    app.state.model_id = "c0002"
    headers = {"X-Session-Token": "sampling-test", "X-Model-Id": "c0002"}
    with TestClient(app, base_url="http://127.0.0.1:8765", headers=headers) as client:
        response = client.post("/api/thermal", json={"length_unit": "m"})
        assert response.status_code == 200, response.text
        initial = response.json()
        identity = {"sampling_id": initial["sampling_id"]}
        response = client.post("/api/thermal/export", json=identity)
        assert response.status_code == 200, response.text
        config = ast.literal_eval(ast.parse(response.text).body[0].value)
        exported = {(group["name"], node) for group in config["groups"] for node in group["node_ids"]}
        assert exported == {(node["component"], node["node_id"]) for node in initial["nodes"]}
        response = client.post("/api/thermal/resample", json={**identity, "sampling": {"spacing_mm": 4, "surface": "lower"}})
        assert response.status_code == 200, response.text
        updated = response.json()
        assert updated["sampling_id"] != initial["sampling_id"]
        assert not app.state.thermal.exported
        assert app.state.thermal.summary is None
        assert client.post("/api/thermal/export", json=identity).status_code == 409
        assert client.post("/api/thermal/create", json={**identity, "room_c": 25, "melting_c": 1082}).status_code == 409
        identity = {"sampling_id": updated["sampling_id"]}
        assert client.post("/api/thermal/export", json=identity).status_code == 200
        response = client.post("/api/thermal/resample", json={**identity, "sampling": {"spacing_mm": 0}})
        assert response.status_code == 400
        assert not app.state.thermal.exported
        assert app.state.thermal.summary is None
        response = client.post("/api/thermal/resample", json={**identity, "sampling": {"spacing_mm": 3, "turn_angle_deg": None}})
        assert response.status_code == 200, response.text
        identity = {"sampling_id": response.json()["sampling_id"]}
        assert client.post("/api/thermal/create", json={**identity, "room_c": 25, "melting_c": 1082}).status_code == 400
        assert client.post("/api/model/clear", json={}).status_code == 200
        assert app.state.thermal is None


def test_new_model_prepares_during_previous_model_sampling():
    app = create_app("sampling-concurrent-test")
    source = SOURCE.read_bytes()
    app.state.model = SurfaceModel(Deck({SOURCE.name: source}, SOURCE.name))
    app.state.model_id = "original"
    original_model = app.state.model
    headers = {"X-Session-Token": "sampling-concurrent-test", "X-Model-Id": "original"}
    with TestClient(app, base_url="http://127.0.0.1:8765", headers=headers) as client:
        response = client.post("/api/thermal", json={"length_unit": "m"})
        assert response.status_code == 200, response.text
        sampling_id = response.json()["sampling_id"]
        with ThreadPoolExecutor(max_workers=1) as executor:
            previous = executor.submit(client.post, "/api/thermal/resample", json={
                "sampling_id": sampling_id, "sampling": {"spacing_mm": .15}})
            deadline = time.monotonic() + 10
            while app.state.thermal_sampling is None and not previous.done() and time.monotonic() < deadline:
                time.sleep(.01)
            assert app.state.thermal_sampling is not None
            assert app.state.thermal_sampling[0] is original_model
            response = client.post("/api/model", data={"root": SOURCE.name},
                                   files={"files": (SOURCE.name, source, "application/octet-stream")})
            assert response.status_code == 200, response.text
            current_headers = {"X-Model-Id": response.json()["id"]}
            assert app.state.model is not original_model
            assert not previous.done(), "需要旧模型的实际计算仍在执行，才能验证并发准备"
            response = client.post("/api/thermal", headers=current_headers, json={"length_unit": "m"})
            assert response.status_code == 200, response.text
            current = app.state.thermal
            assert current.model is app.state.model
            previous_response = previous.result(timeout=180)
            assert previous_response.status_code == 409, previous_response.text
            assert app.state.thermal is current
            assert app.state.thermal_sampling is None
            response = client.post("/api/thermal/export", headers=current_headers,
                                   json={"sampling_id": current.sampling_id})
            assert response.status_code == 200, response.text
