from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

from fortranformat import FortranRecordWriter
import numpy as np

from sfmarc_workbench.deck import Deck
from sfmarc_workbench.contact_geometry import read_rigid_surfaces
from sfmarc_workbench.geometry import SurfaceModel
from sfmarc_workbench.inherent_strain import TemperatureJob
from sfmarc_workbench.plate_material import Material
from test_plate_units import write_plate_dat
from reference_data import reference_path


def write_physical_dat(directory, duration_s=.001, radius_mm=.4, power_W=500.):
    directory.mkdir(parents=True, exist_ok=True)
    write_plate_dat(directory, 'm', duration_s=duration_s, radius_mm=radius_mm, source_kind=5, power_W=power_W)
    records = ['contact 2']

    def record(format_string, values):
        records.append(FortranRecordWriter(format_string).write(values))

    record('16I10', [2] + [0] * 15)
    for _ in range(3):
        record('16I10', [0] * 16)
    # 物理基准模型：4×2 mm 双层板，下层厚度 0.421 mm，上层厚度 0.08 mm，间隙 0.03 mm。
    # Base 位于底面，Cover 位于顶面。
    for ident, name, height, order in [(1, 'Base', 0., [1, 2, 3, 4]),
                                       (2, 'Cover', .000531, [4, 3, 2, 1])]:
        record('8I10,A80', [ident, 1, 0, 0, 0, 0, 0, 0, name])
        for _ in range(8):
            record('16I10', [0] * 16)
        record('16I10', [7, 1, 4, 0] + [0] * 12)
        record('16I10', [1, 4, *order] + [0] * 10)
        for node, (x, y) in enumerate([(0., 0.), (.004, 0.), (.004, .002), (0., .002)], 1):
            record('I10,3E20.12', [node, x, y, height])
    text = (directory / 'two_plates_m.dat').read_text(encoding='utf-8')
    text = text.replace('end option\n', '\n'.join(records) + '\nend option\n', 1)
    (directory / 'physical.dat').write_text(text, encoding='utf-8')
    return Deck({'physical.dat': text.encode()}, 'physical.dat')


def test_physical_temperature_inherent_shell_cooling_release_export():
    directory = Path(__file__).resolve().parents[1] / '.work/temperature-job-physical'
    model = SurfaceModel(write_physical_dat(directory))
    assert [name for name, _, _ in read_rigid_surfaces(model.deck)] == ['Base', 'Cover']
    job = TemperatureJob(model, 'physical-plates', 1,
                         dict(length_unit='m', spacing_mm=1., weld_spacing_mm=.5, refine_distance_mm=.5))
    job.prepare()
    material_path = reference_path('SFMARC_TEST_MATERIAL')
    job.material = Material.from_xmt(material_path.read_bytes())
    job.material_info = dict(filename=material_path.name)
    source = job.grid.plan['paths'][0]['sources'][0]
    settings = dict(ambient_c=25., initial_c=25., convection_h=2000., emissivity=.2,
                    contact_h=200., gap_h=20., weld_h=10000., end_s=.001, dt_s=.001,
                    record_interval_s=.001, mechanics=True)
    job.run(lambda: job.calculate(settings))
    for _ in range(24):
        job.thread.join(5)
        if not job.thread.is_alive():
            break
    if job.thread.is_alive():
        job.cancel.set()
        job.thread.join(10)
    assert job.state['status'] == 'complete', job.status()
    result = job.mechanical_arrays
    history = result['history']
    loading = [row for row in history if row['stage'] == 'inherent_loading']
    assert job.state['status'] == 'complete'
    assert job.mechanical_result['released'] and not result['cover']
    assert len(loading) > 1 and loading[-1]['load_fraction'] == 1.
    assert np.all(np.diff([row['load_fraction'] for row in loading]) > 0)
    assert all(row['cover'] for row in loading)
    assert all(row['converged'] for row in history)
    assert all(row['time_s'] == job.frames[-1][0] for row in history)
    assert history[-1]['stage'] == 'released'
    unloading = [row for row in history if row['stage'] == 'cover_unloading']
    assert unloading[-1]['release_fraction'] == 1.
    assert all(row['active_weld_rows'] == len(job.mechanical_structure.weld_times) for row in history)
    assert len(job.mechanical_structure.weld_times) > 0
    assert history[-1]['residual_N'] < 1e-7
    assert np.max(np.abs(result['plastic'])) > 0
    assert np.max(result['equivalent']) > 0
    assert np.max(np.abs(result['q'])) > 0
    assert job.frames[-1][0] > job.grid.plan['end_s']
    assert np.max(job.frames[-1][1]) <= settings['ambient_c'] + 5.
    output = job.export()
    (directory / 'physical-result.zip').write_bytes(output)
    with ZipFile(BytesIO(output)) as archive:
        metadata = json.loads(archive.read('parameters.json'))
        mechanics = json.loads(archive.read('mechanics.json'))
        assert metadata['full_weld_completed']
        assert mechanics['released']
        with np.load(BytesIO(archive.read('mechanics.npz'))) as arrays:
            assert arrays['plastic_strain_mandel'].shape[-2:] == (5, 6)
            assert all(np.isfinite(arrays[name]).all() for name in arrays.files)
            np.testing.assert_array_equal(arrays['plastic_strain_mandel'], result['plastic'])
        with np.load(BytesIO(archive.read('temperature.npz'))) as arrays:
            np.testing.assert_array_equal(arrays['times_s'], [frame[0] for frame in job.frames])
        assert json.loads(archive.read('equilibrium.json'))[-1]['residual_N'] < 1e-7
    report = dict(model='4x2_mm_two_plate_physical_benchmark', material=material_path.name,
                  thermal_cells=len(job.active), shell_elements=len(result['plastic']),
                  source=source, settings=settings,
                  maximum_temperature_c=float(job.peak.max()), final_time_s=float(job.frames[-1][0]),
                  final_temperature_c=float(job.frames[-1][1].max()),
                  maximum_plastic_strain=float(np.max(np.abs(result['plastic']))),
                  maximum_equivalent_strain=float(result['equivalent'].max()),
                  maximum_displacement_mm=float(np.max(np.abs(result['q'][:, :3])) * 1000),
                  residual_N=float(history[-1]['residual_N']), history=history)
    (directory / 'physical-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key not in ('source', 'settings', 'history')}), flush=True)
