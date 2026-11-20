from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import box

from sfmarc_workbench.background import BackgroundModel
from sfmarc_workbench.contact_geometry import read_rigid_surfaces
from sfmarc_workbench.deck import Deck
from sfmarc_workbench.geometry import SurfaceModel
from sfmarc_workbench.plate_geometry import PlateGrid
from sfmarc_workbench.plate_material import Material
from sfmarc_workbench.plate_structure import Structure
from reference_data import reference_path


@pytest.fixture(scope='module')
def material():
    path = reference_path('SFMARC_TEST_MATERIAL')
    return Material.from_xmt(path.read_bytes())


def test_three_layers_mapping_contacts_and_free_constraints(material):
    grid = PlateGrid.from_regions([(f'plate{i}', box(0, 0, .004, .002), i*.0002, i*.0002+.0001)
                                   for i in range(3)], .5)
    structure = Structure(grid, material, 1.)
    assert len(structure.anchors) == 3
    assert len(structure.rigid_mode_dofs) == 3
    assert structure.cover_dofs.size == 0
    assert not np.any(structure.contact_types == 0)
    assert structure.weld.shape == (0, structure.ndof)
    np.testing.assert_allclose(structure.map @ np.ones(structure.map.shape[1]), 1.)
    np.testing.assert_allclose(structure.gaps, .0001)
    np.testing.assert_allclose(structure.contact_k.sum()*1e-6, 2*.004*.002)
    np.testing.assert_allclose(structure.gravity[2::5].sum(), -material.density*9.80665*structure.volumes.sum())


def test_disconnected_regions_expose_each_rigid_mode(material):
    grid = PlateGrid.from_regions([('plate', box(0, 0, .002, .002), 0., .0001),
                                   ('plate', box(.004, 0, .006, .002), 0., .0001)], .5)
    structure = Structure(grid, material, 1.)
    assert len(structure.anchors[0]) == 6
    assert len(structure.rigid_mode_dofs) == 2


def test_weld_depth_changes_along_path_and_is_deterministic(material):
    grid = PlateGrid.from_regions([('lower', box(0, 0, .004, .002), 0., .0001),
                                   ('upper', box(0, 0, .004, .002), .0001, .0002)], .5)
    grid.plan = {'paths': [dict(points_mm=[[0., 1., .15], [4., 1., .55]], start_s=0., end_s=1.,
                                sources=[dict(kind=5, depth_mm=.2, cutoff_mm=.3,
                                              upper_radius_mm=.2, lower_radius_mm=.1)])]}
    first = Structure(grid, material, .5)
    second = Structure(grid, material, .5)
    assert len(first.weld_times) > 0
    assert first.weld_points[:, 0].max() < .002
    np.testing.assert_array_equal(first.weld_points, second.weld_points)
    np.testing.assert_allclose(first.weld @ np.tile([1., 2., 3., 0., 0.], len(first.xy)), 0., atol=1e-14)


