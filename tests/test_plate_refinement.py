from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import box

from sfmarc_workbench.plate_geometry import PlateGrid
from sfmarc_workbench.plate_material import Material
from sfmarc_workbench.plate_thermal import ThermalSolver
from reference_data import reference_path


@pytest.fixture
def material():
    return Material.from_xmt(reference_path('SFMARC_TEST_MATERIAL').read_bytes())


def refined_grid(two=False):
    regions = [('lower', box(0, 0, .012, .006), 0., .00005)]
    if two:
        regions.append(('upper', box(0, 0, .012, .006), .00008, .00016))
    return PlateGrid.from_regions(regions, 1., refinement_paths=[np.array([[.004, .003], [.008, .003]])],
                                  weld_spacing_mm=.05, refine_distance_mm=.5)


def test_path_refinement_dimensions_and_growth():
    grid = refined_grid()
    assert grid.spacing == .001
    assert grid.x[[0, -1]] == pytest.approx([0., .012])
    assert grid.y[[0, -1]] == pytest.approx([0., .006])
    assert grid.cell_area == pytest.approx(grid.dy[:, None] * grid.dx[None, :])
    for coordinates, lower, upper in [(grid.x, .0035, .0085), (grid.y, .0025, .0035)]:
        widths = np.diff(coordinates)
        middle = (coordinates[:-1] + coordinates[1:]) / 2
        assert widths[(middle > lower) & (middle < upper)].max() <= .00005 * (1 + 1e-10)
        assert widths.max() > 3 * widths.min()
        assert widths[1] / widths[0] == pytest.approx(1.3)
    assert np.allclose(grid.layers[0].fraction, 1.)
    assert grid.layers[0].area.sum() == pytest.approx(.012 * .006)


def test_nonuniform_inplane_conduction_preserves_enthalpy(material):
    grid = refined_grid()
    solver = ThermalSolver(grid, material)
    solver.temperature[0] = 500 + 200 * np.cos(np.pi * grid.centers[..., 0] / .012)
    initial = solver.total_enthalpy()
    spread = np.ptp(solver.temperature)
    for _ in range(4):
        solver.step(.01)
    assert solver.total_enthalpy() == pytest.approx(initial, rel=2e-10)
    assert np.ptp(solver.temperature) < spread


def test_nonuniform_diffusion_matches_cosine_solution(material):
    grid = refined_grid()
    ambient, amplitude, end = 300., .01, .001
    solver = ThermalSolver(grid, material)
    cosine = np.cos(np.pi * grid.centers[..., 0] / .012)
    solver.temperature[0] = ambient + amplitude * cosine
    for _ in range(50):
        solver.step(end / 50)
    diffusivity = material.conductivity(ambient) / (material.density * material.capacity(ambient))
    expected = ambient + amplitude * cosine * np.exp(-diffusivity * (np.pi / .012) ** 2 * end)
    error = np.sqrt(np.sum(grid.cell_area * (solver.temperature[0] - expected) ** 2) / grid.cell_area.sum())
    assert error / amplitude < .003


def test_disabled_refinement_preserves_uniform_grid():
    regions = [('plate', box(0., 0., .004, .002), 0., .00005)]
    grid = PlateGrid.from_regions(regions, .5, refinement_paths=[[[0., .001], [.004, .001]]],
                                  weld_spacing_mm=None)
    assert grid.dx == pytest.approx(np.full(8, .0005))
    assert grid.dy == pytest.approx(np.full(4, .0005))
    assert grid.cell_area == pytest.approx(np.full((4, 8), .0005 ** 2))


def test_nonuniform_face_flux_matches_series_resistance(material):
    grid = refined_grid()
    layer = grid.layers[0]
    a, b, length, half_a, half_b = layer.edges[0]
    temperature = 300 + 1e4 * grid.centers[..., 0].ravel()
    conductivity = material.conductivity(temperature)
    conductance = length / (half_a / conductivity[a] + half_b / conductivity[b])
    widths_a = np.broadcast_to(grid.dx, grid.cells.shape).ravel()[a]
    widths_b = np.broadcast_to(grid.dx, grid.cells.shape).ravel()[b]
    expected = length / (widths_a / (2 * layer.thickness.ravel()[a] * conductivity[a])
                         + widths_b / (2 * layer.thickness.ravel()[b] * conductivity[b]))
    assert conductance == pytest.approx(expected)
    assert np.any(abs(widths_a - widths_b) > 1e-8)


def test_nonuniform_layer_exchange_preserves_enthalpy(material):
    grid = refined_grid(two=True)
    solver = ThermalSolver(grid, material, {'gap_h': 400.})
    solver.temperature[0] = 500.
    solver.temperature[1] = 300.
    initial = solver.total_enthalpy()
    solver.step(.01)
    assert solver.total_enthalpy() == pytest.approx(initial, rel=2e-10)
    assert solver.temperature[0].max() < 500.
    assert solver.temperature[1].min() > 300.
