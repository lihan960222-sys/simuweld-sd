import csv
import json
from pathlib import Path

import pytest

from sfmarc_workbench.templates import local_results_export as module


from reference_data import reference_path


ROOT = reference_path('SFMARC_TEST_LOCAL_RESULTS')


def real_times():
    with (ROOT / 'increments.csv').open(encoding='utf-8-sig', newline='') as stream:
        return {int(row['Increment']): float(row['Time_s']) for row in csv.DictReader(stream)}


def real_events():
    plan = json.loads((ROOT / 'model-match.json').read_text(encoding='utf-8'))['paths']
    return [{'name': str(path['id']), 'start_s': path['start_s'], 'end_s': path['end_s']}
            for path in plan['paths']]


def test_actual_two_pass_checkpoints_before_next_weld_and_release():
    checkpoints = module.select_checkpoints(real_times(), real_events(), 2.6)
    assert [row['increment'] for row in checkpoints] == [0, 62, 123]
    assert checkpoints[1]['time_s'] <= real_events()[1]['start_s'] + 1e-6
    assert checkpoints[-1]['time_s'] < 3


def test_actual_incomplete_results_cannot_supply_final_checkpoint():
    times = {number: time for number, time in real_times().items() if number <= 62}
    with pytest.raises(ValueError, match='完整'):
        module.select_checkpoints(times, real_events(), 2.6)


def test_actual_checkpoint_requires_saved_cooling_state():
    events = real_events()
    times = {number: time for number, time in real_times().items() if not 48 <= number <= 62}
    with pytest.raises(ValueError, match='冷却'):
        module.select_checkpoints(times, events, 2.6)
