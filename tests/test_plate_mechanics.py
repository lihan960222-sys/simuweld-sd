from pathlib import Path

import numpy as np
import pytest
from shapely import box

from sfmarc_workbench.plate_geometry import PlateGrid
from sfmarc_workbench.plate_material import Material
from sfmarc_workbench.plate_mechanics import Mechanics
from sfmarc_workbench.plate_structure import Structure
from reference_data import reference_path


@pytest.fixture
def material():
    return Material.from_xmt(reference_path('SFMARC_TEST_MATERIAL').read_bytes())


def plate(material):
    polygon = box(0., 0., .002, .002)
    grid = PlateGrid.from_regions([("plate", polygon, 0., .0001)], 1.)
    model = Structure(grid, material, 1.)
    model.fixture_surfaces = {"base": [(polygon, np.zeros(3))], "cover": []}
    model.contact, model.gaps, model.contact_k, model.contact_types = model.contacts()
    return model


def test_free_uniform_heating_and_release(material):
    model = plate(material)
    solver = Mechanics(model, material)
    solver.advance(0., np.full(model.map.shape[1], 298.15))
    for time, temperature in enumerate((598.15, 998.15, 298.15), 1):
        info = solver.advance(time, np.full(model.map.shape[1], temperature))
        assert info["residual_N"] < 1e-7
        assert np.max(solver.state.equivalent) < 1e-9
        expected = material.thermal_strain(temperature) * model.xy
        np.testing.assert_allclose(solver.q.reshape(-1, 5)[:, :2], expected, atol=2e-12)
    solver.release()
    assert solver.result()["residual_N"] < 1e-7
    assert solver.state.plastic.shape == (4, 4, 5, 6)
    gap = model.contact @ solver.q + model.gaps
    assert -np.sum(solver.contact_k * np.minimum(gap, 0.)) == pytest.approx(-model.gravity[2::5].sum(), rel=1e-7)


def test_constrained_cycles_accumulate_signed_plasticity(material):
    model = plate(material)
    model.cover_dofs = np.arange(model.ndof)
    solver = Mechanics(model, material)
    solver.advance(0., solver.temperature.copy())
    solver.advance(1., np.full(model.map.shape[1], 998.15))
    compressed = solver.state.plastic.copy()
    first = solver.state.equivalent.copy()
    assert np.max(compressed[..., :2]) < 0
    solver.advance(2., np.full(model.map.shape[1], 298.15))
    assert np.all(solver.state.equivalent > first)
    assert np.all(solver.state.plastic[..., 0] > compressed[..., 0])
    before_release = solver.state.equivalent.copy()
    solver.release()
    assert np.all(solver.state.equivalent >= before_release)
    assert solver.result()["residual_N"] < 1e-7


def test_trial_tangent_and_no_history_commit(material):
    model = plate(material)
    model.cover_dofs = np.arange(model.ndof)
    solver = Mechanics(model, material)
    temperature = np.full(model.map.shape[1], 998.15)
    residual, tangent, trial = solver._evaluate(solver.q, temperature, solver.state)
    assert trial.equivalent.max() > 0
    assert solver.state.equivalent.max() == 0
    direction = np.random.default_rng(41).normal(size=model.ndof)
    direction[3::5] /= model.grid.spacing
    direction[4::5] /= model.grid.spacing
    delta = 1e-10
    plus = solver._evaluate(solver.q + delta * direction, temperature, solver.state, tangent=False)[0]
    minus = solver._evaluate(solver.q - delta * direction, temperature, solver.state, tangent=False)[0]
    # 接触分支固定在受压位置，使有限差分检查同一个切线。
    contact = model.contact
    expected = tangent @ direction - .5 * contact.T @ (solver.contact_k * (contact @ direction))
    np.testing.assert_allclose((plus - minus) / (2 * delta), expected, rtol=2e-5, atol=.1)
    direction.reshape(-1, 5)[:, 2:] = 0
    plus_energy = solver._potential(solver.q + delta * direction, temperature, solver.state, None)
    minus_energy = solver._potential(solver.q - delta * direction, temperature, solver.state, None)
    assert (plus_energy - minus_energy) / (2 * delta) == pytest.approx(residual @ direction, rel=2e-6)


def test_high_temperature_extension_keeps_peak(material):
    model = plate(material)
    model.cover_dofs = np.arange(model.ndof)
    solver = Mechanics(model, material)
    target = material.temperature_max + 100
    solver.advance(1., np.full(model.map.shape[1], target))
    np.testing.assert_allclose(solver.state.peak_temperature, target)
    np.testing.assert_allclose(solver.temperature, target)
    assert solver.result()["temperature_extrapolated"].all()
    assert solver.result()["extrapolated"].all()


