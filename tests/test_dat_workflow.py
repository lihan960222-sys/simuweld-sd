from pathlib import Path
from threading import Event

import numpy as np
import pytest
from fortranformat import FortranRecordWriter

from sfmarc_workbench.deck import Deck
from sfmarc_workbench.geometry import SurfaceModel
from sfmarc_workbench.inherent_strain import TemperatureJob
from sfmarc_workbench.plate_material import Material
from sfmarc_workbench.routes_inherent_strain import PreviewInput, CalculationInput
from sfmarc_workbench.plate_thermal import ThermalSolver
from sfmarc_workbench.contact_geometry import read_thermal_conditions
from test_plate_units import write_plate_dat
from test_plate_thermal import grid
from test_temperature_job_physical import write_physical_dat
from reference_data import reference_path


def small_job():
    directory = Path(__file__).resolve().parents[1] / '.work/dat-workflow/model'
    directory.mkdir(parents=True, exist_ok=True)
    write_plate_dat(directory, 'm')
    path = directory / 'two_plates_m.dat'
    model = SurfaceModel(Deck({path.name: path.read_bytes()}, path.name))
    job = TemperatureJob(model, 'small', 1, PreviewInput(model_id='small', revision=1, length_unit='m',
                         spacing_mm=.5, weld_spacing_mm=.25, refine_distance_mm=.5).model_dump())
    job.material = Material.from_xmt(reference_path('SFMARC_TEST_MATERIAL').read_bytes())
    job.material_info = {'name': 'C19000'}
    return job


def test_defaults_and_separate_operation_clocks():
    inputs = CalculationInput(model_id='small', revision=1, job_id='small')
    assert inputs.dt_s is None and inputs.end_s is None and inputs.record_interval_s is None
    job = small_job()
    job.run(job.prepare)
    job.thread.join(30)
    state = job.status()
    assert state['status'] == 'ready', state
    assert state['operation'] == 'prepare'
    assert state['calculation_elapsed_s'] == 0
    assert state['prepare_elapsed_s'] > 0
    assert state['steps'] == 0 and state['frames'] == 0 and not state['can_stop']
    defaults = job.preview['defaults']
    assert defaults['end_s'] == pytest.approx(.01)
    assert defaults['weld_spacing_mm'] == pytest.approx(.02)
    assert defaults['initial_c'] == pytest.approx(25)
    job.run(lambda: job.calculate({**inputs.model_dump(), 'mechanics': False}))
    job.thread.join(30)
    state = job.status()
    assert state['status'] == 'complete', state
    assert state['operation'] == 'calculate' and state['calculation_elapsed_s'] > 0
    assert state['prepare_elapsed_s'] > 0
    assert state['time_s'] == pytest.approx(.01)
    assert job.calculation['end_s'] == pytest.approx(.01)
    assert state['steps'] > 0 and state['step_min_s'] > 0
    assert 'diagnostics' not in state


def test_adaptive_events_energy_and_stop():
    job = small_job()
    job.prepare()
    solver = ThermalSolver(job.grid, job.material, {}, job.grid.plan['paths'])
    initial = solver.total_enthalpy()
    stop = Event()
    iterator = solver.iter_steps(.01, .1, adaptive=True, cancelled=stop)
    first = next(iterator)[0]
    assert 0 < first < .001
    stop.set()
    with pytest.raises(InterruptedError):
        next(iterator)
    assert solver.time == first
    stop.clear()
    times = [time for time, _, _ in solver.iter_steps(.01, .1, adaptive=True, cancelled=stop)]
    assert times[-1] == pytest.approx(.01)
    assert np.diff([first, *times]).max() <= job.preview['defaults']['dt_s'] + 1e-14
    assert solver.total_enthalpy() - initial == pytest.approx(solver.energy['absorbed'], abs=1e-10)
    assert solver.energy['input'] == pytest.approx(.008, rel=1e-10)


def test_layer_environment_and_contact_coefficients():
    job = small_job()
    plate = grid(two=True)
    solver = ThermalSolver(plate, job.material,
                           dict(ambient=np.array([298.15, 308.15])[:, None, None],
                                convection_h=np.array([0., 200.])[:, None, None],
                                contact_h=[0.], gap_h=[0.], weld_h=[0.]), [])
    solver.temperature[:] = 400.
    solver.step(.01)
    np.testing.assert_allclose(solver.temperature[0], 400., atol=1e-10)
    assert np.max(solver.temperature[1]) < 400.
    assert solver.energy['loss'] > 0


def test_actual_dat_heat_transfer_read_only():
    path = reference_path('SFMARC_TEST_C0002_DAT')
    data = read_thermal_conditions(Deck({path.name: path.read_bytes()}, path.name))
    plates = [body for body in data['bodies'] if body['kind'] == 0]
    assert len(plates) == 2
    assert all(body['convection_h'] == 20. and body['emissivity'] == .6 for body in plates)
    assert all(body['ambient_c'] == pytest.approx(25.) for body in plates)
    assert len(data['pairs']) == 4
    assert sorted(pair['h'] for pair in data['pairs']) == [1000.] * 4
    assert sum(pair['equivalent'] for pair in data['pairs']) == 1
    assert data['stage_count'] > 1


def test_manual_fixture_heat_transfer_uses_default_temperature():
    directory = Path(__file__).resolve().parents[1] / '.work/dat-workflow/manual-fixture'
    model = SurfaceModel(write_physical_dat(directory, power_W=1.))
    job = TemperatureJob(model, 'fixture', 1, dict(length_unit='m', spacing_mm=1., weld_spacing_mm=.5, refine_distance_mm=.5))
    job.prepare()
    job.material = small_job().material
    job.calculate(dict(fixture_h=1000., end_s=.00001, mechanics=False))
    assert job.preview['defaults']['fixture_c'] == 25.
    assert np.min(job.frames[-1][1]) >= 25. - 1e-9


@pytest.mark.parametrize('near_h,near_epsilon', [(10., 0.), (0., .2)])
def test_unsupported_near_contact_heat_transfer_stops_preparation(near_h, near_epsilon):
    directory = Path(__file__).resolve().parents[1] / '.work/dat-workflow/near-contact'
    deck = write_physical_dat(directory, power_W=1.)
    records = ['contact table', FortranRecordWriter('2I10').write([1, 2]),
               FortranRecordWriter('I10,6E20.12,3I10').write([1] + [0.] * 6 + [0] * 3)]
    records += [FortranRecordWriter('16I10').write([0] * 16)] * 7
    records += [FortranRecordWriter('8E20.12').write([1000., near_h, 0., 0., near_epsilon, 0., 0., 0.]),
                FortranRecordWriter('8I10').write([0] * 8), FortranRecordWriter('I10').write([2])]
    text = '\n'.join(line.text for line in deck.lines).replace('end option', '\n'.join(records) + '\nend option', 1)
    with pytest.raises(ValueError, match='近接触'):
        read_thermal_conditions(Deck({'physical.dat': text.encode()}, 'physical.dat'))
