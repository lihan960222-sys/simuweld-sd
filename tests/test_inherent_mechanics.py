from io import BytesIO
import json
from pathlib import Path
from time import monotonic, sleep
from zipfile import ZipFile

from fastapi.testclient import TestClient
import numpy as np

from sfmarc_workbench.server import create_app
from reference_data import reference_path


def wait(client, headers, job):
    deadline = monotonic() + 360
    while monotonic() < deadline:
        response = client.get('/api/inherent-strain/status', headers=headers, params={'job_id': job})
        assert response.status_code == 200, response.text
        state = response.json()
        if state['status'] not in ('running', 'cancelling'):
            assert state['status'] in ('ready', 'complete'), state
            return state
        sleep(.05)
    raise AssertionError(f'真实 C0002 机械计算超时：{state}')


def test_actual_c0002_mechanics_api_export():
    source = reference_path('SFMARC_TEST_C0002_DAT')
    material = reference_path('SFMARC_TEST_MATERIAL')
    headers = {'X-Session-Token': 'mechanics-test'}
    with TestClient(create_app('mechanics-test'), base_url='http://127.0.0.1:8765') as client:
        response = client.post('/api/model', headers=headers, data={'root': source.name},
                               files={'files': (source.name, source.read_bytes())})
        assert response.status_code == 200, response.text
        ident = response.json()['id']
        headers['X-Model-Id'] = ident
        spec = dict(model_id=ident, revision=1)
        response = client.post('/api/inherent-strain/prepare', headers=headers,
                               json={**spec, 'length_unit': 'm', 'spacing_mm': 2., 'weld_spacing_mm': 2.})
        assert response.status_code == 200, response.text
        job = response.json()['job_id']
        assert wait(client, headers, job)['status'] == 'ready'
        response = client.post('/api/inherent-strain/material', headers=headers, data=spec,
                               files={'material': (material.name, material.read_bytes())})
        assert response.status_code == 200, response.text
        response = client.post('/api/inherent-strain/start', headers=headers,
                               json={**spec, 'job_id': job, 'end_s': .001, 'dt_s': .001,
                                     'record_interval_s': .001, 'mechanics': True})
        assert response.status_code == 200, response.text
        assert wait(client, headers, job)['status'] == 'complete'
        response = client.get('/api/inherent-strain/mechanics', headers=headers, params={'job_id': job})
        assert response.status_code == 200, response.text
        mechanics = response.json()
        assert np.isfinite(mechanics['displacement_mm']).all()
        assert mechanics['summary']['structure']['base_nodes'] > 0
        assert mechanics['summary']['structure']['cover_nodes'] > 0
        assert mechanics['summary']['residual_N'] < 1e-7
        response = client.get('/api/inherent-strain/export', headers=headers, params={'job_id': job})
        assert response.status_code == 200, response.text
        with ZipFile(BytesIO(response.content)) as archive:
            assert json.loads(archive.read('mechanics.json')) == mechanics
            history = json.loads(archive.read('equilibrium.json'))
            assert history
            initial = [item for item in history if item.get('stage') == 'initial_equilibrium']
            assert initial and initial[-1]['gravity_fraction'] == 1.
            loading = [item for item in history if item.get('stage') == 'inherent_loading']
            assert loading and loading[-1]['load_fraction'] == 1.
            assert np.all(np.diff([item['load_fraction'] for item in loading]) > 0)
            assert all(item['active_weld_rows'] == initial[0]['active_weld_rows'] for item in history)
            with np.load(BytesIO(archive.read('mechanics.npz')), allow_pickle=False) as values:
                assert all(values[name].dtype != object for name in values.files)
                assert np.isfinite(values['displacement_rotation']).all()
                assert values['plastic_strain_mandel'].shape[1:] == (4, 5, 6)
                np.testing.assert_array_equal(values['weld_reference'], 0.)
                for name in ('extrapolated', 'temperature_extrapolated', 'plastic_extrapolated'):
                    assert name in values
                    assert values[name].dtype == bool
                normal = values['displacement_rotation'][values['connectivity'], 2].mean(axis=1)*1000
                preview_normal = np.asarray(mechanics['normal_displacement_mm'])
                assert preview_normal.size > 0 and np.isfinite(preview_normal).all()
                assert preview_normal.min() >= normal.min() - 1e-10
                assert preview_normal.max() <= normal.max() + 1e-10
