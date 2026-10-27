import ast
import os
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from sfmarc_workbench.server import create_app

HEAT = {"power_w": 35, "velocity": 120, "efficiency": 0.8,
        "conical_upper_radius": 0.18, "conical_lower_radius": 0.08, "conical_depth": 0.18,
        "conical_gaussian_parameter": 1, "volume_heat_fraction": 0.8,
        "disc_radius": 0.18, "surface_depth": 0.1, "disc_gaussian_parameter": 1}

@pytest.fixture
def client():
    with TestClient(create_app("welding-test"), base_url="http://127.0.0.1:8765",
                    headers={"X-Session-Token": "welding-test"}) as value:
        yield value


def test_import_reference_points_and_generate_independent_script(client):
    if not os.environ.get("SFMARC_TEST_WELD_CSV"):
        pytest.skip("需要本机参考 nodes.csv，设置 SFMARC_TEST_WELD_CSV 后执行")
    source = Path(os.environ["SFMARC_TEST_WELD_CSV"])
    response = client.post("/api/weld-paths", data={"unit": "mm"},
                           files=[("files", (source.name, source.read_bytes(), "text/csv"))])
    assert response.status_code == 200, response.text
    path = response.json()["paths"][0]
    assert len(path["points"]) == 295
    assert path["points"][13]["order"] == 14
    assert path["points"][13]["node_id"] == "456744"
    assert path["points"][281]["node_id"] == "456456"
    assert path["points"][281]["xyz"][2] == 992.549
    points = [point["xyz"] for point in path["points"][13:282]][::-1]
    exported = client.post("/api/weld-script", json={
        "process": "Process-1", "robot": "Robot-1", "heats": [{"name": "Laser-1", "parameters": HEAT}],
        "orientation": [0, 0, 2], "paths": [
            {"name": "完整焊路", "points": points, "pause_after": 2.5, "heat_source": "Laser-1"},
            {"name": "反向焊路", "points": points[::-1], "pause_after": 0, "heat_source": "Laser-1"},
        ],
    })
    assert exported.status_code == 200, exported.text
    namespace = {"__name__": "generated_script_test"}
    exec(compile(exported.text, "generate_weld_paths.py", "exec"), namespace)
    namespace["validate_plan"]()
    assert namespace["ORIENTATION"] == (0.0, 0.0, 1.0)
    assert [row["pause_after"] for row in namespace["PATHS"]] == [2.5, 0]
    assert namespace["PATHS"][0]["points"] == points
    assert namespace["PATHS"][1]["points"] == points[::-1]
    assert namespace["PATHS"][0]["length_mm"] == pytest.approx(205.4898146957904)
    ast.parse(exported.text)
    namespace["PATHS"][0]["pause_after"] = -1
    with pytest.raises(ValueError, match="停止时间"):
        namespace["validate_plan"]()


def test_grouping_order_units_and_multiple_files(client):
    content = "PathID,Order,NodeID,X,Y,Z\nB,2,b2,0.002,0,0\nA,2,a2,0,0.003,0\nB,1,b1,0,0,0\nA,1,a1,0,0,0\n"
    result = client.post("/api/weld-paths", data={"unit": "m"}, files=[
        ("files", ("paths.csv", content.encode("utf-8-sig"), "text/csv")),
        ("files", ("second.csv", b"Index,X,Y,Z\n1,0,0,0\n2,0.001,0,0\n", "text/csv")),
    ])
    assert result.status_code == 200, result.text
    paths = result.json()["paths"]
    assert [path["name"] for path in paths] == ["paths / B", "paths / A", "second"]
    assert paths[0]["points"][1]["xyz"] == [2, 0, 0]
    assert paths[1]["points"][1]["xyz"] == [0, 3, 0]
    assert [point["order"] for point in paths[0]["points"]] == [1, 2]
    assert paths[2]["points"][1]["xyz"] == [1, 0, 0]


@pytest.mark.parametrize("content", [
    "Order,X,Y,Z\n1,0,0,0\n1,1,0,0\n",
    "Order,X,Y,Z\n1,0,0,0\n2,nan,0,0\n",
    "Order,X,Y,Z\n1,0,0,0\n2,0,0,0\n",
    "Order,X,Y,Z\n1,0,0,0\n",
    "X,Y,Z\n0,0,0\n1,0,0\n",
    "Order,X,Y,Z\n1,0,0,0,extra\n2,1,0,0\n",
    "Order,X,Y,Z\n1,0,0,0\n2,,0,0\n",
    "Order,X,Y,Z,X\n1,0,0,0,0\n2,1,0,0,1\n",
    'Order,X,Y,Z\n1,0,0,0\n2,"1,0,0\n',
])
def test_rejects_ambiguous_or_invalid_csv(client, content):
    response = client.post("/api/weld-paths", data={"unit": "mm"},
                           files=[("files", ("invalid.csv", content.encode(), "text/csv"))])
    assert response.status_code == 400, response.text


