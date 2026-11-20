from dataclasses import replace
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from sfmarc_workbench.plate_material import Material, MaterialState, plane_stress_update
from reference_data import reference_path


@pytest.fixture(scope="module")
def material():
    return Material.from_xmt(reference_path('SFMARC_TEST_MATERIAL').read_bytes())


def test_real_xmt_units_history_and_extrapolation(material):
    assert material.density == 8880
    assert material.poisson == 0.33
    assert material.conductivity(293.15) == 253
    assert material.capacity(293.15) == 385
    assert material.elastic_modulus(293.15) == 130e9
    assert material.strain_rate == 0.01
    np.testing.assert_allclose(
        material.yield_stress(293.15, [0, 0, 0, 0.06, 0.3],
                              np.array([20, 700, 575, 20, 20]) + 273.15),
        np.array([550, 150, 350, 600, 680]) * 1e6,
    )
    np.testing.assert_allclose(material.thermal_strain(573.15, 293.15), 17.6e-6 * 280, rtol=1e-7)


def test_enthalpy_latent_heat_and_exact_inverse(material):
    temperature = np.linspace(293.15, 2273.15, 10001)
    np.testing.assert_allclose(material.temperature(material.enthalpy(temperature)), temperature, atol=2e-9)
    assert material.enthalpy(293.15) == 0
    assert material.capacity(1333.15) == pytest.approx(513 + 205000 / 44)
    assert material.enthalpy(1355.15) - material.enthalpy(1311.15) == pytest.approx(513 * 44 + 205000)
    h = 1e-3
    derivative = (material.enthalpy(1333.15 + h) - material.enthalpy(1333.15 - h)) / (2 * h)
    assert derivative == pytest.approx(material.capacity(1333.15), rel=1e-8)


@pytest.mark.parametrize("value", [293.14, 2273.16, np.nan, np.inf])
def test_temperature_validation(material, value):
    for function in (material.conductivity, material.capacity, material.enthalpy, material.elastic_modulus, material.thermal_strain):
        with pytest.raises(ValueError):
            function(value)
    with pytest.raises(ValueError):
        material.yield_stress(293.15, 0, value)


def test_thermal_extension_preserves_energy_and_mechanical_limits(material):
    extended = replace(material, thermal_extrapolation=True)
    boundary = material.temperature_max
    temperatures = np.array([boundary - 1., boundary, boundary + 100., boundary + 1000.])
    capacity = material.capacity(boundary)
    expected = material.enthalpy(boundary) + capacity * np.array([100., 1000.])
    np.testing.assert_allclose(extended.enthalpy(temperatures)[2:], expected, rtol=1e-14)
    np.testing.assert_allclose(extended.temperature(extended.enthalpy(temperatures)), temperatures, atol=1e-9)
    np.testing.assert_allclose(extended.capacity(temperatures[2:]), capacity)
    np.testing.assert_allclose(extended.conductivity(temperatures[2:]), material.conductivity(boundary))
    for operation in (extended.elastic_modulus, extended.thermal_strain):
        with pytest.raises(ValueError):
            operation(boundary + 1.)
    with pytest.raises(ValueError):
        extended.yield_stress(boundary + 1., 0., boundary + 1.)
    for invalid in (np.nan, np.inf, material.temperature_min - 1.):
        with pytest.raises(ValueError):
            extended.enthalpy(invalid)
    for invalid in (np.nan, np.inf, -1.):
        with pytest.raises(ValueError):
            extended.temperature(invalid)
    np.testing.assert_array_equal(extended.enthalpy(temperatures[:2]), material.enthalpy(temperatures[:2]))


def test_material_rejects_incomplete_or_unsupported_xmt():
    source = reference_path('SFMARC_TEST_MATERIAL').read_bytes()
    root = ET.fromstring(source)
    root.find("youngs_modulus_table/sfDataTable/columndata[@usage='2']").set("unit_symbol", "unsupported")
    with pytest.raises(ValueError):
        Material.from_xmt(ET.tostring(root))
    root = ET.fromstring(source)
    flow = root.find("flow_curves_table_approach")
    flow.remove(flow.find("flow_curve_table"))
    with pytest.raises(ValueError):
        Material.from_xmt(ET.tostring(root))


