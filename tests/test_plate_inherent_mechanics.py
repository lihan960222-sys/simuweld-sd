from pathlib import Path

import numpy as np
import pytest
from shapely import box

from sfmarc_workbench.plate_geometry import PlateGrid
from sfmarc_workbench.plate_material import Material, MaterialState
from sfmarc_workbench.plate_mechanics import Mechanics
from sfmarc_workbench.plate_structure import Structure
from reference_data import reference_path


@pytest.fixture
def material():
    return Material.from_xmt(reference_path('SFMARC_TEST_MATERIAL').read_bytes())


def plate(material, welded=False):
    polygon = box(0., 0., .002, .002)
    regions = [("lower", polygon, 0., .0001)]
    if welded:
        regions.append(("upper", polygon, .0001, .0002))
    grid = PlateGrid.from_regions(regions, 1.)
    if welded:
        grid.plan = {"paths": [dict(points_mm=[[0, 1, .2], [2, 1, .2]], start_s=0, end_s=1,
                                    sources=[dict(kind=5, depth_mm=.2, cutoff_mm=.3,
                                                  upper_radius_mm=.2, lower_radius_mm=.1)])]}
    model = Structure(grid, material, 1.)
    model.fixture_surfaces = {"base": [(polygon, np.zeros(3))],
                              "cover": [(polygon, np.array([0., 0., .0002]))] if welded else []}
    model.cover_dofs = model.cover_constraints()
    model.contact, model.gaps, model.contact_k, model.contact_types = model.contacts()
    return model


def inherent_state(model, strain=-.001):
    state = MaterialState.zeros((len(model.connectivity), 4, 5), 998.15)
    state.plastic[..., 0] = strain
    state.plastic[..., 1] = strain
    state.plastic[..., 2] = -2 * strain
    state.equivalent[:] = 2 * abs(strain)
    return state


def test_global_loading_preserves_full_material_history_and_release(material):
    model = plate(material, welded=True)
    state = inherent_state(model)
    solver = Mechanics(model, material, inherent_state=state)
    assert len(model.cover_dofs) > 0
    info = solver.apply_inherent()
    assert info["residual_N"] < 1e-7
    assert solver.weld_count == len(model.weld_times) > 0
    np.testing.assert_array_equal(solver.weld_reference, 0.)
    loading = [entry for entry in solver.history if entry["stage"] == "inherent_loading"]
    fractions = [entry["load_fraction"] for entry in loading]
    assert len(fractions) > 1
    assert np.all(np.diff(fractions) > 0)
    assert fractions[-1] == 1.
    assert all(entry["active_weld_rows"] == len(model.weld_times) for entry in solver.history)
    assert np.max(np.abs(solver.q.reshape(-1, 5)[:, :2])) > 1e-7
    assert solver.state.plastic.shape == (len(model.connectivity), 4, 5, 6)
    np.testing.assert_array_equal(solver.state.plastic, state.plastic)
    np.testing.assert_array_equal(solver.state.equivalent, state.equivalent)
    np.testing.assert_allclose(solver.q[model.cover_dofs], 0.)
    solver.release()
    assert solver.result()["residual_N"] < 1e-7
    assert not solver.cover
    np.testing.assert_array_equal(solver.state.plastic, state.plastic)


def test_thickness_history_drives_elastic_stress_and_energy_gradient(material):
    model = plate(material)
    state = inherent_state(model, -.02)
    state.plastic[..., 5] = np.linspace(-.003, .003, 5)
    solver = Mechanics(model, material, inherent_state=state)
    solver.load_fraction = .4
    q = solver.q.copy()
    q[2::5] = -1e-8
    kin, stress, tangent, trial, _ = solver._constitutive(q, solver.temperature, solver.state)
    expected = np.einsum("eij,eptj->epti", model.shell.elasticity,
                         kin.strain - solver.eigenstrain * .4)
    np.testing.assert_allclose(stress, expected)
    assert stress.shape == (len(model.connectivity), 4, 5, 3)
    assert tangent.shape == (len(model.connectivity), 4, 5, 3, 3)
    assert np.ptp(stress[..., 2]) > 0
    np.testing.assert_array_equal(trial.plastic, state.plastic)
    direction = np.random.default_rng(42).normal(size=model.ndof)
    direction[3::5] /= model.grid.spacing
    direction[4::5] /= model.grid.spacing
    delta = 1e-11
    plus = solver._potential(q + delta * direction, solver.temperature, solver.state, None)
    minus = solver._potential(q - delta * direction, solver.temperature, solver.state, None)
    residual = solver._evaluate(q, solver.temperature, solver.state, tangent=False)[0]
    assert (plus - minus) / (2 * delta) == pytest.approx(residual @ direction, rel=1e-6)
    matrix = solver._evaluate(q, solver.temperature, solver.state)[1]
    plus_residual = solver._evaluate(q + delta * direction, solver.temperature, solver.state, tangent=False)[0]
    minus_residual = solver._evaluate(q - delta * direction, solver.temperature, solver.state, tangent=False)[0]
    np.testing.assert_allclose((plus_residual - minus_residual) / (2 * delta),
                               matrix @ direction, rtol=2e-6, atol=.1)
    with pytest.raises(ValueError, match="apply_inherent"):
        solver.advance(1., solver.temperature)


def test_requires_five_thickness_points(material):
    model = plate(material)
    with pytest.raises(ValueError, match="4×5"):
        Mechanics(model, material, inherent_state=MaterialState.zeros((len(model.connectivity), 4, 3)))
