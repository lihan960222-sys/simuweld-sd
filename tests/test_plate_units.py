from pathlib import Path

from fortranformat import FortranRecordWriter
from meshioplusplus import Mesh, write
import numpy as np
import pytest

from sfmarc_workbench.deck import Deck
from sfmarc_workbench.geometry import SurfaceModel
from sfmarc_workbench.plate_geometry import PlateGrid
from sfmarc_workbench.plate_material import Material
from sfmarc_workbench.plate_thermal import ThermalSolver
from sfmarc_workbench.thermal_deck import read_weld_paths
from reference_data import reference_path


def write_plate_dat(folder, unit, duration_s=.01, radius_mm=.08, source_kind=7, power_W=1.):
    scale = 1. if unit == 'm' else 1000.
    points = np.array([
        [x, y, z]
        for bottom, top in ((0., .000421), (.000451, .000531))
        for z in (bottom, top)
        for x, y in ((0., 0.), (.004, 0.), (.004, .002), (0., .002))
    ]) * scale
    mesh = Mesh(points, [('hexahedron', np.arange(16).reshape(2, 8))], point_sets={
        'T_cpt_lower_n_set': np.arange(8), 'T_cpt_upper_n_set': np.arange(8, 16),
    })
    filename = folder / f'two_plates_{unit}.dat'
    write(filename, mesh, file_format='marc')
    mesh_text = filename.read_text(encoding='utf-8')
    assert mesh_text.endswith('end option\n')
    records = []

    def record(format_string, values):
        records.append(FortranRecordWriter(format_string).write(values))

    # 两套单位表示相同的 4×2 mm 双层板、1 W 热源及 0.26 m/s 焊接速度。
    records.append('weld path')
    record('2I10', [1, 0])
    record('6I10,A', [1, 2, 2, 1, 0, 1, 'path'])
    record('2I10', [1, 2])
    for x in (.0007, .0033):
        record('3E20.12', np.array([x, .001, .000531]) * scale)
    record('I10,E20.12,I10', [1, 0., 0])
    for x in (.0007, .0033):
        record('3E20.12', np.array([x, .001, -.000469]) * scale)
    records.append('weld flux')
    record('4I10', [1, 0, 1, 1])
    record('6I10,A', [1, 1, source_kind, 1, 0, 0, 'heat'])
    record('6E20.12,I10,E20.12', [0., 0., 0., 0., 0., .0026 / duration_s * scale, 1, 1.2])
    values = [power_W * scale, .8, 1., radius_mm / 1000 * scale, .0001 * scale, 0., 0., radius_mm / 500 * scale]
    if source_kind == 5:
        values = [power_W * scale, .8, 1., radius_mm / 1000 * scale, .0003 * scale,
                  radius_mm / 1000 * scale, radius_mm / 2000 * scale, radius_mm / 800 * scale]
    record('8E20.12', values)
    record('8I10', [0] * 8)
    record('2I10', [0, 10])
    record('I10', [14 if source_kind == 7 else 1])
    records.extend(['T_cpt_upper_n_set', 'table'])
    record('5I10', [1, 1, 0, 0, 2])
    record('12I10', [1, 2, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1])
    record('2E20.12', [0., 1.])
    record('2E20.12', [duration_s, 1.])
    records.extend(['end option', 'loadcase welding'])
    record('I10', [1])
    records.extend(['heat', 'auto step'])
    record('2E20.12', [min(.001, duration_s), duration_s])
    filename.write_text(mesh_text.removesuffix('end option\n') + '\n'.join(records) + '\n', encoding='utf-8')
    return Deck({filename.name: filename.read_bytes()}, filename.name)


def test_m_and_mm_dat_have_equal_geometry_power_and_temperature():
    material = Material.from_xmt(reference_path('SFMARC_TEST_MATERIAL').read_bytes())
    folder = Path(__file__).resolve().parents[1] / '.work/unit-equivalence'
    folder.mkdir(parents=True, exist_ok=True)
    decks = [write_plate_dat(folder, unit) for unit in ('m', 'mm')]
    grids = [PlateGrid.from_model(SurfaceModel(deck), unit, .5) for deck, unit in zip(decks, ('m', 'mm'))]
    first, second = grids
    np.testing.assert_allclose(first.origin, second.origin, atol=1e-15, rtol=0)
    np.testing.assert_allclose(first.basis, second.basis, atol=1e-12, rtol=0)
    np.testing.assert_allclose(first.centers, second.centers, atol=1e-15, rtol=0)
    for a, b in zip(first.layers, second.layers):
        np.testing.assert_allclose(a.thickness, b.thickness, atol=1e-15, rtol=0)
        np.testing.assert_allclose(a.top, b.top, atol=1e-15, rtol=0)
        np.testing.assert_allclose(a.area, b.area, atol=1e-18, rtol=0)
    assert len(first.layers) == len(second.layers) == 2
    for grid, power_scale in zip(grids, (1., .001)):
        path = grid.plan['paths'][0]
        source = path['sources'][0]
        assert path['speed_mm_s'] == pytest.approx(260.)
        assert source['power_W'] == pytest.approx(1.)
        assert source['power_scale_W'] == power_scale
    np.testing.assert_allclose(first.plan['paths'][0]['points_mm'], second.plan['paths'][0]['points_mm'], atol=1e-12, rtol=0)

    unspecified = read_weld_paths(decks[1], 'mm')
    assert unspecified['paths'][0]['sources'][0]['power_W'] is None
    with pytest.raises(ValueError, match='热源参数或功率单位无效'):
        ThermalSolver(second, material, {}, unspecified['paths'])

    solvers = [ThermalSolver(grid, material, {'gap_h': 400.}, grid.plan['paths']) for grid in grids]
    for solver in solvers:
        for _ in range(3):
            solver.step(.001)
        assert solver.energy['input'] == pytest.approx(.8 * .003, rel=1e-12)
        assert solver.energy['absorbed'] > 0
        assert solver.temperature.max() > 298.15
    np.testing.assert_allclose(solvers[0].temperature, solvers[1].temperature, atol=1e-8, rtol=0)
    for name in solvers[0].energy:
        assert solvers[0].energy[name] == pytest.approx(solvers[1].energy[name], abs=1e-12)
    print('UNIT_EQUIVALENCE', 'cell_count', int(solvers[0].mask.sum()),
          'maximum_temperature_K', [float(solver.temperature.max()) for solver in solvers],
          'maximum_difference_K', float(np.max(np.abs(solvers[0].temperature - solvers[1].temperature))),
          'input_J', [solver.energy['input'] for solver in solvers],
          'absorbed_J', [solver.energy['absorbed'] for solver in solvers])
