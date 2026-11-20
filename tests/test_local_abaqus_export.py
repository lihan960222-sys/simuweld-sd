from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from sfmarc_workbench.local_abaqus_export import read_shell, export_abaqus


from reference_data import reference_path


ROOT = reference_path('SFMARC_TEST_LOCAL_RESULTS')


def test_real_local_mid_surface_import_and_full_calculation_bundle():
    source = ROOT / 'local-shell-geometry.inp'
    model = read_shell(source.read_bytes(), source.name, 'mm')
    assert len(model['preview']['bodies']) == 6
    assert model['preview']['face_count'] == 7653
    equivalent = json.loads((ROOT / 'shell-equivalent.json').read_text(encoding='utf-8'))
    paths = json.loads((ROOT / 'model-match.json').read_text(encoding='utf-8'))['paths']['paths']
    bodies = sorted(model['preview']['bodies'], key=lambda body: body['min_mm'][2])
    middle = (bodies[0]['min_mm'][2] + bodies[-1]['min_mm'][2]) / 2
    mapping = {str(body['id']): equivalent['component_names'][int(body['min_mm'][2] > middle)] for body in bodies}
    content = export_abaqus(model, equivalent, paths, mapping, 'SD_ActualLocal')
    with ZipFile(BytesIO(content)) as archive:
        assert archive.testzip() is None
        assert archive.read('source_model.inp') == source.read_bytes()
        settings = json.loads(archive.read('sd_loading.json'))
        assert len(settings['segments']) == 6
        assert len(settings['regions']) == 6
        assert settings['conservation']['area_integral_error'] < 1e-10
        assert settings['equivalent']['final_cooling_complete'] is False
        assert settings['validation_status']
        for name in ('prepare_abaqus_model.py', 'shell_mapping.py'):
            compile(archive.read(name), name, 'exec')
        assert b'SUBROUTINE UEXPAN' in archive.read('local_shell_loading.for')
    (ROOT / 'actual-local-abaqus-package.zip').write_bytes(content)


def test_real_shell_model_rejects_invalid_units_and_empty_plate_mapping():
    source = ROOT / 'local-shell-geometry.inp'
    with pytest.raises(ValueError, match='单位'):
        read_shell(source.read_bytes(), source.name, 'inch')
    model = read_shell(source.read_bytes(), source.name, 'mm')
    equivalent = json.loads((ROOT / 'shell-equivalent.json').read_text(encoding='utf-8'))
    paths = json.loads((ROOT / 'model-match.json').read_text(encoding='utf-8'))['paths']['paths']
    with pytest.raises(ValueError, match='对应'):
        export_abaqus(model, equivalent, paths, {}, 'SD_ActualLocal')


def test_requested_two_plate_test_inp_preserves_shared_nodes_and_exports_two_passes():
    source = ROOT / 'SD_TwoPlates_Test.inp'
    model = read_shell(source.read_bytes(), source.name, 'mm')
    assert len(model['preview']['bodies']) == 2
    assert model['preview']['face_count'] == 2598
    equivalent = json.loads((ROOT / 'shell-equivalent.json').read_text(encoding='utf-8'))
    paths = json.loads((ROOT / 'model-match.json').read_text(encoding='utf-8'))['paths']['paths']
    mapping = {str(body['id']): equivalent['component_names'][body['id']] for body in model['preview']['bodies']}
    content = export_abaqus(model, equivalent, paths, mapping, 'SD_TwoPlates')
    with ZipFile(BytesIO(content)) as archive:
        value = json.loads(archive.read('sd_loading.json'))
        assert value['conservation']['area_integral_error'] < 1e-10
        events = [event for element in value['element_loads'].values() for event in element['events']]
        assert {event['pass'] for event in events} == {1, 2}
        assert {event['path_id'] for event in events} == {path['id'] for path in paths}
        assert any(len(row['events']) > 2 for row in value['element_loads'].values())
        assert archive.read('source_model.inp') == source.read_bytes()
    (ROOT / 'SD_TwoPlates_Abaqus.zip').write_bytes(content)
