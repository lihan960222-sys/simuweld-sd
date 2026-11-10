from pathlib import Path
from copy import copy

import numpy as np
import pytest

from sfmarc_workbench.deck import Deck
from sfmarc_workbench.geometry import SurfaceModel
from sfmarc_workbench.local_trial import LocalTrial, _arcs, _box_intersects, _same_trace, _slice


SOURCE = Path.home() / "Downloads" / "C0002.dat"


@pytest.fixture(scope="module")
def trial():
    assert SOURCE.is_file(), "需要真实 Downloads/C0002.dat"
    return LocalTrial(SurfaceModel(Deck({SOURCE.name: SOURCE.read_bytes()}, SOURCE.name)), "m")


def specification(trial):
    path = trial.plan["paths"][0]
    points = np.asarray(path["points_mm"])
    length = _arcs(points)[-1]
    coords, _, _ = _slice(points, np.asarray(path["normals"]), length * .3, length * .6)
    return {"path_id": path["id"], "start_mm": length * .3, "end_mm": length * .6,
            "box": {"min": (coords.min(axis=0) - 2).tolist(), "max": (coords.max(axis=0) + 2).tolist()},
            "keep_whole": [name for name in trial.components if name.lower() == "base"]}


def test_actual_metadata_and_crop(trial):
    metadata = trial.metadata()
    assert metadata["source_sha256"] == trial.model.deck.digest
    assert len(metadata["nodes"]) == len(trial.model.node_ids)
    used = {node for _, nodes, _ in trial.elements.values() for node in nodes}
    assert set(trial.node_index) - used == {72793}
    assert trial.node_components[72793] == []
    assert trial.points[trial.node_index[72793]].tolist() == [0., 0., 0.]
    physical_points = trial.points[[trial.node_index[node] for node in used]]
    assert metadata["bounds_mm"] == [physical_points.min(axis=0).tolist(), physical_points.max(axis=0).tolist()]
    assert metadata["bounds_mm"][1][2] - metadata["bounds_mm"][0][2] == pytest.approx(.501)
    assert metadata["bounds_mm"][1][0] == pytest.approx(-12.882448834)
    spec = specification(trial)
    result = trial.preview(spec)
    crop = result["crop"]
    assert crop["element_count"] < len(trial.elements)
    assert crop["cut_faces"]
    retained = set(crop["node_ids"])
    for element in crop["element_ids"]:
        assert set(trial.elements[element][1]).issubset(retained)
    for component in crop["components"]:
        if component["keep_whole"]:
            assert component["element_ids"] == trial.component_elements[component["name"]]
            assert not component["has_new_cut_faces"]
    match = next(item for item in result["matches"] if item["path_id"] == spec["path_id"])
    source = trial.plan["paths"][0]
    assert match["matched"]
    assert match["start_s"] == pytest.approx(source["start_s"] + spec["start_mm"] / source["speed_mm_s"])
    assert match["end_s"] == pytest.approx(source["start_s"] + spec["end_mm"] / source["speed_mm_s"])
    assert all(a < b for a, b in zip(match["times_s"], match["times_s"][1:]))