def test_fixture_footprint_limits_base_contact(material):
    grid = PlateGrid.from_regions([('plate', box(0, 0, .004, .002), 0., .0001)], 1.)
    structure = Structure(grid, material, 1.)
    structure.fixture_surfaces['base'] = [(box(0, 0, .001, .002), np.zeros(3))]
    contact, gaps, weights, kinds = structure.contacts()
    assert np.all(kinds == 0)
    np.testing.assert_allclose(weights.sum()*1e-6, .001*.002)
    np.testing.assert_allclose(gaps, 0.)
    columns = contact.nonzero()[1]
    assert structure.xy[columns//5, 0].max() <= .001
    structure.fixture_surfaces['base'] = [(box(0, 0, .001, .002), np.array([0., 0., .00001]))]
    with pytest.raises(ValueError, match='初始穿入'):
        structure.contacts()


def test_uploaded_inp_fixture_geometry_and_units(material):
    data = b'*HEADING\nBase\n*NODE\n1,0,0,0\n2,1,0,0\n3,1,2,0\n4,0,2,0\n*ELEMENT,TYPE=S4,ELSET=BASE\n1,1,2,3,4\n'
    fixture = BackgroundModel(data, 'base.inp', 'inp')
    fixture.length_unit = 'mm'
    grid = PlateGrid.from_regions([('plate', box(0, 0, .004, .002), 0., .0001)], 1.)
    structure = Structure(grid, material, fixture_model={'base': fixture})
    np.testing.assert_allclose(structure.contact_k.sum()*1e-6, .001*.002)
    assert structure.metadata()['fixture_surfaces']['base'] == 1


@pytest.mark.parametrize('reverse', [False, True])
def test_fixture_envelopes_preserve_visible_partial_overlap(material, reverse):
    # 两组水平接触面对应带有中央凸台的 Base 与 Cover。
    points = [(0, 0, -1), (4, 0, -1), (4, 2, -1), (0, 2, -1),
              (1, 0, 0), (3, 0, 0), (3, 2, 0), (1, 2, 0),
              (0, 0, 1.1), (4, 0, 1.1), (4, 2, 1.1), (0, 2, 1.1),
              (1, 0, .1), (3, 0, .1), (3, 2, .1), (1, 2, .1)]
    lines = ['*HEADING', 'Stepped fixtures', '*NODE']
    lines.extend(f'{index},{x},{y},{z}' for index, (x, y, z) in enumerate(points, 1))
    for owner, indices in [('Base', (1, 5)), ('Cover', (9, 13))]:
        lines.append(f'*ELEMENT,TYPE=S4,ELSET={owner}')
        for start in indices:
            nodes = list(range(start, start + 4))
            if reverse:
                nodes.reverse()
            lines.append(','.join(map(str, [start, *nodes])))
    fixture = BackgroundModel(('\n'.join(lines) + '\n').encode(), 'fixtures.inp', 'inp')
    grid = PlateGrid.from_regions([('plate', box(0, 0, .004, .002), 0., .0001)], 1.)
    names = {'base': [fixture.body_names[0], fixture.body_names[1]],
             'cover': [fixture.body_names[2], fixture.body_names[3]]}
    structure = Structure(grid, material, fixture_model=fixture, length_unit='mm', fixture_body_names=names)
    for role, exposed_height, hidden_height in [('base', 0., -.001), ('cover', .0001, .0011)]:
        surfaces = structure.fixture_surfaces[role]
        assert len(surfaces) == 2
        exposed = next(polygon for polygon, plane in surfaces if np.isclose(plane[2], exposed_height))
        outer = next(polygon for polygon, plane in surfaces if np.isclose(plane[2], hidden_height))
        assert exposed.equals(box(.001, 0, .003, .002))
        assert outer.equals(box(0, 0, .004, .002).difference(exposed))
        assert exposed.intersection(outer).area == 0.
    np.testing.assert_allclose(structure.gaps[structure.contact_types == 0].min(), 0., atol=1e-12)
    assert len(structure.cover_dofs) > 0


def test_actual_c0002_fixture_support(material):
    source = reference_path('SFMARC_TEST_C0002_DAT')
    model = SurfaceModel(Deck({source.name: source.read_bytes()}, source.name))
    surfaces = read_rigid_surfaces(model.deck)
    assert [(name, len(points), len(faces)) for name, points, faces in surfaces] == [
        ('Base2', 67983, 135958), ('cover2', 18374, 36744)]
    for _, points, _ in surfaces:
        assert np.isfinite(points).all()
    grid = PlateGrid.from_model(model, 'm', 2., weld_spacing_mm=2.)
    structure = Structure(grid, material, 2., model=model)
    base = structure.contact_types == 0
    assert np.count_nonzero(base) > 0
    assert len(structure.cover_dofs) > 0
    assert len(structure.anchors) == len(grid.layers) == 2
    assert len(structure.rigid_mode_dofs) == 2
    assert np.min(structure.gaps[base]) >= -2e-8
    assert np.max(structure.gaps[base]) < 2e-8
    np.testing.assert_allclose(structure.fixture_surfaces['base'][0][1][2],
                               grid.layers[0].regions[0].bottom, atol=1e-10)
    cover_nodes = structure.cover_dofs // 5
    assert np.all(structure.node_layers[cover_nodes] == 1)
    base_area = sum(polygon.area for polygon, _ in structure.fixture_surfaces['base'])
    assert structure.contact_k[base].sum()*1e-6 <= base_area
    np.testing.assert_allclose(structure.map.sum(axis=1), 1., atol=1e-7)
