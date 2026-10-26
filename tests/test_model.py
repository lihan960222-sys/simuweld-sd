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


def test_angle_selection_stays_connected_and_in_body(model):
    seed = int(np.argmax(model.areas))
    selected = model.select(seed, 1.0)
    assert seed in selected
    assert len(selected) > 1
    assert len(set(model.body_ids[selected])) == 1
    assert len(selected) <= len(model.select(seed, 45.0))
    with pytest.raises(ValueError):
        model.select(-1, 20)
