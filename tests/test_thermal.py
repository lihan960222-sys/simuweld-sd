import ast
import csv
import os
from hashlib import sha256
from io import StringIO
from pathlib import Path
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient
import numpy as np
import pytest

from sfmarc_workbench.deck import Deck
from sfmarc_workbench.geometry import SurfaceModel
from sfmarc_workbench.server import create_app
from sfmarc_workbench.thermal import ThermalConversion, material_temperature
from sfmarc_workbench.thermal_deck import read_weld_paths


SOURCE = Path(os.environ.get("SFMARC_TEST_TC_DAT", "work/thermal-sample/C0002.dat"))
TEMPERATURES = Path(os.environ.get("SFMARC_TEST_TC_CSV", str(SOURCE.with_name("C0002_node_temperatures.csv"))))
MATERIAL = Path(os.environ.get("SFMARC_TEST_TC_MATERIAL", "work/thermal-sample/material.xmt"))
RECORDED_NODES = Path(os.environ.get("SFMARC_TEST_TC_RECORDED_NODES", "work/thermal-sample/C0002_temperature_nodes.csv"))
pytestmark = pytest.mark.skipif(not all(path.is_file() for path in (SOURCE, TEMPERATURES, MATERIAL)),
                                reason="需要实际 C0002 DAT、温度 CSV 与材料文件")


@pytest.fixture(scope="module")
def conversion():
    if not RECORDED_NODES.is_file():
        pytest.skip("原有流程回归需要实际已记录的节点清单")
    model = SurfaceModel(Deck({SOURCE.name: SOURCE.read_bytes()}, SOURCE.name))
    result = ThermalConversion(model, "m")
    result.selected = {}
    with RECORDED_NODES.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            node = int(row["SolverNodeID"])
            result.selected[row["Component"], node] = dict(component=row["Component"], node_id=node,
                xyz_mm=result.points[result.node_index[node]].tolist(),
                paths={int(tag.split(":")[0]) for tag in row["SampleTags"].split(";")})
    for path, node in zip(result.plan["paths"], [45318, 21699, 22310, 24430]):
        path["representative_node"] = node
        path["representative_component"] = next(name for name, current in result.selected if current == node)
        points = np.asarray(path["points_mm"])
        vectors = np.diff(points, axis=0)
        lengths = np.linalg.norm(vectors, axis=1)
        normal = result.surfaces[path["representative_component"]].normal
        relative = result.points[result.node_index[node]] - points[:-1]
        relative -= np.outer(relative @ normal, normal)
        projected = vectors - np.outer(vectors @ normal, normal)
        parameters = np.clip(np.einsum("ij,ij->i", relative, projected) / np.einsum("ij,ij->i", projected, projected), 0, 1)
        errors = np.linalg.norm(relative - parameters[:, None] * projected, axis=1)
        arc = np.r_[0., np.cumsum(lengths)]
        positions = arc[:-1] + parameters * lengths
        index = min(np.flatnonzero(errors <= errors.min() + 1e-7), key=lambda item: abs(positions[item] - arc[-1] / 2))
        path["arrival_s"] = path["start_s"] + float(positions[index]) / path["speed_mm_s"]
    return result


def load_script(code):
    ast.parse(code)
    namespace = {"__name__": "generated_script"}
    exec(compile(code, "generated_script.py", "exec"), namespace)
    namespace["validate_config"](namespace["CONFIG"])
    return namespace


