import ast
import os
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from sfmarc_workbench.server import create_app


@pytest.mark.parametrize("module, paths", [
    ("routes_face_load", {"/api/select", "/api/preview", "/api/export"}),
    ("routes_weld_paths", {"/api/weld-paths", "/api/weld-script"}),
    ("routes_thermal_cycle", {"/api/thermal", "/api/thermal/resample", "/api/thermal/export",
                              "/api/thermal/temperatures", "/api/thermal/material", "/api/thermal/create"}),
    ("routes_local_trial", {"/api/local-trial", "/api/local-trial/preview", "/api/local-trial/export", "/api/local-trial/results-script"}),
])
def test_business_routes_are_registered_once_by_their_module(module, paths):
    app = create_app("module-routes")
    selected = [route for route in app.routes if route.path in paths]
    assert len(selected) == len(paths)
    assert {route.path for route in selected} == paths
    assert all(route.endpoint.__module__ == f"sfmarc_workbench.{module}" for route in selected)
    assert all(route.methods == {"POST"} for route in selected)


def test_thermal_routes_reject_recorded_temperatures_without_full_cooling():
    variables = ("SFMARC_TEST_TC_DAT", "SFMARC_TEST_TC_CSV", "SFMARC_TEST_TC_MATERIAL")
    if not all(os.environ.get(name) for name in variables):
        pytest.skip("需要实际 DAT、冷却过程不完整的匹配温度 CSV 和材料文件")
    source, temperatures, material = [Path(os.environ[name]) for name in variables]
    app = create_app("thermal-routes")
    headers = {"X-Session-Token": "thermal-routes"}
    with TestClient(app, base_url="http://127.0.0.1:8765", headers=headers) as client:
        response = client.post("/api/model", data={"root": source.name},
                               files={"files": (source.name, source.read_bytes())})
        assert response.status_code == 200, response.text
        client.headers["X-Model-Id"] = response.json()["id"]
        response = client.post("/api/thermal", json={"length_unit": "m"})
        assert response.status_code == 200, response.text
        selection = {"sampling_id": response.json()["sampling_id"]}
        response = client.post("/api/thermal/export", json=selection)
        assert response.status_code == 200, response.text
        ast.parse(response.text)
        with temperatures.open("rb") as stream:
            response = client.post("/api/thermal/temperatures", data=selection,
                                   files={"file": (temperatures.name, stream, "text/csv")})
        assert response.status_code == 200, response.text
        assert response.json()["rows"] > 0
        response = client.post("/api/thermal/material", files={"file": (material.name, material.read_bytes())})
        assert response.status_code == 200, response.text
        response = client.post("/api/thermal/create", json={**selection, "room_c": 25,
                                                           "melting_c": response.json()["melting_c"]})
        assert response.status_code == 400, response.text
        assert "温度记录未覆盖本条焊路及其冷却过程" in response.json()["detail"]
        app.state.thermal.close()
