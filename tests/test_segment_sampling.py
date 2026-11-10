from copy import deepcopy
import os
from pathlib import Path

from meshioplusplus.marc import _marc
import numpy as np
import pytest
from scipy.spatial import cKDTree

from sfmarc_workbench.deck import Deck
from sfmarc_workbench.geometry import SurfaceModel
from sfmarc_workbench.sampling import nearest_intervals, sampling_parameters, select_nodes
from sfmarc_workbench.thermal_deck import read_weld_paths


SOURCE = Path(os.environ.get("SFMARC_TEST_TC_DAT", "work/thermal-sample/C0002.dat"))
pytestmark = pytest.mark.skipif(not SOURCE.is_file(), reason="需要真实 C0002.dat")


@pytest.fixture(scope="module")
def geometry():
    deck = Deck({SOURCE.name: SOURCE.read_bytes()}, SOURCE.name)
    model = SurfaceModel(deck)
    known, components = {}, {}
    for name, family, tokens, _ in deck.mesh.sets:
        if family == "node":
            members = _marc._expand(tokens, name, known, _marc._DAT)
            known[name.lower()] = members
            if name.startswith("T_cpt_") and name.endswith("_n_set"):
                components[name[6:-6]] = sorted(set(members))
    return (model, model.points * 1000,
            {int(node): index for index, node in enumerate(model.node_ids)},
            components, read_weld_paths(deck, "m")["paths"])


def sample(geometry, parameters=None, paths=None, points=None):
    model, xyz, node_index, components, original = geometry
    paths = deepcopy(original if paths is None else paths)
    selected, missing = select_nodes(model, xyz if points is None else points, node_index,
                                     components, paths, sampling_parameters(paths, parameters))
    return selected, missing, paths


def test_parameters_and_continuous_real_node_selection(geometry):
    paths = geometry[-1]
    parameters = sampling_parameters(paths)
    assert parameters["divisions"][str(paths[0]["id"])] == {"mode": "length", "value": 2.}
    selected, missing, divided = sample(geometry)
    assert len(selected) > 237
    assert any(node == 21176 for _, node in selected)
    for key, record in selected.items():
        assert key == (record["component"], record["node_id"])
        np.testing.assert_allclose(record["xyz_mm"], geometry[1][geometry[2][key[1]]], rtol=0, atol=0)
        assert record["paths"] and record["segments"]
    print("连续采样", len(selected), len(missing),
          [(path["id"], path["node_count"], len(path["segments"])) for path in divided])


def test_real_segment_members_geometry_and_timing(geometry):
    selected, _, paths = sample(geometry)
    for original, path in zip(geometry[-1], paths):
        assert path["segments"][0]["start_s"] == original["start_s"]
        assert path["segments"][-1]["end_s"] == original["end_s"]
        assert sum(segment["length_mm"] for segment in path["segments"]) == pytest.approx(path["length_mm"])
        observed = set()
        for index, segment in enumerate(path["segments"], 1):
            assert segment["index"] == index and segment["length_mm"] <= 2 + 1e-10
            xyz = np.asarray(segment["points_mm"])
            vectors = np.diff(xyz, axis=0)
            lengths = np.linalg.norm(vectors, axis=1)
            assert lengths.sum() == pytest.approx(segment["length_mm"], abs=1e-8)
            np.testing.assert_allclose(np.linalg.norm(segment["normals"], axis=1), 1, atol=1e-12)
            assert segment["node_count"] == len(segment["samples"])
            keys = {(item["component"], item["node_id"]) for item in segment["samples"]}
            assert len(keys) == segment["node_count"]
            observed.update(keys)
            for item in segment["samples"]:
                assert segment["start_s"] - 1e-12 <= item["arrival_s"] <= segment["end_s"] + 1e-12
                relative = np.asarray(item["xyz_mm"]) - xyz[:-1]
                ratios = np.clip(np.sum(relative * vectors, axis=1) / lengths ** 2, 0, 1)
                distance = np.linalg.norm(relative - ratios[:, None] * vectors, axis=1).min()
                assert item["distance_mm"] == pytest.approx(distance, abs=1e-9)
                assert segment["radius_mm"] >= distance
                assert any(entry["path_id"] == path["id"] and entry["index"] == index
                           for entry in selected[item["component"], item["node_id"]]["segments"])
        assert observed == {key for key, item in selected.items() if path["id"] in item["paths"]}


def test_real_selection_is_independent_of_divisions_and_duplicate_paths(geometry):
    expected, _, _ = sample(geometry)
    divisions = {str(path["id"]): {"mode": "count", "value": 7} for path in geometry[-1]}
    selected, _, paths = sample(geometry, {"divisions": divisions})
    assert selected.keys() == expected.keys()
    assert all(len(path["segments"]) == 7 for path in paths)
    repeated = deepcopy(geometry[-1])
    repeated[1]["points_mm"] = deepcopy(repeated[0]["points_mm"])
    repeated[1]["normals"] = deepcopy(repeated[0]["normals"])
    repeated[1]["length_mm"] = repeated[0]["length_mm"]
    single, _, _ = sample(geometry, paths=repeated[:1])
    combined, _, paths = sample(geometry, paths=repeated[:2])
    assert single.keys() == combined.keys()
    assert all(record["paths"] == {paths[0]["id"], paths[1]["id"]} for record in combined.values())


