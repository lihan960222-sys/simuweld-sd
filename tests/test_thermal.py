import ast
import csv
import os
from hashlib import sha256
from io import StringIO
from pathlib import Path

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
    model = SurfaceModel(Deck({SOURCE.name: SOURCE.read_bytes()}, SOURCE.name))
    result = ThermalConversion(model, "m")
    yield result
    result.close()


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
    assert conversion.selected
    assert conversion.plan["end_s"] == pytest.approx(2.6)
    assert conversion.plan["room_c"] == pytest.approx(25)
    assert all(key[1] in conversion.model.deck.mesh.nodes for key in conversion.selected)
    with TEMPERATURES.open(encoding="utf-8-sig", newline="") as stream:
        with pytest.raises(ValueError, match="导出脚本"):
            conversion.import_temperatures(stream)
    exporter = load_script(conversion.export_script())
    assert sum(len(group["node_ids"]) for group in exporter["CONFIG"]["groups"]) == len(conversion.selected)
    assert exporter["build_increment_times"]([0, 1, 2], [0, 0, .001]) == {0: 0, 1: 0, 2: .001}
    with pytest.raises(ValueError, match="重复"):
        exporter["build_increment_times"]([1, 1], [0, .001])
    with TEMPERATURES.open(encoding="utf-8-sig", newline="") as stream:
        with pytest.raises(ValueError, match="节点集合不一致"):
            conversion.import_temperatures(stream)
    assert conversion.summary is None and conversion.temperatures is None
    assert conversion._temperature_file is None
    material = material_temperature(MATERIAL.read_bytes())
    assert material["melting_c"] == 1082
    with pytest.raises(ValueError, match="导入温度"):
        conversion.create_script(25, material["melting_c"])
    assert sha256(SOURCE.read_bytes()).hexdigest() == original_digest


def test_real_csv_rejects_incomplete_increment_and_foreign_digest(conversion):
    with TEMPERATURES.open(encoding="utf-8-sig", newline="") as stream:
        rows = [next(stream) for _ in range(398)]
    with pytest.raises(ValueError, match="节点集合|不完整"):
        conversion.import_temperatures(StringIO("".join(rows)))
    values = list(csv.DictReader(StringIO("".join(rows))))
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=[*values[0], "ModelSHA256"])
    writer.writeheader()
    writer.writerow({**values[0], "ModelSHA256": "0" * 64})
    with pytest.raises(ValueError, match="其他 DAT"):
        conversion.import_temperatures(StringIO(output.getvalue()))
    assert conversion.summary is None


def test_recorded_csv_reader_preserves_real_histories(conversion):
    if not RECORDED_NODES.is_file():
        pytest.skip("需要实际温度导出的节点清单")
    with RECORDED_NODES.open(encoding="utf-8-sig", newline="") as stream:
        recorded = list(csv.DictReader(stream))
    selected = {}
    for row in recorded:
        node = int(row["SolverNodeID"])
        components = [name for name, ids in conversion.components.items() if node in ids]
        assert len(components) == 1
        selected[components[0], node] = row
    current = conversion.selected
    conversion.selected = selected
    try:
        with TEMPERATURES.open(encoding="utf-8-sig", newline="") as stream:
            summary = conversion.read_temperatures(stream)
        times, keys, values = conversion.temperatures
        assert summary["nodes"] == len(recorded) == 398
        assert summary["rows"] == 1278774
        assert summary["increments"] == 3212
        assert keys == sorted(selected)
        assert isinstance(values, np.memmap)
        expected = {}
        columns = {node: index for index, (_, node) in enumerate(keys) if node in (45318, 21699, 22310)}
        assert len(columns) == 3
        with TEMPERATURES.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                node = int(row["SolverNodeID"])
                if node in columns:
                    expected.setdefault(float(row["Time_s"]), {})[node] = float(row["Temperature_C"])
        np.testing.assert_array_equal(times, sorted(expected))
        for node, column in columns.items():
            np.testing.assert_array_equal(values[:, column], [expected[time][node] for time in times])
    finally:
        conversion.clear_temperatures()
        conversion.selected = current
    assert conversion._temperature_file is None


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