def test_existing_recorded_node_set_regression(conversion, tmp_path):
    original_digest = sha256(SOURCE.read_bytes()).hexdigest()
    paths = conversion.plan["paths"]
    assert [path["id"] for path in paths] == [2900000, 2900001, 2900002, 2900003]
    assert [len(path["points_mm"]) for path in paths] == [269, 70, 93, 120]
    assert len(conversion.selected) == 398
    assert [path["representative_node"] for path in paths] == [45318, 21699, 22310, 24430]
    assert conversion.plan["end_s"] == pytest.approx(2.6)
    assert conversion.plan["room_c"] == pytest.approx(25)
    assert all(key[1] in conversion.model.deck.mesh.nodes for key in conversion.selected)
    with TEMPERATURES.open(encoding="utf-8-sig", newline="") as stream:
        with pytest.raises(ValueError, match="导出脚本"):
            conversion.import_temperatures(stream)
    exporter = load_script(conversion.export_script())
    assert sum(len(group["node_ids"]) for group in exporter["CONFIG"]["groups"]) == 398
    assert exporter["build_increment_times"]([0, 1, 2], [0, 0, .001]) == {0: 0, 1: 0, 2: .001}
    with pytest.raises(ValueError, match="重复"):
        exporter["build_increment_times"]([1, 1], [0, .001])
    with TEMPERATURES.open(encoding="utf-8-sig", newline="") as stream:
        summary = conversion.import_temperatures(stream)
    assert summary["rows"] == 1278774
    assert summary["increments"] == 3212
    assert summary["nodes"] == 398
    assert summary["end_s"] == pytest.approx(2.510305881500244)
    assert [item["complete"] for item in summary["coverage"]] == [True, True, True, False]
    material = material_temperature(MATERIAL.read_bytes())
    assert material["melting_c"] == 1082
    with pytest.raises(ValueError, match="未覆盖"):
        conversion.create_script(25, material["melting_c"], 2, 5, False)
    creator = load_script(conversion.create_script(25, material["melting_c"], 2, 5, True))
    config = creator["CONFIG"]
    assert config["target_process"] == "C0002_TC"
    assert [path["segment_count"] for path in config["paths"]] == [103, 20, 36, 52]
    assert "未覆盖" in config["coverage_note"]
    files = creator["write_import_files"](config, tmp_path)
    times, ids, temperatures = conversion.temperatures
    for path in config["paths"]:
        cycle = ET.parse(files[path["id"]]).getroot().find("WeldingParameters/thermalCycle")
        assert cycle.find("fullCycle").get("enable") == "false"
        assert int(cycle.findtext("count")) == path["segment_count"]
        assert cycle.findtext("subDivisionMode") == "constant"
        table = cycle.find("xyTable")
        assert table.get("xUnit") == "seconds" and table.get("yUnit") == "1/1"
        curve = np.asarray(path["curve"])
        source = times <= path["cooling_end_s"] + 1e-6
        observed = temperatures[source, ids.index(path["representative_node"])]
        reconstructed = 25 + 1057 * np.interp(times[source] - path["arrival_s"], curve[:, 0], curve[:, 1])
        assert np.abs(observed - reconstructed).max() <= 5.00001
        assert max(reconstructed) == pytest.approx(max(observed), abs=1e-5)
    assert sha256(SOURCE.read_bytes()).hexdigest() == original_digest


def test_real_csv_rejects_incomplete_increment_and_foreign_digest(conversion):
    with TEMPERATURES.open(encoding="utf-8-sig", newline="") as stream:
        rows = [next(stream) for _ in range(398)]
    with pytest.raises(ValueError, match="代表节点|采样节点|不完整"):
        conversion.import_temperatures(StringIO("".join(rows)))
    values = list(csv.DictReader(StringIO("".join(rows))))
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=[*values[0], "ModelSHA256"])
    writer.writeheader()
    writer.writerow({**values[0], "ModelSHA256": "0" * 64})
    with pytest.raises(ValueError, match="其他 DAT"):
        conversion.import_temperatures(StringIO(output.getvalue()))
    assert conversion.summary is None


def test_session_model_association_clears_conversion(conversion):
    app = create_app("thermal-integration-token")
    app.state.model = conversion.model
    app.state.model_id = "real-dat"
    app.state.thermal = conversion
    headers = {"X-Session-Token": "thermal-integration-token", "X-Model-Id": "real-dat"}
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.post("/api/thermal/export", headers={**headers, "X-Model-Id": "old"}, json={"sampling_id": conversion.sampling_id}).status_code == 409
        assert client.post("/api/model/clear", headers=headers, json={}).status_code == 200
        assert app.state.thermal is None
        assert client.post("/api/thermal/export", headers=headers, json={"sampling_id": conversion.sampling_id}).status_code == 409


def test_second_real_dat_has_data_driven_path_count():
    path = SOURCE.with_name("C0002-3.dat")
    if not path.is_file():
        pytest.skip("需要实际 C0002-3.dat")
    deck = Deck({path.name: path.read_bytes()}, path.name)
    plan = read_weld_paths(deck, "m")
    assert len(plan["paths"]) == 76
    assert plan["end_s"] == pytest.approx(50)
