from pathlib import Path

import numpy as np

from sfmarc_workbench import local_shell_results as results
from sfmarc_workbench import local_shell_equivalent as equivalent
from sfmarc_workbench.local_result_archive import analyze_archive
import json


from reference_data import reference_path


ROOT = reference_path('SFMARC_TEST_LOCAL_RESULTS')


def test_actual_gauss_positions_and_engineering_shear_fit_displacements():
    for component in (1, 2):
        path = ROOT / 'increments/00000062' / f'group_1_component_{component}_62.unv'
        value = results.read_component(path)
        check = results.validate_locations(value)
        assert check['relative_rms'] < .04
        assert check['engineering_shear']
        assert len(value['plastic']) in (2579, 6604)
        assert np.all(value['weights_mm3'] > 0)


def test_actual_representative_preserves_membrane_and_bending_moments():
    for component in (1, 2):
        path = ROOT / 'increments/00000062' / f'group_1_component_{component}_62.unv'
        value = results.read_component(path)
        line = [[-30.743062083696, -20.911308910363, 992.5490003445],
                [-30.743062083696, -17.911308910363, 992.5490003445]]
        profile = equivalent.representative(value, line, 1., [0., 0., 1.], bins=16)
        assert profile['sample_volume_mm3'] > 0
        assert profile['moment_error'] < 1e-11
        assert len(profile['bands']) > 0
        assert profile['thickness_mm'] > 0
        assert all(len(row['membrane']) == len(row['curvature_per_mm']) == 3 for row in profile['bands'])


def test_actual_archive_two_increment_profiles_have_no_target_node_mapping():
    plan = json.loads((ROOT / 'model-match.json').read_text(encoding='utf-8'))['paths']
    result = analyze_archive(reference_path('SFMARC_TEST_LOCAL_ARCHIVE'),
                             ROOT, local_plan=plan, final_time_s=2.6, bins=16)
    assert result['source_to_target_node_mapping'] is False
    assert [row['increment'] for row in result['states']] == [0, 62, 123]
    assert result['final_cooling_complete'] is False
    assert 32 < result['final_temperature_max_c'] < 33
    assert result['equivalent_id']
    for state in result['states'][1:]:
        assert len(state['components']) == 2
        assert all(row['profile']['moment_error'] < 1e-11 for row in state['components'])
    (ROOT / 'shell-equivalent.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