def test_cooling_history_potential_gradient_and_tangent(material):
    model = plate(material)
    model.cover_dofs = np.arange(model.ndof)
    solver = Mechanics(model, material)
    solver.advance(1., np.array([1298.15, 998.15, 698.15, 498.15]))
    solver.advance(2., np.array([648.15, 598.15, 448.15, 348.15]))
    assert solver.state.equivalent.max() > 0
    temperature = solver.temperature - 1.7
    q = solver.q.copy()
    q[2::5] = 1e-6
    direction = np.random.default_rng(54).normal(size=model.ndof)
    direction[3::5] /= model.grid.spacing
    direction[4::5] /= model.grid.spacing
    delta = 1e-10
    residual, tangent, _ = solver._evaluate(q, temperature, solver.state)
    plus = solver._evaluate(q + delta * direction, temperature, solver.state, tangent=False)[0]
    minus = solver._evaluate(q - delta * direction, temperature, solver.state, tangent=False)[0]
    np.testing.assert_allclose((plus - minus) / (2 * delta), tangent @ direction, rtol=2e-5, atol=.1)
    plus_energy = solver._potential(q + delta * direction, temperature, solver.state, None)
    minus_energy = solver._potential(q - delta * direction, temperature, solver.state, None)
    assert (plus_energy - minus_energy) / (2 * delta) == pytest.approx(residual @ direction, rel=2e-6)


@pytest.mark.parametrize("initial_gap", [2.607e-12, 1.1002344394605039e-5])
def test_gravity_closes_positive_gap(material, initial_gap):
    model = plate(material)
    model.gaps += initial_gap
    original_gaps = model.gaps.copy()
    solver = Mechanics(model, material)
    solver.advance(.001, solver.temperature.copy())
    np.testing.assert_array_equal(model.gaps, original_gaps)
    gap = model.contact @ solver.q + model.gaps
    assert solver.result()["residual_N"] < 1e-7
    assert -np.sum(solver.contact_k * np.minimum(gap, 0.)) == pytest.approx(-model.gravity[2::5].sum(), rel=1e-7)


def test_nonuniform_heat_cool_and_cover_removal(material):
    model = plate(material)
    model.cover_dofs = np.arange(2, model.ndof, 5)
    solver = Mechanics(model, material)
    solver.advance(0., solver.temperature.copy())
    solver.advance(1., np.array([1298.15, 698.15, 698.15, 298.15]))
    solver.advance(2., np.full(4, 298.15))
    assert solver.state.equivalent.max() > 0
    solver.release()
    result = solver.result()
    assert result["residual_N"] < 1e-7
    assert np.isfinite(result["q"]).all()
    assert result["cover"] is False
    unloading = [entry for entry in result["history"] if entry.get("stage") == "cover_unloading"]
    assert unloading[-1]["release_fraction"] == 1.
    assert all(entry["residual_N"] < 1e-7 for entry in unloading)
    assert np.all(np.diff([entry["release_fraction"] for entry in unloading]) > 0)


def test_two_cover_nodes_allow_tilting_into_base(material):
    model = plate(material)
    model.gaps += 1.1002344394605039e-5
    nodes = np.flatnonzero(model.xy[:, 0] == model.xy[:, 0].min())
    model.cover_dofs = 5 * nodes[[0, -1]] + 2
    original_gaps = model.gaps.copy()
    messages = []
    solver = Mechanics(model, material, progress=messages.append)
    solver.advance(.001, solver.temperature.copy())
    assert messages and "Newton" in messages[-1]
    assert solver.result()["residual_N"] < 1e-7
    np.testing.assert_array_equal(model.gaps, original_gaps)
    np.testing.assert_array_equal(solver.q[model.cover_dofs], 0.)
    assert np.min(solver.q[2::5]) < -1e-5
    assert np.any(model.contact @ solver.q + model.gaps < 0)


def test_progressive_weld_preserves_activation_reference(material):
    polygon = box(0., 0., .002, .002)
    grid = PlateGrid.from_regions([("lower", polygon, 0., .0001),
                                   ("upper", polygon, .0001, .0002)], 1.)
    grid.plan = {"paths": [dict(points_mm=[[0., 1., .2], [2., 1., .2]], start_s=0., end_s=1.,
                                sources=[dict(kind=5, depth_mm=.2, cutoff_mm=.3,
                                              upper_radius_mm=.2, lower_radius_mm=.1)])]}
    model = Structure(grid, material, 1.)
    model.fixture_surfaces = {"base": [(polygon, np.zeros(3))],
                              "cover": [(polygon, np.array([0., 0., .0002]))]}
    model.cover_dofs = model.cover_constraints()
    model.contact, model.gaps, model.contact_k, model.contact_types = model.contacts()
    solver = Mechanics(model, material)
    first_time = float(model.weld_times[0])
    solver.advance(first_time, np.r_[np.full(4, 348.15), np.full(4, 398.15)])
    assert solver.history[0]["stage"] == "initial_equilibrium"
    count = solver.weld_count
    assert 0 < count < len(model.weld_times)
    references = solver.weld_reference[:count].copy()
    np.testing.assert_allclose(references, model.weld[:count] @ solver.q, atol=1e-16)
    assert np.max(np.abs(references)) > 1e-8
    solver.advance(1., np.full(8, 298.15))
    np.testing.assert_array_equal(solver.weld_reference[:count], references)
    assert solver.weld_count == len(model.weld_times)
    solver.release()
    assert solver.result()["residual_N"] < 1e-7
