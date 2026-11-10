import csv
import os
from pathlib import Path

import numpy as np
import pytest

from sfmarc_workbench.thermal import aligned_average, simplify_curve


SOURCE = Path(os.environ.get("SFMARC_TEST_TC_CSV", "C:/Users/60066457/Downloads/C0002_node_temperatures.csv"))
pytestmark = pytest.mark.skipif(not SOURCE.is_file(), reason="需要真实节点温度 CSV")


@pytest.fixture(scope="module")
def measurements():
    ids = (45318, 21699, 22310)
    records = {}
    with SOURCE.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            node = int(row["SolverNodeID"])
            if node in ids:
                records.setdefault(float(row["Time_s"]), {})[node] = float(row["Temperature_C"])
    times = np.asarray(sorted(records))
    values = np.asarray([[records[time][node] for node in ids] for time in times])
    return times, values


def test_average_uses_shifted_real_histories(measurements):
    times, values = measurements
    arrivals = np.asarray([.467122587097, 1.08541151169, 1.55978722662])
    shifted, mean = aligned_average(times, values, arrivals)
    assert shifted[0] == pytest.approx(max(times[0] - arrivals))
    assert shifted[-1] == pytest.approx(min(times[-1] - arrivals))
    expected = np.asarray([sum(np.interp(time + arrival, times, values[:, index])
                               for index, arrival in enumerate(arrivals)) / len(arrivals)
                           for time in shifted])
    np.testing.assert_allclose(mean, expected, rtol=1e-12, atol=1e-10)
    assert np.any(np.abs(mean - np.interp(shifted + arrivals[0], times, values[:, 0])) > 1)
    indices, error = simplify_curve(shifted, mean, 5)
    assert error <= 5
    assert mean[indices].max() == mean.max()


def test_average_single_real_node_and_no_extrapolation(measurements):
    times, values = measurements
    shifted, mean = aligned_average(times, values[:, :1], np.asarray([.467122587097]))
    np.testing.assert_allclose(shifted, times - .467122587097)
    np.testing.assert_allclose(mean, values[:, 0])
    with pytest.raises(ValueError, match="共同"):
        aligned_average(times, values[:, :2], np.asarray([0., times[-1] + 1]))


def test_average_respects_segment_activation_window(measurements):
    times, values = measurements
    arrivals = np.asarray([.467122587097, 1.08541151169, 1.55978722662])
    relative, mean = aligned_average(times, values, arrivals, (-.01, .01))
    assert relative[0] == -.01 and relative[-1] == .01
    expected = sum(np.interp(relative + arrival, times, values[:, index])
                   for index, arrival in enumerate(arrivals)) / len(arrivals)
    np.testing.assert_allclose(mean, expected, rtol=1e-12, atol=1e-9)
