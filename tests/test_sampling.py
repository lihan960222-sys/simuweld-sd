import ast
import os
from pathlib import Path

import numpy as np
import pytest

from sfmarc_workbench.deck import Deck
from sfmarc_workbench.geometry import SurfaceModel
from sfmarc_workbench.thermal import ThermalConversion
from sfmarc_workbench.sampling import select_nodes


SOURCE = Path(os.environ.get("SFMARC_TEST_TC_DAT", "work/thermal-sample/C0002.dat"))
pytestmark = pytest.mark.skipif(not SOURCE.is_file(), reason="需要真实 C0002.dat")


@pytest.fixture(scope="module")
def conversion():
    return ThermalConversion(SurfaceModel(Deck({SOURCE.name: SOURCE.read_bytes()}, SOURCE.name)), "m")


def test_sampling_real_geometry(conversion):
    metadata = conversion.metadata()
    assert metadata["nodes"] and metadata["sampling"]["search_mm"] == .25
    assert metadata["can_export"]
    for path in metadata["paths"]:
        assert path["segments"][0]["start_mm"] == 0
        assert path["segments"][-1]["end_mm"] == pytest.approx(path["length_mm"])
        assert path["node_count"] == sum(path["id"] in node["paths"] for node in metadata["nodes"])
        assert path["segments"][0]["start_s"] == path["start_s"]
        assert path["segments"][-1]["end_s"] == path["end_s"]
        for segment in path["segments"]:
            assert segment["samples"]
            assert all(sample["distance_mm"] <= segment["radius_mm"] for sample in segment["samples"])
    for node in metadata["nodes"]:
        assert np.allclose(node["xyz_mm"], np.asarray(conversion.model.deck.mesh.nodes[node["node_id"]]) * 1000)
    code = ast.parse(conversion.export_script())
    config = ast.literal_eval(code.body[0].value)
    exported = {(group["name"], node) for group in config["groups"] for node in group["node_ids"]}
    assert exported == set(conversion.selected)


def test_parameters_invalidate_and_change_real_selection(conversion):
    original = set(conversion.selected)
    conversion.export_script()
    divisions = {str(path["id"]): {"mode": "count", "value": 20} for path in conversion.plan["paths"]}
    metadata = conversion.resample({"divisions": divisions})
    assert metadata["segment_count"] == 80
    assert conversion.exported and set(conversion.selected) == original
    conversion.resample({"search_mm": .05})
    assert not conversion.exported and conversion.temperatures is None and conversion.summary is None
    assert set(conversion.selected) != original
    assert conversion.metadata()["missing_length_mm"] > 0
    conversion.resample()


def test_old_real_csv_must_match_current_selection(conversion):
    source = Path(os.environ.get("SFMARC_TEST_TC_CSV", str(SOURCE.with_name("C0002_node_temperatures.csv"))))
    if not source.is_file():
        pytest.skip("需要真实温度 CSV")
    conversion.export_script()
    with source.open(encoding="utf-8-sig", newline="") as stream:
        with pytest.raises(ValueError, match="节点集合"):
            conversion.import_temperatures(stream)
    assert conversion.temperatures is None


def test_real_rotated_geometry_and_empty_selection(conversion):
    conversion.resample()
    original = set(conversion.selected)
    angle = np.radians(37.)
    rotation = np.array([[1, 0, 0], [0, np.cos(angle), -np.sin(angle)], [0, np.sin(angle), np.cos(angle)]])
    points = conversion.points @ rotation.T
    paths = [{**path, "points_mm": (np.asarray(path["points_mm"]) @ rotation.T).tolist()}
             for path in conversion.plan["paths"]]
    for path in paths:
        path["normals"] = (np.asarray(path["normals"]) @ rotation.T).tolist()
    selected, missing = select_nodes(conversion.model, points, conversion.node_index, conversion.components,
                                     paths, conversion.sampling)
    assert set(selected) == original
    assert len(missing) == len(conversion.missing)
    conversion.resample({"search_mm": 1e-8})
    assert conversion.missing
    with pytest.raises(ValueError, match="未覆盖"):
        conversion.export_script()
    conversion.resample()


def test_invalid_parameters_leave_previous_preview_intact(conversion):
    previous = conversion.sampling_id
    for changes in ({"search_mm": 0}, {"search_mm": float("nan")}, {"offsets_mm": []}, {"surface": "other"},
                    {"divisions": {"2900000": {"mode": "count", "value": 2.5}}}):
        conversion.export_script()
        with pytest.raises(ValueError):
            conversion.resample(changes)
        assert conversion.sampling_id == previous
        assert conversion.exported and conversion.temperatures is None and conversion.summary is None


def test_real_selection_has_unique_nodes_and_shared_path_membership(conversion):
    conversion.resample()
    metadata = conversion.metadata()
    assert len(metadata["nodes"]) == len({(node["component"], node["node_id"]) for node in metadata["nodes"]})
    assert any(len(node["paths"]) > 1 for node in metadata["nodes"])
    assert any(node["node_id"] == 21176 for node in metadata["nodes"])
