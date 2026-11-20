import ast
import json
from pathlib import Path

import numpy as np

from sfmarc_workbench.templates.shell_mapping import path_segments, smooth_fraction
from sfmarc_workbench.local_conservative import pieces
from shapely.geometry import LineString
from shapely.ops import unary_union
from sfmarc_workbench.local_abaqus_export import selected_paths


from reference_data import reference_path


TEMPLATES = Path(__file__).resolve().parents[1] / 'src/sfmarc_workbench/templates'


def real_plan():
    ROOT = reference_path('SFMARC_TEST_LOCAL_RESULTS')
    return json.loads((ROOT / 'model-match.json').read_text(encoding='utf-8'))['paths']['paths']


def test_actual_welds_split_into_one_mm_segments_without_length_change():
    paths = real_plan()
    segments = path_segments(paths, 1.)
    assert len(segments) == 6
    for path in paths:
        selected = [row for row in segments if row['path_id'] == path['id']]
        assert np.isclose(sum(row['length_mm'] for row in selected), path['length_mm'])
        assert np.allclose(selected[0]['points_mm'][0], path['points_mm'][0])
        assert np.allclose(selected[-1]['points_mm'][-1], path['points_mm'][-1])


def test_actual_two_pass_increment_schedule_and_step_partition():
    paths = real_plan()
    schedule = path_segments(paths, 1.)
    assert len(schedule) == 6
    assert schedule[3]['start_s'] >= paths[1]['start_s']
    for row in schedule:
        grid = np.linspace(0., 3., 1001)
        increments = [smooth_fraction(last, row['start_s'], row['end_s']) - smooth_fraction(first, row['start_s'], row['end_s'])
                      for first, last in zip(grid[:-1], grid[1:])]
        assert np.isclose(sum(increments), 1.)
        assert min(increments) >= 0


def test_actual_order_edit_does_not_mutate_source():
    paths = real_plan()
    original = json.dumps(paths)
    ordered = selected_paths(paths, [paths[1]['id'], paths[0]['id']], [paths[1]['id']])
    assert json.dumps(paths) == original
    assert ordered[0]['id'] == paths[1]['id']
    assert ordered[0]['points_mm'][0] == paths[1]['points_mm'][-1]
    assert ordered[1]['start_s'] >= ordered[0]['end_s']


def test_abaqus_templates_use_old_python_syntax_and_fixed_fortran_columns():
    for name in ('shell_mapping.py', 'prepare_abaqus_model.py'):
        code = (TEMPLATES / name).read_text(encoding='utf-8')
        ast.parse(code, feature_version=(3, 4))
        assert 'f"' not in code
    lines = (TEMPLATES / 'local_shell_loading.for').read_text(encoding='utf-8').splitlines()
    assert all(len(line) <= 72 for line in lines)


def test_actual_adjacent_one_mm_regions_share_boundary_without_double_area():
    ROOT = reference_path('SFMARC_TEST_LOCAL_RESULTS')
    path = real_plan()[0]
    line = LineString(np.asarray(path['points_mm'])[:, :2])
    equivalent = json.loads((ROOT / 'shell-equivalent.json').read_text(encoding='utf-8'))
    bands = equivalent['states'][1]['components'][0]['profile']['bands']
    band = max(bands, key=lambda row: np.linalg.norm(row['membrane']))
    complete, regions = pieces(line, band['r_min_mm'], band['r_max_mm'], 1.)
    assert np.isclose(sum(row[3].area for row in regions), complete.area)
    assert np.isclose(unary_union([row[3] for row in regions]).area, complete.area)
    for index, first in enumerate(regions):
        for last in regions[index + 1:]:
            assert first[3].intersection(last[3]).area < 1e-12