def test_free_expansion_and_elastic_plane_stress(material):
    temperature = np.array([298.15, 573.15, 1073.15, 1355.15])
    thermal = material.thermal_strain(temperature)
    strain = np.stack((thermal, thermal, np.zeros_like(thermal)), axis=-1)
    initial = MaterialState.zeros((4,))
    stress, tangent, state, ezz = plane_stress_update(material, strain, temperature, initial)
    np.testing.assert_allclose(stress, 0, atol=1e-5)
    np.testing.assert_allclose(ezz, thermal, atol=1e-13)
    np.testing.assert_array_equal(state.equivalent, 0)
    np.testing.assert_array_equal(initial.peak_temperature, 298.15)
    stress, tangent, _, ezz = plane_stress_update(material, [1e-4, 0, 2e-4], 298.15, MaterialState.zeros())
    young = material.elastic_modulus(298.15)
    expected = young / (1 - material.poisson ** 2) * np.array([
        [1, material.poisson, 0], [material.poisson, 1, 0], [0, 0, (1 - material.poisson) / 2],
    ])
    np.testing.assert_allclose(stress, expected @ np.array([1e-4, 0, 2e-4]), rtol=1e-12)
    np.testing.assert_allclose(tangent, expected, rtol=1e-12, atol=1e-5)
    assert ezz == pytest.approx(-material.poisson / (1 - material.poisson) * 1e-4)


def assert_return_consistent(material, strain, temperature, initial):
    stress, tangent, state, ezz = plane_stress_update(material, strain, temperature, initial)
    thermal = material.thermal_strain(temperature)
    full_strain = np.array([strain[0], strain[1], ezz, 0, 0, strain[2] / np.sqrt(2)])
    elastic = full_strain - state.plastic - thermal * np.array([1, 1, 1, 0, 0, 0])
    young = material.elastic_modulus(temperature)
    shear = young / (2 * (1 + material.poisson))
    bulk = young / (3 * (1 - 2 * material.poisson))
    full_stress = 2 * shear * elastic
    full_stress[:3] += (bulk - 2 * shear / 3) * np.sum(elastic[:3])
    assert abs(full_stress[2]) < 0.02
    deviator = full_stress.copy()
    deviator[:3] -= full_stress[:3].mean()
    q = np.sqrt(1.5 * np.dot(deviator, deviator))
    if state.equivalent > initial.equivalent + 1e-10:
        assert q == pytest.approx(material.yield_stress(temperature, state.equivalent, state.peak_temperature), rel=2e-9)
    numerical = np.empty((3, 3))
    for axis in range(3):
        increment = np.eye(3)[axis] * 1e-7
        plus = plane_stress_update(material, strain + increment, temperature, initial)[0]
        minus = plane_stress_update(material, strain - increment, temperature, initial)[0]
        numerical[:, axis] = (plus - minus) / 2e-7
    np.testing.assert_allclose(tangent, numerical, rtol=2e-5, atol=1e3)
    np.testing.assert_allclose(tangent, tangent.T, rtol=1e-12, atol=1e-4)
    return stress, state


@pytest.mark.parametrize("strain", [[0.02, -0.005, 0], [0, 0, 0.05], [0.4, -0.1, 0.2]])
def test_plastic_plane_stress_and_consistent_tangent(material, strain):
    initial = MaterialState.zeros()
    stress, state = assert_return_consistent(material, np.array(strain), 298.15, initial)
    assert state.equivalent > 0
    assert np.max(np.abs(state.plastic)) > 0
    np.testing.assert_array_equal(initial.plastic, 0)
    assert initial.equivalent == 0
    if strain[0] > 0.3:
        assert state.extrapolated


