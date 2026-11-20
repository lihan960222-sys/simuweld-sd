from io import BytesIO
import json
from pathlib import Path
from time import monotonic, sleep
from zipfile import ZipFile

from fastapi.testclient import TestClient
import numpy as np
import pytest

from sfmarc_workbench.server import create_app
from sfmarc_workbench.routes_inherent_strain import PreviewInput, CalculationInput
from test_plate_units import write_plate_dat
from reference_data import reference_path


def wait(client, headers, job, timeout=180):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        response = client.get('/api/inherent-strain/status', headers=headers, params={'job_id': job})
        assert response.status_code == 200, response.text
        state = response.json()
        if state['status'] not in ('running', 'cancelling') and not state['can_stop']:
            return state
        sleep(.03)
    pytest.fail('后台任务未在规定时间完成')


def test_finite_strict_parameters():
    preview = PreviewInput(model_id='model', revision=1, length_unit='m')
    assert (preview.spacing_mm, preview.weld_spacing_mm, preview.refine_distance_mm) == (None, None, None)
    for field in ('spacing_mm', 'weld_spacing_mm', 'refine_distance_mm'):
        for spacing in (0, -1, float('nan'), float('inf'), '0.5'):
            with pytest.raises(ValueError):
                PreviewInput(model_id='model', revision=1, length_unit='m', **{field: spacing})
        with pytest.raises(ValueError):
            CalculationInput(model_id='model', revision=1, job_id='job', end_s=.001, **{field: .5})
    with pytest.raises(ValueError):
        CalculationInput(model_id='model', revision=1, job_id='job', end_s=.001, emissivity=1.1)


def test_actual_dat_preview_temperature_export_and_invalidation():
    directory = Path(__file__).resolve().parents[1] / '.work/dat-workflow/api'
    directory.mkdir(parents=True, exist_ok=True)
    write_plate_dat(directory, 'm', radius_mm=1.)
    source = directory / 'two_plates_m.dat'
    material = reference_path('SFMARC_TEST_MATERIAL')
    headers = {'X-Session-Token': 'inherent-test'}
    with TestClient(create_app('inherent-test'), base_url='http://127.0.0.1:8765') as client:
        response = client.post('/api/model', headers=headers, data={'root': source.name},
                               files={'files': (source.name, source.read_bytes())})
        assert response.status_code == 200, response.text
        headers['X-Model-Id'] = response.json()['id']
        spec = dict(model_id=headers['X-Model-Id'], revision=1, length_unit='m', spacing_mm=.5,
                    weld_spacing_mm=.5)
        assert client.post('/api/inherent-strain/prepare', headers=headers, json={**spec, 'model_id': 'old'}).status_code == 409
        response = client.post('/api/inherent-strain/prepare', headers=headers, json=spec)
        assert response.status_code == 200, response.text
        job = response.json()['job_id']
        assert client.post('/api/inherent-strain/prepare', headers=headers, json=spec).status_code == 409
        state = wait(client, headers, job)
        assert state['status'] == 'ready', state
        preview = client.get('/api/inherent-strain/grid', headers=headers, params={'job_id': job}).json()
        assert preview['cell_count'] > 10 and preview['paths'] and preview['layers']
        assert preview['spacing_mm'] == .5
        response = client.post('/api/inherent-strain/material', headers=headers, data={'model_id': spec['model_id'], 'revision': 1},
                               files={'material': (material.name, material.read_bytes())})
        assert response.status_code == 200, response.text
        calculation = dict(model_id=spec['model_id'], revision=1, job_id=job, end_s=.002, dt_s=.001,
                           record_interval_s=.001, mechanics=False)
        response = client.post('/api/inherent-strain/start', headers=headers, json=calculation)
        assert response.status_code == 200, response.text
        state = wait(client, headers, job)
        assert state['status'] == 'complete', state
        assert state['steps'] > 0 and state['time_s'] == pytest.approx(.002)
        frames = [client.get('/api/inherent-strain/frame', headers=headers,
                            params={'job_id': job, 'index': index}).json() for index in range(state['frames'])]
        peak = client.get('/api/inherent-strain/peak', headers=headers, params={'job_id': job}).json()
        assert np.all(np.array(peak['temperature_c']) >= np.max([frame['temperature_c'] for frame in frames], axis=0))
        response = client.get('/api/inherent-strain/export', headers=headers, params={'job_id': job})
        assert response.status_code == 200, response.text
        with ZipFile(BytesIO(response.content)) as archive:
            metadata = json.loads(archive.read('parameters.json'))
            assert metadata['status'] == 'complete' and not metadata['full_weld_completed']
            assert len(metadata['material']['sha256']) == 64
            with np.load(BytesIO(archive.read('temperature.npz')), allow_pickle=False) as data:
                np.testing.assert_array_equal(data['temperature_c'], [frame['temperature_c'] for frame in frames])
                np.testing.assert_array_equal(data['peak_temperature_c'], peak['temperature_c'])
                np.testing.assert_array_equal(data['times_s'], [frame['time_s'] for frame in frames])
                assert all(data[name].dtype != object for name in data.files)
        response = client.post('/api/inherent-strain/start', headers=headers, json={**calculation, 'record_interval_s': .1})
        assert response.status_code == 200, response.text
        assert wait(client, headers, job)['frames'] == 2
        sparse = client.get('/api/inherent-strain/peak', headers=headers, params={'job_id': job}).json()
        np.testing.assert_array_equal(sparse['temperature_c'], peak['temperature_c'])
        response = client.post('/api/inherent-strain/start', headers=headers, json={**calculation, 'end_s': 100.})
        assert response.status_code == 200, response.text
        assert client.post('/api/inherent-strain/start', headers=headers, json=calculation).status_code == 409
        assert client.post('/api/inherent-strain/cancel', headers=headers, json={'model_id': spec['model_id'], 'revision': 1, 'job_id': job}).status_code == 200
        assert wait(client, headers, job)['status'] == 'cancelled'
        assert client.post('/api/inherent-strain/invalidate', headers=headers, json={'model_id': spec['model_id'], 'revision': 2}).status_code == 200
        assert client.get('/api/inherent-strain/grid', headers=headers, params={'job_id': job}).status_code == 409
        response = client.post('/api/inherent-strain/prepare', headers=headers,
                               json={**spec, 'revision': 2, 'weld_spacing_mm': 1.})
        failed_job = response.json()['job_id']
        failed = wait(client, headers, failed_job)
        assert failed['status'] == 'failed', failed
        assert failed['grid_parameters']['weld_spacing_mm'] == 1.
        response = client.post('/api/inherent-strain/material', headers=headers,
                               data={'model_id': spec['model_id'], 'revision': 2},
                               files={'material': (material.name, material.read_bytes())})
        assert response.status_code == 200, response.text
        assert wait(client, headers, failed_job)['status'] == 'failed'
        response = client.post('/api/inherent-strain/prepare', headers=headers, json={**spec, 'revision': 2})
        assert wait(client, headers, response.json()['job_id'])['status'] == 'ready'
        assert client.post('/api/local-trial/export', headers=headers, json={'model_id': 'old', 'groups': []}).status_code == 422
        assert client.post('/api/model/clear', headers=headers).status_code == 200
        assert client.get('/api/inherent-strain/status', headers=headers, params={'job_id': job}).status_code == 409
