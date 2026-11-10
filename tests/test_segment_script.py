import ast
import copy
import csv
import os
from hashlib import sha256
from pathlib import Path
import runpy
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from sfmarc_workbench.deck import Deck
from sfmarc_workbench.thermal_deck import read_weld_paths


TEMPLATE = Path(__file__).parents[1] / "src/sfmarc_workbench/templates/tc_create.py"
SOURCE = Path(os.environ.get("SFMARC_TEST_TC_DAT", "work/thermal-sample/C0002.dat"))
TEMPERATURES = Path(os.environ.get("SFMARC_TEST_TC_CSV", str(SOURCE.with_name("C0002_node_temperatures.csv"))))
RECORDED = Path(os.environ.get("SFMARC_TEST_TC_RECORDED_NODES", "work/thermal-sample/C0002_temperature_nodes.csv"))


@pytest.fixture(scope="module")
def creator():
    return runpy.run_path(str(TEMPLATE))


@pytest.fixture(scope="module")
def real_config():
    if not all(path.is_file() for path in (SOURCE, TEMPERATURES, RECORDED)):
        pytest.skip("需要真实 C0002 DAT、温度 CSV 和已记录节点清单")
    plan = read_weld_paths(Deck({SOURCE.name: SOURCE.read_bytes()}, SOURCE.name), "m")
    path = copy.deepcopy(plan["paths"][1])
    points = np.asarray(path["points_mm"])
    normals = np.asarray(path["normals"])
    lengths = np.linalg.norm(np.diff(points, axis=0), axis=1)
    stations = np.r_[0., np.cumsum(lengths)]
    middle = len(points) // 2
    with RECORDED.open(encoding="utf-8-sig", newline="") as stream:
        recorded = [row for row in csv.DictReader(stream) if str(path["id"]) + ":" in row["SampleTags"]]
    segments = []
    for index, (first, last) in enumerate(((0, middle), (middle, len(points) - 1)), 1):
        positions = np.asarray([[float(row[key]) * 1000 for key in ("X_m", "Y_m", "Z_m")] for row in recorded])
        chosen = int(np.argmin(np.linalg.norm(positions - points[(first + last) // 2], axis=1)))
        row, position = recorded[chosen], positions[chosen]
        vectors = np.diff(points[first:last + 1], axis=0)
        fractions = np.clip(np.sum((position - points[first:last]) * vectors, axis=1) / np.sum(vectors * vectors, axis=1), 0, 1)
        projected = points[first:last] + fractions[:, None] * vectors
        nearest = int(np.argmin(np.linalg.norm(position - projected, axis=1)))
        station = float(stations[first + nearest] + fractions[nearest] * lengths[first + nearest])
        arrival = path["start_s"] + station / path["length_mm"] * (path["end_s"] - path["start_s"])
        sample = dict(component=row["Component"], node_id=int(row["SolverNodeID"]), xyz_mm=position.tolist(),
                      station_mm=station, arrival_s=arrival,
                      distance_mm=float(np.linalg.norm(position - projected[nearest])))
        segments.append(dict(path_id=path["id"], index=index, label=f"焊路 2，第 {index} 段",
                             start_mm=float(stations[first]), end_mm=float(stations[last]),
                             points_mm=points[first:last + 1].tolist(), normals=normals[first:last + 1].tolist(),
                             length_mm=float(stations[last] - stations[first]),
                             start_s=path["start_s"] + stations[first] / path["length_mm"] * (path["end_s"] - path["start_s"]),
                             end_s=path["start_s"] + stations[last] / path["length_mm"] * (path["end_s"] - path["start_s"]),
                             samples=[sample], radius_mm=sample["distance_mm"] + 1e-6, depth_mm=.5, curve=[]))
    by_node = {segment["samples"][0]["node_id"]: segment for segment in segments}
    with TEMPERATURES.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            segment = by_node.get(int(row["SolverNodeID"]))
            if segment is not None:
                point = [float(row["Time_s"]) - segment["samples"][0]["arrival_s"],
                         max(0., (float(row["Temperature_C"]) - 25) / (1082 - 25))]
                half_duration = (segment["end_s"] - segment["start_s"]) / 2
                if not -half_duration <= point[0] <= half_duration:
                    continue
                if segment["curve"] and point[0] == segment["curve"][-1][0]:
                    assert point == segment["curve"][-1]
                else:
                    segment["curve"].append(point)
    path["segments"] = segments
    return dict(source_process="C0002", target_process="C0002_TC", source_sha256=sha256(SOURCE.read_bytes()).hexdigest(),
                room_c=25., melting_c=1082., coverage_note="真实节点温度记录", paths=[path],
                heat_source_xml=TEMPLATE.with_name("thermal_cycle_source.xml").read_text(encoding="utf-8"))


def test_real_segments_write_independent_no_subdivision_tables(creator, real_config, tmp_path):
    creator["validate_config"](real_config)
    files = creator["write_import_files"](real_config, tmp_path)
    segments = real_config["paths"][0]["segments"]
    assert len(files) == len(segments) == 2
    for segment in segments:
        filename = files[(segment["path_id"], segment["index"])]
        assert filename.name == "{}_{:04d}.xml".format(real_config["target_process"], segment["index"])
        root = ET.parse(filename).getroot()
        cycle = root.find("WeldingParameters/thermalCycle")
        assert cycle.findtext("subDivisionMode") == "noDivision"
        assert cycle.find("count") is None
        assert cycle.find("fullCycle").get("enable") == "false"
        assert root.find("WeldingParameters/transient") is None
        assert len(cycle.find("xyTable")) == len(segment["curve"])
        last_row = next(csv.reader([cycle.find("xyTable")[-1].text], delimiter=";"))
        assert float(last_row[1]) == pytest.approx(segment["curve"][-1][1])
        assert segment["curve"][-1][1] > 0
        expected = segment["length_mm"] / (segment["end_s"] - segment["start_s"]) / 1000
        assert float(root.findtext("WeldingParameters/velocity")) == pytest.approx(expected)


def test_real_sample_is_inside_full_depth_cylinder(creator, real_config):
    segment = real_config["paths"][0]["segments"][0]
    radius, depth = creator["heat_source_dimensions"](segment, .21)
    assert radius >= segment["radius_mm"]
    assert depth >= .21
    assert creator["sample_projection"](segment, segment["samples"][0]["xyz_mm"])[1] <= depth


def test_rejects_disconnected_segment_geometry(creator, real_config):
    config = copy.deepcopy(real_config)
    config["paths"][0]["segments"][1]["points_mm"][0][0] += .1
    with pytest.raises(ValueError, match="长度|连续|几何"):
        creator["validate_config"](config)


def test_rejects_empty_segment_samples(creator, real_config):
    config = copy.deepcopy(real_config)
    config["paths"][0]["segments"][0]["samples"] = []
    with pytest.raises(ValueError, match="采样节点"):
        creator["validate_config"](config)


def test_rejects_curve_outside_segment_time(creator, real_config):
    config = copy.deepcopy(real_config)
    segment = config["paths"][0]["segments"][0]
    segment["curve"][0][0] = -(segment["end_s"] - segment["start_s"])
    with pytest.raises(ValueError, match="时间"):
        creator["validate_config"](config)


def test_rejects_temperature_history_ending_at_peak(creator, real_config):
    config = copy.deepcopy(real_config)
    segment = config["paths"][0]["segments"][0]
    peak = int(np.argmax([row[1] for row in segment["curve"]]))
    segment["curve"] = segment["curve"][:peak + 1]
    with pytest.raises(ValueError, match="峰值"):
        creator["validate_config"](config)


def test_template_assigns_independent_laser_geometry():
    tree = ast.parse(TEMPLATE.read_text(encoding="utf-8"))
    assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
               and node.func.id == "HeatSourceLaser" for node in ast.walk(tree))
    assert not any(isinstance(node, ast.Attribute) and node.attr == "name"
                   and isinstance(node.value, ast.Attribute) and node.value.attr == "heat_source"
                   for node in ast.walk(tree))


def test_creation_saves_and_exports_with_manual_refinement_notice():
    code = TEMPLATE.read_text(encoding="utf-8")
    tree = ast.parse(code)
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert {"save", "write_program_input"} <= attributes
    assert not {"check_model", "export_all_settings", "import_all_settings"} & attributes
    assert "refinement_data" not in code
    assert "手动取消自适应细化" in code
    assert "重新生成求解输入文件" in code
    assert code.index("project.save()") < code.index("手动取消自适应细化") < code.index("target.write_program_input()")