def test_script_rejects_invalid_plan_and_escapes_names(client):
    payload = {"process": "Process-1", "robot": "Robot-1", "heats": [{"name": "Laser-1", "parameters": HEAT}],
               "orientation": [0, 0, 1],
               "paths": [{"name": "引号'路径", "points": [[0, 0, 0], [1, 0, 0]], "pause_after": 3, "heat_source": "Laser-1"}]}
    result = client.post("/api/weld-script", json=payload)
    assert result.status_code == 200, result.text
    namespace = {"__name__": "generated_script_test"}
    exec(compile(result.text, "paths.py", "exec"), namespace)
    namespace["validate_plan"]()
    assert namespace["PATHS"][0]["name"] == "引号'路径"
    payload["paths"][0]["pause_after"] = -1
    assert client.post("/api/weld-script", json=payload).status_code == 422
    payload["paths"][0]["pause_after"] = 0
    payload["orientation"] = [0, 0, 0]
    assert client.post("/api/weld-script", json=payload).status_code == 422
    payload["orientation"] = [0, 0, 1]
    payload["paths"].append(payload["paths"][0].copy())
    assert client.post("/api/weld-script", json=payload).status_code == 422


def test_welding_api_uses_session_guard(client):
    response = client.post("/api/weld-script", headers={"X-Session-Token": "wrong"}, json={})
    assert response.status_code == 401


def test_heat_parameters_are_exported_and_validated(client):
    payload = {"process": "Process-1", "robot": "Robot-1", "heats": [{"name": "Web-Laser", "parameters": dict(HEAT)}],
               "paths": [{"name": "path", "points": [[0, 0, 0], [10, 0, 0]], "pause_after": 0.2, "heat_source": "Web-Laser"}]}
    response = client.post("/api/weld-script", json=payload)
    assert response.status_code == 200, response.text
    namespace = {"__name__": "generated_script_test"}
    exec(compile(response.text, "heat.py", "exec"), namespace)
    namespace["validate_plan"]()
    assert namespace["HEATS"] == {"Web-Laser": HEAT}
    payload["heats"][0]["parameters"]["efficiency"] = 0
    assert client.post("/api/weld-script", json=payload).status_code == 200
    namespace["HEATS"]["Web-Laser"]["efficiency"] = 5
    with pytest.raises(ValueError, match="热源"):
        namespace["validate_plan"]()
    for key, value in (("efficiency", 1.1), ("power_w", 0), ("velocity", -2), ("conical_depth", 0),
                       ("conical_gaussian_parameter", 4), ("volume_heat_fraction", -1)):
        payload["heats"][0]["parameters"] = {**HEAT, key: value}
        assert client.post("/api/weld-script", json=payload).status_code == 422


def test_multiple_heat_profiles_follow_each_path_and_reject_missing_references(client):
    heats = [{"name": "Laser-A", "parameters": dict(HEAT)},
             {"name": "Laser-B", "parameters": {**HEAT, "power_w": 250, "velocity": 25, "conical_depth": 0.8}}]
    payload = {"process": "Process-1", "robot": "Robot-1", "heats": heats, "paths": [
        {"name": "焊路-A", "points": [[0, 0, 0], [10, 0, 0]], "pause_after": 2, "heat_source": "Laser-A"},
        {"name": "焊路-B", "points": [[10, 0, 0], [20, 0, 0]], "pause_after": 3, "heat_source": "Laser-B"},
    ]}
    response = client.post("/api/weld-script", json=payload)
    assert response.status_code == 200, response.text
    namespace = {"__name__": "generated_script_test"}
    exec(compile(response.text, "multiple_heats.py", "exec"), namespace)
    namespace["validate_plan"]()
    assert namespace["HEATS"] == {profile["name"]: profile["parameters"] for profile in heats}
    assert [path["heat_source"] for path in namespace["PATHS"]] == ["Laser-A", "Laser-B"]
    assert namespace["HEATS"]["Laser-A"]["velocity"] == 120
    assert namespace["HEATS"]["Laser-B"]["velocity"] == 25
    assert [path["pause_after"] for path in namespace["PATHS"]] == [2, 3]
    del namespace["HEATS"]["Laser-B"]["power_w"]
    with pytest.raises(ValueError, match="热源参数"):
        namespace["validate_plan"]()
    namespace["HEATS"]["Laser-B"]["power_w"] = 250
    namespace["PATHS"][1]["heat_source"] = "missing"
    with pytest.raises(ValueError, match="热源"):
        namespace["validate_plan"]()
    payload["paths"][1]["heat_source"] = "missing"
    assert client.post("/api/weld-script", json=payload).status_code == 422
    payload["paths"][1]["heat_source"] = "Laser-B"
    payload["heats"][1]["name"] = "Laser-A"
    assert client.post("/api/weld-script", json=payload).status_code == 422
    payload["heats"] = []
    assert client.post("/api/weld-script", json=payload).status_code == 422


def test_reference_weldid_pointid_columns(client):
    filename = Path("D:/Work_directory/simufact_welding/weld_automation/weld_lines_5points.csv")
    if not filename.exists():
        pytest.skip("需要用户提供的 weld_lines_5points.csv")
    response = client.post("/api/weld-paths", data={"unit": "mm"}, files={"files": (filename.name, filename.read_bytes())})
    assert response.status_code == 200, response.text
    paths = response.json()["paths"]
    assert paths[0]["name"].endswith("W001")
    assert len(paths[0]["points"]) == 5
    assert paths[0]["points"][0]["xyz"] == [-46.382, 49.54, 155.752]
