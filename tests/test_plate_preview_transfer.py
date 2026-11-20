from pathlib import Path

import numpy as np
from shapely import box

from sfmarc_workbench.inherent_strain import TemperatureJob
from sfmarc_workbench.plate_geometry import PlateGrid
from sfmarc_workbench.plate_material import Material, MaterialState
from sfmarc_workbench.plate_mechanics import Mechanics
from sfmarc_workbench.plate_structure import Structure
from reference_data import reference_path


def test_refined_temperature_to_independent_shell_preview():
    material = Material.from_xmt(reference_path('SFMARC_TEST_MATERIAL').read_bytes())
    polygon = box(0., 0., .004, .003)
    grid = PlateGrid.from_regions([('plate', polygon, 0., .0001)], 1.,
                                  refinement_paths=[np.array([[.001, .001], [.003, .001]])],
                                  weld_spacing_mm=.25, refine_distance_mm=.25)
    structure = Structure(grid, material, 1.)
    assert structure.grid is not grid
    assert len(structure.connectivity) < grid.layers[0].mask.sum()
    structure.fixture_surfaces['base'] = [(polygon, np.zeros(3))]
    structure.contact, structure.gaps, structure.contact_k, structure.contact_types = structure.contacts()
    state = MaterialState.zeros((len(structure.connectivity), 4, 5), 298.15)
    state.plastic[..., :2] = -.001
    state.plastic[..., 2] = .002
    state.equivalent[:] = .002
    mechanics = Mechanics(structure, material, inherent_state=state)
    mechanics.apply_inherent()
    job = TemperatureJob(None, 'physical-plate', 0, {})
    job.grid = grid
    cells, positions, triangles = [], [], []
    for flat in np.flatnonzero(grid.layers[0].mask):
        j, i = np.unravel_index(flat, grid.cells.shape)
        cells.append([0, int(flat)])
        xy = np.array([[grid.x[i], grid.y[j]], [grid.x[i+1], grid.y[j]], [grid.x[i+1], grid.y[j+1]]])
        positions.extend(np.c_[xy, np.full(3, .0001)].ravel() * 1000)
        triangles.append(len(cells) - 1)
    job.preview = dict(cells=cells, positions_mm=positions, triangle_cells=triangles)
    preview = job.mechanics_preview(mechanics)
    np.testing.assert_allclose(np.asarray(preview['plastic_strain'])[:, :2], -.001, atol=1e-12)
    np.testing.assert_allclose(preview['equivalent_plastic_strain'], .002, atol=1e-12)
    displacement = np.asarray(preview['displacement_mm']).reshape(-1, 3)
    assert np.isfinite(displacement).all()
    assert np.ptp(displacement[:, 0]) > .003
    areas = grid.layers[0].area[grid.layers[0].mask]
    np.testing.assert_allclose(np.dot(preview['equivalent_plastic_strain'], areas),
                               np.dot(state.equivalent[:, 0, 0], structure.area), rtol=1e-12)