def test_real_selection_survives_rotation_reversal_and_collinear_vertices(geometry):
    expected, _, _ = sample(geometry)
    angle = np.radians(37.)
    rotation = np.array([[1, 0, 0], [0, np.cos(angle), -np.sin(angle)], [0, np.sin(angle), np.cos(angle)]])
    paths = deepcopy(geometry[-1])
    for path in paths:
        path["points_mm"] = (np.asarray(path["points_mm"])[::-1] @ rotation.T).tolist()
        path["normals"] = (np.asarray(path["normals"])[::-1] @ rotation.T).tolist()
    rotated, _, _ = sample(geometry, paths=paths, points=geometry[1] @ rotation.T)
    assert rotated.keys() == expected.keys()
    paths = deepcopy(geometry[-1])
    for path in paths:
        xyz, normals = np.asarray(path["points_mm"]), np.asarray(path["normals"])
        path["points_mm"] = np.concatenate((np.stack((xyz[:-1], (xyz[:-1] + xyz[1:]) / 2), axis=1).reshape(-1, 3), xyz[-1:])).tolist()
        path["normals"] = np.concatenate((np.stack((normals[:-1], (normals[:-1] + normals[1:]) / 2), axis=1).reshape(-1, 3), normals[-1:])).tolist()
    divided, _, _ = sample(geometry, paths=paths)
    assert divided.keys() == expected.keys()


def test_real_continuous_gaps_match_distance_queries(geometry):
    selected, missing, paths = sample(geometry, {"search_mm": .02})
    assert selected and missing
    indices = sorted({geometry[2][node] for ids in geometry[3].values() for node in ids})
    tree = cKDTree(geometry[1][indices])
    for path in paths:
        gaps = [item for item in missing if item["path_id"] == path["id"]]
        assert path["missing_count"] == len(gaps)
        assert path["missing_length_mm"] == pytest.approx(sum(item["length_mm"] for item in gaps))
        for gap in gaps:
            assert gap["end_mm"] > gap["start_mm"]
            assert tree.query(gap["xyz_mm"])[0] > .02 - 1e-10
            assert gap["length_mm"] == pytest.approx(gap["end_mm"] - gap["start_mm"])


def test_real_long_segments_match_independent_pairwise_inequalities(geometry):
    keys = sorted((name, node) for name, nodes in geometry[3].items() for node in nodes)
    points = np.asarray([geometry[1][geometry[2][node]] for _, node in keys])
    tree = cKDTree(points)
    for path in geometry[-1]:
        xyz = np.asarray(path["points_mm"])
        vectors = np.diff(xyz, axis=0)
        lengths = np.linalg.norm(vectors, axis=1)
        edge = int(np.argmax(lengths))
        start, end, length = xyz[edge], xyz[edge + 1], lengths[edge]
        direction = (end - start) / length
        relative = points - start
        along = relative @ direction
        residual = relative - np.clip(along, 0, length)[:, None] * direction
        candidates = np.flatnonzero(np.linalg.norm(residual, axis=1) <= .25 + 1e-9)
        along = along[candidates]
        relative = relative[candidates]
        squared = np.sum(relative ** 2, axis=1)
        expected = set()
        for index, node in enumerate(candidates):
            coefficients = 2 * (along - along[index])
            limits = squared - squared[index]
            parallel = np.abs(coefficients) < 1e-12
            if np.any(parallel & ((limits < -1e-10) | ((np.abs(limits) <= 1e-10) & (candidates < node)))):
                continue
            lower = max(0., float(np.max(limits[coefficients < -1e-12] / coefficients[coefficients < -1e-12], initial=-np.inf)))
            upper = min(length, float(np.min(limits[coefficients > 1e-12] / coefficients[coefficients > 1e-12], initial=np.inf)))
            height = np.linalg.norm(relative[index] - along[index] * direction)
            if height > .25 + 1e-9:
                continue
            reach = np.sqrt(max(0., .25 ** 2 - height ** 2))
            lower, upper = max(lower, along[index] - reach), min(upper, along[index] + reach)
            if upper - lower > 1e-9:
                expected.add(int(node))
            elif lower <= upper + 1e-9 and 0 <= lower <= length:
                distances = np.linalg.norm(relative - lower * direction, axis=1)
                winner = candidates[np.flatnonzero(distances <= distances.min() + 1e-9)[0]]
                if winner == node:
                    expected.add(int(node))
        actual = {node for _, _, node in nearest_intervals(tree, points, start, end, .25)}
        assert actual == expected


@pytest.mark.parametrize("values", [
    {"spacing_mm": 2}, {"surface": "upper"}, {"search_mm": 0}, {"search_mm": float("nan")},
    {"divisions": {"unknown": {"mode": "count", "value": 2}}},
    {"divisions": {"2900000": {"mode": "count", "value": 2.5}}},
    {"divisions": {"2900000": {"mode": "count", "value": True}}},
    {"divisions": {"2900000": {"mode": "length", "value": 0}}},
])
def test_invalid_parameters_fail_before_selection(geometry, values):
    with pytest.raises(ValueError):
        sampling_parameters(geometry[-1], values)