def test_uniaxial_and_shear_analytical_return(material):
    temperature = 298.15
    plastic = 0.03
    young = material.elastic_modulus(temperature)
    shear = young / (2 * (1 + material.poisson))
    yielding = material.yield_stress(temperature, plastic, temperature)
    uniaxial = np.array([plastic + yielding / young, -plastic / 2 - material.poisson * yielding / young, 0])
    stress, state = assert_return_consistent(material, uniaxial, temperature, MaterialState.zeros())
    np.testing.assert_allclose(stress, [yielding, 0, 0], atol=0.02)
    assert state.equivalent == pytest.approx(plastic, rel=1e-10)
    np.testing.assert_allclose(state.plastic[:3], [plastic, -plastic / 2, -plastic / 2], atol=1e-11)
    shear_strain = np.array([0, 0, yielding / (np.sqrt(3) * shear) + np.sqrt(3) * plastic])
    stress, state = assert_return_consistent(material, shear_strain, temperature, MaterialState.zeros())
    np.testing.assert_allclose(stress, [0, 0, yielding / np.sqrt(3)], atol=0.02)
    assert state.equivalent == pytest.approx(plastic, rel=1e-10)


@pytest.mark.parametrize("temperature", [293.15, 873.15, 1311.15, 1500., 2273.15])
def test_high_temperature_plastic_tangent(material, temperature):
    assert_return_consistent(material, np.array([0.025, -0.012, 0.033]), temperature, MaterialState.zeros())


def test_repeated_constrained_thermal_cycles_accumulate_and_reverse(material):
    state = MaterialState.zeros()
    equivalent = []
    stresses = []
    for _ in range(4):
        for temperature in np.concatenate((np.linspace(298.15, 1073.15, 40), np.linspace(1073.15, 298.15, 40))):
            stress, _, state, _ = plane_stress_update(material, np.zeros(3), temperature, state)
            equivalent.append(float(state.equivalent))
            stresses.append(stress[0])
    assert np.min(stresses) < -10e6
    assert stresses[-1] > 10e6
    assert state.equivalent > 10 * material.yield_stress(298.15, 0, 298.15) / material.elastic_modulus(298.15)
    assert equivalent[-1] > equivalent[79] * 2
    assert np.all(np.diff(equivalent) >= 0)
    assert state.peak_temperature == pytest.approx(1073.15)
    assert_return_consistent(material, np.array([0.003, -0.002, 0.015]), 298.15, state)


def test_batch_independent_history_matches_scalar(material):
    strains = np.array([[0.02, 0, 0.01], [0, 0, 0.05], [0.001, 0, 0]])
    temperatures = np.array([298.15, 873.15, 573.15])
    stress, tangent, state, ezz = plane_stress_update(material, strains, temperatures, MaterialState.zeros((3,)))
    for index in range(3):
        single = plane_stress_update(material, strains[index], temperatures[index], MaterialState.zeros())
        np.testing.assert_allclose(stress[index], single[0], atol=1e-3)
        np.testing.assert_allclose(tangent[index], single[1], atol=1e-2)
        np.testing.assert_allclose(state.plastic[index], single[2].plastic, atol=1e-12)
        np.testing.assert_allclose(ezz[index], single[3], atol=1e-12)
    assert len(np.unique(state.equivalent)) == 3


def test_transverse_plastic_history_preserves_plane_stress(material):
    initial = MaterialState(np.array([0., 0., 0., 0.01, -0.02, 0.]), np.array(0.1), np.array(298.15))
    stress, _, state, _ = plane_stress_update(material, np.zeros(3), 298.15, initial)
    np.testing.assert_allclose(stress, 0, atol=1e-5)
    np.testing.assert_array_equal(state.plastic, initial.plastic)
    assert state.equivalent == initial.equivalent


def test_invalid_strain_state_and_enthalpy_fail(material):
    with pytest.raises(ValueError):
        plane_stress_update(material, [np.nan, 0, 0], 298.15, MaterialState.zeros())
    with pytest.raises(ValueError):
        plane_stress_update(material, [0, 0, 0], 298.15, MaterialState(np.zeros(6), -0.1, 298.15))
    with pytest.raises(ValueError):
        material.temperature(-1)
    with pytest.raises(ValueError):
        material.yield_stress(298.15, -0.1, 298.15)
