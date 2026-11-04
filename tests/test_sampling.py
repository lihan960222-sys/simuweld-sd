import ast
from collections import Counter
import os
from pathlib import Path

import numpy as np
import pytest

from sfmarc_workbench.deck import Deck
from sfmarc_workbench.geometry import SurfaceModel
from sfmarc_workbench.thermal import ThermalConversion
from sfmarc_workbench.sampling import ComponentSurface, select_nodes


SOURCE = Path(os.environ.get("SFMARC_TEST_TC_DAT", "work/thermal-sample/C0002.dat"))
pytestmark = pytest.mark.skipif(not SOURCE.is_file(), reason="需要真实 C0002.dat")


@pytest.fixture(scope="module")
def conversion():
    return ThermalConversion(SurfaceModel(Deck({SOURCE.name: SOURCE.read_bytes()}, SOURCE.name)), "m")


def test_sampling_real_geometry(conversion):
    metadata = conversion.metadata()
    assert metadata["nodes"] and metadata["sampling"]["spacing_mm"] == 2
    targets = [target for node in metadata["nodes"] for target in node["targets"]] + metadata["missing"]
    target_counts = Counter((item["path_id"], round(item["station_mm"], 8), item["component"],
                             item["offset_mm"], item["surface"]) for item in targets)
    for path in metadata["paths"]:
        assert path["stations_mm"][0] == 0
        assert path["stations_mm"][-1] == pytest.approx(path["length_mm"])
        assert all(any(abs(value - station) < 1e-8 for station in path["stations_mm"]) for value in path["turn_positions_mm"])
        assert path["node_count"] == sum(path["id"] in node["paths"] for node in metadata["nodes"])
        for position in [0., path["length_mm"], *path["turn_positions_mm"]]:
            actual = min(path["stations_mm"], key=lambda value: abs(value - position))
            assert actual == pytest.approx(position, abs=1e-8)
            for component in conversion.components:
                for offset in metadata["sampling"]["offsets_mm"]:
                    for side in ("upper", "lower"):
                        assert target_counts[path["id"], round(actual, 8), component, offset, side] == 1
    for node in metadata["nodes"]:
        assert np.allclose(node["xyz_mm"], np.asarray(conversion.model.deck.mesh.nodes[node["node_id"]]) * 1000)
        for target in node["targets"]:
            assert np.linalg.norm(np.asarray(node["xyz_mm"]) - target["xyz_mm"]) <= .25 + 1e-10
    code = ast.parse(conversion.export_script())
    config = ast.literal_eval(code.body[0].value)
    exported = {(group["name"], node) for group in config["groups"] for node in group["node_ids"]}
    assert exported == set(conversion.selected)


def test_parameters_invalidate_and_change_real_selection(conversion):
    original = set(conversion.selected)
    configurations = [dict(spacing_mm=4), dict(offsets_mm=[0.]), dict(surface="upper"),
                      dict(surface="lower"), dict(search_mm=.05), dict(include_turns=False)]
    counts = []
    for changes in configurations:
        conversion.resample(changes)
        counts.append((changes, len(conversion.selected), len(conversion.missing)))
        assert not conversion.exported and conversion.temperatures is None and conversion.summary is None
        assert set(conversion.selected) != original
    print("实际参数检查", counts)
    conversion.resample()


def test_old_real_csv_must_match_current_selection(conversion):
    source = Path(os.environ.get("SFMARC_TEST_TC_CSV", str(SOURCE.with_name("C0002_node_temperatures.csv"))))
    if not source.is_file():
        pytest.skip("需要真实温度 CSV")
    conversion.export_script()
    with source.open(encoding="utf-8-sig", newline="") as stream:
        with pytest.raises(ValueError, match="代表节点|节点集合"):
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
    surfaces = {name: ComponentSurface(conversion.model, points, [conversion.node_index[node] for node in ids])
                for name, ids in conversion.components.items()}
    selected, missing = select_nodes(conversion.model, points, conversion.node_index, conversion.components,
                                     paths, conversion.sampling, surfaces)
    assert set(selected) == original
    assert len(missing) == len(conversion.missing)
    conversion.resample({"offsets_mm": [10000.]})
    assert not conversion.selected and conversion.missing
    with pytest.raises(ValueError, match="没有匹配"):
        conversion.export_script()
    conversion.resample()


def test_invalid_parameters_leave_previous_preview_intact(conversion):
    previous = conversion.sampling_id
    for changes in ({"spacing_mm": 0}, {"spacing_mm": 1e-320}, {"search_mm": float("nan")}, {"offsets_mm": []}, {"surface": "other"}):
        conversion.export_script()
        with pytest.raises(ValueError):
            conversion.resample(changes)
        assert conversion.sampling_id == previous
        assert not conversion.exported and conversion.temperatures is None and conversion.summary is None


def test_real_upper_lower_surface_geometry(conversion):
    conversion.resample({"surface": "both"})
    both = set(conversion.selected)
    selections = {}
    surface_nodes = {}
    for name, surface in conversion.surfaces.items():
        indices = set(surface.indices.tolist())
        for side in ("upper", "lower"):
            sign = 1 if side == "upper" else -1
            surface_nodes[name, side] = {int(conversion.model.node_ids[index])
                for face, normal in zip(conversion.model.faces, conversion.model.normals)
                if sign * float(normal @ surface.normal) > .5 and set(face) <= indices
                for index in face}
    for side in ("upper", "lower"):
        conversion.resample({"surface": side})
        selections[side] = set(conversion.selected)
        for node in conversion.selected.values():
            assert node["node_id"] in surface_nodes[node["component"], side]
            surface = conversion.surfaces[node["component"]]
            for target in node["targets"]:
                hits = surface.intersections(np.asarray(target["xyz_mm"]))
                assert np.allclose(hits[side][0], target["xyz_mm"], atol=1e-8, rtol=0)
    assert not selections["upper"] & selections["lower"]
    assert both == selections["upper"] | selections["lower"]
    conversion.resample()