def test_continuous_trace_resampling_and_reverse(trial):
    path = trial.plan["paths"][0]
    points = np.asarray(path["points_mm"])[20:45]
    dense = np.concatenate([np.linspace(a, b, 4, endpoint=False) for a, b in zip(points[:-1], points[1:])]
                           + [points[-1:]])
    assert _same_trace(points, dense, .001)
    assert _same_trace(points, dense[::-1], .001)
    assert not _same_trace(points, dense[1:], .001)
    moved = dense.copy()
    moved[len(moved) // 2, 2] += 1
    assert not _same_trace(points, moved, .001)


def test_box_inside_actual_element_without_any_node_inside(trial):
    ident = next(iter(trial.elements))
    nodes = trial.elements[ident][1]
    vertices = trial.points[[trial.node_index[node] for node in nodes]]
    center = vertices.mean(axis=0)
    extent = min(np.linalg.norm(vertices - center, axis=1)) / 100
    lower, upper = center - extent, center + extent
    assert not np.any(np.all((vertices >= lower) & (vertices <= upper), axis=1))
    assert _box_intersects(vertices, lower, upper)
    distant = vertices.max(axis=0) + 10
    assert not _box_intersects(vertices, distant, distant + 1)
    center = (vertices[0] + vertices[1]) / 2
    extent = np.linalg.norm(vertices[0] - vertices[1]) / 100
    assert not np.any(np.all((vertices >= center - extent) & (vertices <= center + extent), axis=1))
    assert _box_intersects(vertices, center - extent, center + extent)


def test_actual_geometry_history_density_direction_and_coverage(trial):
    source = trial.plan["paths"][0]
    points, normals = np.asarray(source["points_mm"])[20:45], np.asarray(source["normals"])[20:45]
    dense = np.concatenate([np.linspace(a, b, 4, endpoint=False) for a, b in zip(points[:-1], points[1:])]
                           + [points[-1:]])
    dense_normals = np.column_stack([np.interp(_arcs(dense), _arcs(points), normals[:, axis]) for axis in range(3)])
    paths = []
    for ident, coords, directions in ((1, points, normals), (2, dense, dense_normals),
                                       (3, dense[::-1], dense_normals[::-1]), (4, dense[1:], dense_normals[1:])):
        path = dict(source, id=ident, points_mm=coords.tolist(), normals=directions.tolist(), start_s=ident * 10.)
        path["length_mm"] = float(_arcs(coords)[-1])
        path["end_s"] = path["start_s"] + path["length_mm"] / path["speed_mm_s"]
        paths.append(path)
    derived = copy(trial)
    derived.plan = dict(trial.plan, paths=paths)
    result = derived.preview({"path_id": 1, "start_mm": 0, "end_mm": paths[0]["length_mm"],
                              "box": {"min": (points.min(axis=0) - 2).tolist(),
                                      "max": (points.max(axis=0) + 2).tolist()}})
    assert [row["matched"] for row in result["matches"]] == [True, True, True, False]
    for row in result["matches"][:3]:
        assert row["start_s"] == pytest.approx(row["path_id"] * 10)
        assert np.allclose(row["points_mm"], paths[row["path_id"] - 1]["points_mm"])
        expected_endpoints = points[[0, -1] if row["path_id"] != 3 else [-1, 0]]
        assert np.array_equal(np.asarray(row["points_mm"])[[0, -1]], expected_endpoints)
    paths[1]["points_mm"] = (dense + [0, 0, .005]).tolist()
    result = derived.preview({"path_id": 1, "start_mm": 0, "end_mm": paths[0]["length_mm"],
                              "box": {"min": (points.min(axis=0) - 2).tolist(),
                                      "max": (points.max(axis=0) + 2).tolist()}})
    shifted = next(row for row in result["matches"] if row["path_id"] == 2)
    assert not shifted["matched"]
    assert "端点" in shifted["reason"]


def test_actual_closed_geometry_keeps_original_time_ranges(trial):
    source = trial.plan["paths"][0]
    points = np.asarray(source["points_mm"])[20:45]
    normals = np.asarray(source["normals"])[20:45]
    points, normals = np.vstack([points, points[:1]]), np.vstack([normals, normals[:1]])
    length = float(_arcs(points)[-1])
    closed = dict(source, points_mm=points.tolist(), normals=normals.tolist(), length_mm=length)
    historical = dict(closed, id=closed["id"] + 100, start_s=closed["start_s"] + 10)
    derived = copy(trial)
    derived.plan = dict(trial.plan, paths=[closed, historical])
    result = derived.preview({"path_id": closed["id"], "start_mm": length * .8, "end_mm": length * .2,
                              "wrap": True, "box": {"min": (points.min(axis=0) - 2).tolist(),
                                                     "max": (points.max(axis=0) + 2).tolist()}})
    assert len(result["matches"]) == 4
    assert all(row["matched"] for row in result["matches"])
    assert result["selection"]["length_mm"] == pytest.approx(length * .4)
    times = [row["start_s"] for row in result["matches"]]
    assert times == sorted(times)
    shifted_points = np.roll(points[:-1], -3, axis=0)
    shifted_normals = np.roll(normals[:-1], -3, axis=0)
    historical["points_mm"] = np.vstack([shifted_points, shifted_points[:1]]).tolist()
    historical["normals"] = np.vstack([shifted_normals, shifted_normals[:1]]).tolist()
    result = derived.preview({"path_id": closed["id"], "start_mm": length * .8, "end_mm": length * .2,
                              "wrap": True, "box": {"min": (points.min(axis=0) - 2).tolist(),
                                                     "max": (points.max(axis=0) + 2).tolist()}})
    historical_matches = [row for row in result["matches"] if row["path_id"] == historical["id"]]
    assert all(row["matched"] for row in historical_matches)
    assert sum(row["source_end_mm"] - row["source_start_mm"] for row in historical_matches) == pytest.approx(length * .4)
    closed["points_mm"][-1][2] += .005
    with pytest.raises(ValueError, match="闭合焊路"):
        derived.preview({"path_id": closed["id"], "start_mm": length * .8, "end_mm": length * .2,
                         "wrap": True, "box": {"min": (points.min(axis=0) - 2).tolist(),
                                                "max": (points.max(axis=0) + 2).tolist()}})


def test_crop_rejects_invalid_ranges_and_weld_outside(trial):
    spec = specification(trial)
    spec["trim_start_mm"] = spec["end_mm"]
    with pytest.raises(ValueError, match="缩短"):
        trial.preview(spec)
    spec = specification(trial)
    spec["box"]["max"] = spec["box"]["min"]
    with pytest.raises(ValueError, match="包围盒"):
        trial.preview(spec)
    spec = specification(trial)
    spec["box"]["max"][0] = (spec["box"]["max"][0] + spec["box"]["min"][0]) / 2
    with pytest.raises(ValueError, match="超出|活动板件"):
        trial.preview(spec)
    spec = specification(trial)
    spec["included_path_ids"] = [spec["path_id"], max(path["id"] for path in trial.plan["paths"]) + 1]
    with pytest.raises(ValueError, match="编号有效"):
        trial.preview(spec)
    spec = specification(trial)
    spec["keep_whole"] = []
    original = next(iter(trial.components))
    rename = lambda name: "Base" if name == original else name
    renamed = copy(trial)
    renamed.components = {rename(name): nodes for name, nodes in trial.components.items()}
    renamed.component_elements = {rename(name): ids for name, ids in trial.component_elements.items()}
    renamed.node_components = {node: [rename(name) for name in names] for node, names in trial.node_components.items()}
    renamed.elements = {ident: (kind, nodes, rename(name)) for ident, (kind, nodes, name) in trial.elements.items()}
    renamed.face_owners = {face: [(ident, rename(name), nodes) for ident, name, nodes in owners]
                           for face, owners in trial.face_owners.items()}
    with pytest.raises(ValueError, match="Base 必须完整保留"):
        renamed.preview(spec)
    del spec["keep_whole"]
    result = renamed.preview(spec)
    base = next(component for component in result["crop"]["components"] if component["name"] == "Base")
    assert base["keep_whole"]
    assert base["element_ids"] == trial.component_elements[original]
    assert not base["has_new_cut_faces"]
