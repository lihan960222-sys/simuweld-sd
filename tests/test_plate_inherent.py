from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from sfmarc_workbench.plate_inherent import constrained_thermal_update, replicate_thickness_state
from sfmarc_workbench.plate_material import Material, MaterialState, plane_stress_update
from reference_data import reference_path


@pytest.fixture(scope="module")
def material():
    return Material.from_xmt(reference_path('SFMARC_TEST_MATERIAL').read_bytes())


def test_constrained_history_matches_full_j2_with_reverse_yield(material):
    temperatures = np.array([
        [298.15, 298.15, 298.15, 298.15],
        [573.15, 873.15, 1073.15, 1311.15],
        [1073.15, 1273.15, 1355.15, 2273.15],
        [573.15, 1073.15, 1273.15, 1311.15],
        [298.15, 298.15, 298.15, 298.15],
    ])
    state = MaterialState.zeros((4,))
    full = MaterialState.zeros((4,))
    equivalents, signed, stresses = [], [], []
    for temperature in np.tile(temperatures, (5, 1)):
        stress, state, ezz = constrained_thermal_update(material, temperature, state)
        expected_stress, _, full, expected_ezz = plane_stress_update(material, np.zeros((4, 3)), temperature, full)
        np.testing.assert_allclose(stress, expected_stress, rtol=2e-10, atol=0.02)
        np.testing.assert_allclose(state.plastic, full.plastic, atol=2e-12)
        np.testing.assert_allclose(state.equivalent, full.equivalent, atol=2e-12)
        np.testing.assert_allclose(ezz, expected_ezz, atol=2e-12)
        equivalents.append(state.equivalent.copy())
        signed.append(state.plastic[:, 0].copy())
        stresses.append(stress[:, 0])
    assert np.all(np.diff(equivalents, axis=0) >= 0)
    assert np.any(np.diff(signed, axis=0) > 0)
    assert np.any(np.diff(signed, axis=0) < 0)
    assert np.min(stresses) < -1e6
    assert np.max(stresses) > 1e6
    assert np.max(state.equivalent) > material.plastic_limit
    assert np.any(state.extrapolated)


def test_constant_extension_preserves_true_temperature_history(material):
    state = MaterialState.zeros((2,))
    reference = MaterialState.zeros((2,))
    for temperature in ([3400., 4022.], [1273.15, 298.15], [298.15, 298.15]):
        stress, state, ezz = constrained_thermal_update(material, temperature, state, allow_constant_extension=True)
        clipped = np.minimum(temperature, material.temperature_max)
        expected_stress, _, reference, expected_ezz = plane_stress_update(material, np.zeros((2, 3)), clipped, reference)
        np.testing.assert_allclose(stress, expected_stress, rtol=2e-10, atol=0.02)
        np.testing.assert_allclose(state.plastic, reference.plastic, atol=2e-12)
        np.testing.assert_allclose(state.equivalent, reference.equivalent, atol=2e-12)
        np.testing.assert_allclose(ezz, expected_ezz, atol=2e-12)
    np.testing.assert_array_equal(state.peak_temperature, [3400., 4022.])


def test_reference_temperature_matches_full_j2_history(material):
    reference_temperature = 373.15
    state = MaterialState.zeros((2,), reference_temperature)
    full = MaterialState.zeros((2,), reference_temperature)
    imposed = np.zeros((2, 3))
    imposed[:, :2] = material.thermal_strain(reference_temperature)
    for temperature in ([373.15, 373.15], [998.15, 1311.15], [373.15, 373.15]):
        stress, state, ezz = constrained_thermal_update(
            material, temperature, state, reference_temperature=reference_temperature)
        expected_stress, _, full, expected_ezz = plane_stress_update(material, imposed, temperature, full)
        np.testing.assert_allclose(stress, expected_stress, rtol=2e-10, atol=.02)
        np.testing.assert_allclose(state.plastic, full.plastic, atol=2e-12)
        np.testing.assert_allclose(state.equivalent, full.equivalent, atol=2e-12)
        np.testing.assert_allclose(ezz, expected_ezz - material.thermal_strain(reference_temperature), atol=2e-12)
    assert np.max(state.equivalent) > 0


def test_extension_requires_constant_properties(material):
    properties = {name: values.copy() for name, values in material._properties.items()}
    properties["youngs_modulus"][-1, 1] *= 1.1
    changed = replace(material, _properties=properties)
    with pytest.raises(ValueError, match="常值"):
        constrained_thermal_update(changed, 3400., MaterialState.zeros(), allow_constant_extension=True)


def test_thickness_states_are_independent(material):
    _, state, _ = constrained_thermal_update(material, [1073.15, 1311.15], MaterialState.zeros((2,)))
    thickness = replicate_thickness_state(state, 5)
    assert thickness.plastic.shape == (2, 5, 6)
    assert thickness.equivalent.shape == (2, 5)
    np.testing.assert_array_equal(thickness.plastic[:, 3], state.plastic)
    thickness.plastic[0, 0, 0] += 0.01
    assert thickness.plastic[0, 0, 0] != thickness.plastic[0, 1, 0]
    assert thickness.plastic[0, 1, 0] == state.plastic[0, 0]


@pytest.mark.parametrize("temperature", [293.14, np.nan, np.inf, 2273.16])
def test_invalid_temperature_fails(material, temperature):
    with pytest.raises(ValueError):
        constrained_thermal_update(material, temperature, MaterialState.zeros())


def test_non_biaxial_state_fails(material):
    state = MaterialState(np.array([0.001, 0., -0.001, 0., 0., 0.]), np.array(0.002), np.array(298.15))
    with pytest.raises(ValueError, match="等双向"):
        constrained_thermal_update(material, 298.15, state)
