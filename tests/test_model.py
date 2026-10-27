import numpy as np
import pytest


def test_actual_model_identifiers(model):
    sample = model.deck.root.endswith("elemtype7_tension.dat")
    assert len(model.points) == (125 if sample else 163924)
    assert len(model.deck.mesh.elements) == (64 if sample else 88452)
    if not sample:
        assert set(model.deck.mesh.nodes) == set(range(1, 163925))
    assert {e[1] for e in model.deck.mesh.elements} == {7}
    assert len(model.deck.cases) == (2 if sample else 153)
    assert len(np.unique(model.body_ids)) == (1 if sample else 7)
    if not sample:
        assert model.deck.missing_materials == ["newsus304-sw.umt"]


def test_surface_geometry_and_conservation(model):
    assert np.all(model.areas > 0)
    assert np.allclose(np.linalg.norm(model.normals, axis=1), 1)
    assert set(model.local_faces) <= {1, 2, 3, 4, 5, 6}
    for body in np.unique(model.body_ids):
        mask = model.body_ids == body
        assert np.linalg.norm(model.area_vectors[mask].sum(axis=0)) < model.areas[mask].sum() * 1e-8


def test_edges_follow_original_element_faces(model):
    elements = {element[0]: element[2] for element in model.deck.mesh.elements}
    # type 7 的六个面边界，直接使用原始连接顺序核对。
    local_faces = ((0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6),
                   (3, 0, 4, 7), (0, 1, 2, 3), (5, 4, 7, 6))
    expected = set()
    for element, local, body in zip(model.element_ids, model.local_faces, model.body_ids):
        nodes = list(dict.fromkeys(elements[element][i] for i in local_faces[local - 1]))
        expected.update((int(body), min(a, b), max(a, b)) for a, b in zip(nodes, nodes[1:] + nodes[:1]))
    actual = {(int(body), *sorted(model.node_ids[edge].tolist()))
              for body, edge in zip(model.edge_bodies, model.edges)}
    assert actual == expected
    assert len(model.edges) == len(expected)
    if model.deck.root.endswith("elemtype7_tension.dat"):
        assert len(actual) == 192


def test_angle_selection_stays_connected_and_in_body(model):
    seed = int(np.argmax(model.areas))
    selected = model.select(seed, 1.0)
    assert seed in selected
    assert len(selected) > 1
    assert len(set(model.body_ids[selected])) == 1
    assert len(selected) <= len(model.select(seed, 45.0))
    with pytest.raises(ValueError):
        model.select(-1, 20)
