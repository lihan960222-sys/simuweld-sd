import numpy as np
import pytest
from pathlib import Path
from time import perf_counter
from scipy.sparse.linalg import spsolve

from sfmarc_workbench.plate_material import Material, MaterialState, plane_stress_update
from sfmarc_workbench.plate_shell import ElasticSection, MITC4Shell
from reference_data import reference_path


def rectangle(thickness=0.01, young=210e9, poisson=0.3):
    xy = np.array([[-1., -0.5], [1., -0.5], [1., 0.5], [-1., 0.5]])
    return MITC4Shell(xy, [[0, 1, 2, 3]], thickness, young, poisson)


def plane_stress_matrix(young, poisson):
    return young / (1 - poisson ** 2) * np.array([
        [1., poisson, 0.], [poisson, 1., 0.], [0., 0., (1 - poisson) / 2],
    ])


def test_rigid_modes_and_von_karman_rigid_tilt():
    model = rectangle()
    q = np.zeros((4, 5))
    q[:, :3] = [0.1, -0.2, 0.3]
    response = model.evaluate(q)
    assert response.energy < 1e-20
    np.testing.assert_allclose(response.residual, 0, atol=1e-6)
    eigenvalues = np.linalg.eigvalsh(response.tangent.toarray())
    assert np.count_nonzero(np.abs(eigenvalues) < eigenvalues[-1] * 1e-10) == 6
    x, y = model.nodal_xy.T
    a, b, rotation = 0.13, -0.08, 0.07
    q[:, 0] = -0.5 * a ** 2 * x - 0.5 * a * b * y - rotation * y
    q[:, 1] = -0.5 * b ** 2 * y - 0.5 * a * b * x + rotation * x
    q[:, 2] = a * x + b * y
    q[:, 3] = b
    q[:, 4] = -a
    response = model.evaluate(q)
    assert response.energy < 1e-20
    np.testing.assert_allclose(response.residual, 0, atol=1e-6)


def test_constant_membrane_strain_and_engineering_shear_on_distorted_quad():
    xy = np.array([[0., 0.], [2., 0.1], [1.7, 1.1], [-0.2, 0.8]])
    model = MITC4Shell(xy, [[0, 1, 2, 3]], 0.02, 70e9, 0.28)
    q = np.zeros((4, 5))
    q[:, 0] = 0.001 * xy[:, 0] + 0.0004 * xy[:, 1]
    q[:, 1] = -0.0002 * xy[:, 1] + 0.0003 * xy[:, 0]
    strain = np.array([0.001, -0.0002, 0.0007])
    kin = model.kinematics(q)
    np.testing.assert_allclose(kin.strain, np.broadcast_to(strain, (1, 4, 5, 3)), rtol=1e-13)
    area = 0.5 * abs(np.dot(xy[:, 0], np.roll(xy[:, 1], -1)) - np.dot(xy[:, 1], np.roll(xy[:, 0], -1)))
    expected = 0.5 * area * 0.02 * strain @ plane_stress_matrix(70e9, 0.28) @ strain
    assert model.evaluate(q).energy == pytest.approx(expected, rel=1e-13)
    unloaded = model.evaluate(q, strain[None, :])
    assert unloaded.energy < 1e-20
    np.testing.assert_allclose(unloaded.residual, 0, atol=2e-8)


@pytest.mark.parametrize("thickness", [0.1, 0.001, 0.000001])
def test_pure_bending_energy_and_thickness_sign(thickness):
    model = rectangle(thickness)
    q = np.zeros((4, 5))
    curvature = 0.012
    q[:, 4] = curvature * model.nodal_xy[:, 0]
    kin = model.kinematics(q)
    expected = curvature * model.thickness_coordinates[0]
    np.testing.assert_allclose(kin.strain[0, :, :, 0], np.broadcast_to(expected, (4, 5)), rtol=1e-13)
    assert np.all(kin.strain[0, :, 0, 0] < 0)
    assert np.all(kin.strain[0, :, -1, 0] > 0)
    np.testing.assert_allclose(kin.shear, 0, atol=1e-16)
    bending = 210e9 * thickness ** 3 / (12 * (1 - 0.3 ** 2))
    assert model.evaluate(q).energy == pytest.approx(0.5 * 2 * bending * curvature ** 2, rel=1e-12)
    stress_free = model.evaluate(q, kin.strain)
    assert stress_free.energy < 1e-20
    np.testing.assert_allclose(stress_free.residual, 0, atol=1e-10)


def test_twist_has_bending_energy_without_shear_locking():
    model = rectangle(1e-5)
    q = np.zeros((4, 5))
    x, y = model.nodal_xy.T
    curvature = 0.01
    q[:, 2] = -curvature * x * y
    q[:, 3] = -curvature * x
    q[:, 4] = curvature * y
    np.testing.assert_allclose(model.kinematics(q).shear, 0, atol=1e-16)
    linear = model.evaluate(np.zeros_like(q)).tangent
    energy = 0.5 * q.ravel() @ linear @ q.ravel()
    expected = 0.5 * 2 * (210e9 / (2 * 1.3)) * (1e-5 ** 3 / 12) * (2 * curvature) ** 2
    assert energy == pytest.approx(expected, rel=2e-5)


def test_five_thickness_points_integrate_eigenstrain_energy():
    model = rectangle(0.04)
    z = model.thickness_coordinates[0]
    eigenstrain = np.zeros((1, 5, 3))
    eigenstrain[0, :, 0] = 3 * z ** 4
    response = model.evaluate(np.zeros((4, 5)), eigenstrain)
    expected = 0.5 * 2 * plane_stress_matrix(210e9, 0.3)[0, 0] * 9 * 0.04 ** 9 / (9 * 2 ** 8)
    assert response.energy == pytest.approx(expected, rel=1e-13)
    flattened = np.broadcast_to(eigenstrain[:, None], (1, 4, 5, 3)).reshape(1, 20, 3)
    assert model.evaluate(np.zeros((4, 5)), flattened).energy == pytest.approx(expected, rel=1e-13)


def test_constant_covariant_shear_on_distorted_quad():
    xy = np.array([[0., 0.], [2., 0.1], [1.7, 1.1], [-0.2, 0.8]])
    model = MITC4Shell(xy, [[0, 1, 2, 3]], 0.02, 70e9, 0.28)
    q = np.zeros((4, 5))
    q[:, 2] = 0.03 * xy[:, 0] - 0.02 * xy[:, 1]
    q[:, 3] = -0.007
    q[:, 4] = 0.009
    expected = np.broadcast_to([0.039, -0.013], (1, 4, 2))
    np.testing.assert_allclose(model.kinematics(q).shear, expected, rtol=1e-13)


def test_consistent_large_deflection_gradient_hessian_and_material_interface():
    xy = np.array([[0., 0.], [1., 0.1], [2., 0.], [0.1, 1.], [1.1, 1.2], [2.1, 0.9]])
    model = MITC4Shell(xy, [[0, 1, 4, 3], [1, 2, 5, 4]], [0.03, 0.04], [70e9, 90e9], 0.29)
    rng = np.random.default_rng(21)
    q = rng.normal(size=(6, 5)) * [0.001, 0.001, 0.03, 0.04, 0.04]
    direction = rng.normal(size=q.shape)
    eigenstrain = rng.normal(size=(2, 4, 5, 3)) * 0.0007
    response = model.evaluate(q, eigenstrain)
    h = 2e-7
    plus = model.evaluate(q + h * direction, eigenstrain, tangent=False)
    minus = model.evaluate(q - h * direction, eigenstrain, tangent=False)
    derivative = (plus.energy - minus.energy) / (2 * h)
    assert derivative == pytest.approx(response.residual @ direction.ravel(), rel=1e-8)
    numerical = (plus.residual - minus.residual) / (2 * h)
    np.testing.assert_allclose(response.tangent @ direction.ravel(), numerical, rtol=1e-8, atol=0.1)
    np.testing.assert_allclose(response.tangent.toarray(), response.tangent.toarray().T, rtol=1e-13, atol=1e-6)
    kin = model.kinematics(q)
    c = np.broadcast_to(model.elasticity[:, None, None], (2, 4, 5, 3, 3))
    stress = np.einsum("eptij,eptj->epti", c, kin.strain - eigenstrain)
    assembly = model.assemble(kin, stress, c)
    np.testing.assert_allclose(assembly.residual, response.residual, rtol=1e-13)
    np.testing.assert_allclose(assembly.tangent.toarray(), response.tangent.toarray(), rtol=1e-13, atol=1e-7)
    expected = np.einsum("eptij,ej->epti", kin.strain_B, direction[model.connectivity].reshape(2, 20))
    actual = (model.kinematics(q + h * direction).strain - model.kinematics(q - h * direction).strain) / (2 * h)
    np.testing.assert_allclose(expected, actual, rtol=2e-9, atol=1e-10)


def test_cantilever_bending_compliance_remains_accurate_for_thin_plate():
    count = 10
    xy = np.array([[x, y] for x in np.linspace(0, 1, count + 1) for y in (-0.05, 0.05)])
    connectivity = [[2 * i, 2 * i + 2, 2 * i + 3, 2 * i + 1] for i in range(count)]
    for thickness in (0.01, 0.00001):
        model = MITC4Shell(xy, connectivity, thickness, 70e9, 0.)
        matrix = model.evaluate(np.zeros((len(xy), 5))).tangent
        free = np.arange(10, 5 * len(xy))
        load = np.zeros(5 * len(xy))
        load[-8] = load[-3] = 0.5
        solution = np.zeros_like(load)
        solution[free] = spsolve(matrix[free][:, free], load[free])
        bending = 70e9 * 0.1 * thickness ** 3 / 12
        shear = (5 / 6) * 35e9 * 0.1 * thickness
        expected = 1 / (3 * bending) + 1 / shear
        assert solution[-3] == pytest.approx(expected, rel=0.004)


@pytest.mark.parametrize("xy,connectivity,thickness,young,poisson", [
    ([[0, 0], [1, 0], [1, 1], [0, 1]], [[0, 3, 2, 1]], 0.01, 1e9, 0.3),
    ([[0, 0], [1, 0], [1, 1], [0, 1]], [[0, 1, 1, 3]], 0.01, 1e9, 0.3),
    ([[0, 0], [1, 0], [1, 1], [0, 1]], [[0, 1, 2, 4]], 0.01, 1e9, 0.3),
    ([[0, 0], [1, 0], [1, 1], [0, 1]], [[0, 1, 2, 3]], -0.01, 1e9, 0.3),
    ([[0, 0], [1, 0], [1, 1], [0, 1]], [[0, 1, 2, 3]], 0.01, 0., 0.3),
    ([[0, 0], [1, 0], [1, 1], [0, 1]], [[0, 1, 2, 3]], 0.01, 1e9, 0.5),
])
def test_invalid_geometry_or_material_fails(xy, connectivity, thickness, young, poisson):
    with pytest.raises(ValueError):
        MITC4Shell(xy, connectivity, thickness, young, poisson)


def test_invalid_state_fails():
    model = rectangle()
    with pytest.raises(ValueError):
        model.kinematics(np.zeros((4, 6)))
    with pytest.raises(ValueError):
        model.kinematics(np.full((4, 5), np.nan))
    with pytest.raises(ValueError):
        model.evaluate(np.zeros((4, 5)), np.zeros((1, 3, 3)))
    with pytest.raises(ValueError):
        model.energy(np.full((4, 5), np.nan))
    with pytest.raises(ValueError):
        model.energy(np.zeros((4, 5)), np.full((1, 3), np.nan))


@pytest.mark.parametrize("eigenstrain_shape", [None, (2, 3), (2, 5, 3), (2, 20, 3), (2, 4, 5, 3)])
def test_section_integration_matches_full_quadrature_after_material_update(eigenstrain_shape):
    xy = np.array([[0., 0.], [1., 0.1], [2., 0.], [0.1, 1.], [1.1, 1.2], [2.1, 0.9]])
    model = MITC4Shell(xy, [[0, 1, 4, 3], [1, 2, 5, 4]], [0.03, 0.04], [70e9, 90e9], 0.29,
                       area_fraction=[0.23, 0.71], section_offset=[-0.012, 0.007])
    rng = np.random.default_rng(203)
    q = rng.normal(size=(6, 5)) * [0.001, 0.001, 0.03, 0.04, 0.04]
    eigenstrain = None if eigenstrain_shape is None else rng.normal(size=eigenstrain_shape) * 0.0007
    for ratio in (np.ones(2), np.array([0.7, 0.4])):
        model.elasticity *= ratio[:, None, None]
        model.shear_modulus *= ratio
        kin = model.kinematics(q)
        mechanical = kin.strain.copy()
        if eigenstrain_shape == (2, 3):
            mechanical -= eigenstrain[:, None, None]
        elif eigenstrain_shape == (2, 5, 3):
            mechanical -= eigenstrain[:, None]
        elif eigenstrain is not None:
            mechanical -= eigenstrain.reshape(2, 4, 5, 3)
        c = np.broadcast_to(model.elasticity[:, None, None], (2, 4, 5, 3, 3))
        stress = np.einsum("eptij,eptj->epti", c, mechanical)
        reference = model.assemble(kin, stress, c)
        energy = 0.5 * np.einsum("epti,epti,ept->", mechanical, stress, model.volume_weights)
        energy += (5 / 12) * np.einsum("epi,epi,ept,e->", kin.shear, kin.shear,
                                     model.volume_weights, model.shear_modulus)
        for tangent in (False, True):
            response = model.evaluate(q, eigenstrain, tangent=tangent)
            assert response.energy == pytest.approx(energy, rel=2e-14)
            np.testing.assert_allclose(response.residual, reference.residual, rtol=2e-13, atol=1e-8)
            if tangent:
                np.testing.assert_allclose(response.tangent.toarray(), reference.tangent.toarray(), rtol=2e-13, atol=1e-7)
            else:
                assert response.tangent is None
        assert model.energy(q, eigenstrain) == pytest.approx(energy, rel=2e-14)


def test_external_section_matches_heterogeneous_columns_and_consistent_hessian():
    base = rectangle()
    area_fraction = 0.37
    model = MITC4Shell(base.nodal_xy, base.connectivity, 0.01, 210e9, 0.3, area_fraction=area_fraction)
    rng = np.random.default_rng(617)
    q = rng.normal(size=(4, 5)) * [0.001, 0.001, 0.03, 0.04, 0.04]
    stiffness = np.zeros((1, 3, 3, 3))
    eigen_resultants = np.zeros((1, 2, 3))
    eigen_energy = np.zeros(1)
    shear_integral = np.zeros(1)
    reference_energy = 0.
    reference_residual = np.zeros(20)
    reference_tangent = np.zeros((20, 20))
    for fraction, thickness, offset, young, poisson in ((0.35, 0.003, 0.002, 70e9, 0.28),
                                                        (0.65, 0.008, -0.004, 120e9, 0.33)):
        column = MITC4Shell(base.nodal_xy, base.connectivity, thickness, young, poisson,
                           area_fraction=area_fraction * fraction, section_offset=offset)
        z = column.thickness_coordinates[0]
        dz = column.volume_weights[0, 0] / column.area_weights[0, 0]
        c = column.elasticity[0] * np.linspace(0.4, 1.2, 5)[:, None, None]
        g = column.shear_modulus[0] * np.linspace(0.7, 1.1, 5)
        eigenstrain = rng.normal(size=(5, 3)) * 0.0005
        eigenstress = np.einsum("tij,tj->ti", c, eigenstrain)
        for power in range(3):
            stiffness[0, power] += fraction * np.einsum("t,t,tij->ij", dz, z ** power, c)
        for power in range(2):
            eigen_resultants[0, power] += fraction * np.einsum("t,t,ti->i", dz, z ** power, eigenstress)
        eigen_energy[0] += 0.5 * fraction * np.einsum("t,ti,ti->", dz, eigenstrain, eigenstress)
        shear_integral[0] += fraction * dz @ g
        kin = column.kinematics(q)
        mechanical = kin.strain - eigenstrain[None, None]
        stress = np.einsum("tij,eptj->epti", c, mechanical)
        response = column.assemble(kin, stress, np.broadcast_to(c, (1, 4, 5, 3, 3)),
                                   shear_modulus=g[None, None])
        reference_residual += response.residual
        reference_tangent += response.tangent.toarray()
        reference_energy += 0.5 * np.einsum("epti,epti,ept->", mechanical, stress, column.volume_weights)
        reference_energy += (5 / 12) * np.einsum("epi,epi,ept,t->", kin.shear, kin.shear, column.volume_weights, g)
    section = ElasticSection(stiffness, eigen_resultants, eigen_energy, shear_integral)
    response = model.evaluate(q, section=section)
    assert response.energy == pytest.approx(reference_energy, rel=2e-14)
    assert model.energy(q, section=section) == pytest.approx(reference_energy, rel=2e-14)
    np.testing.assert_allclose(response.residual, reference_residual, rtol=2e-13, atol=1e-8)
    np.testing.assert_allclose(response.tangent.toarray(), reference_tangent, rtol=2e-13, atol=1e-7)
    direction = rng.normal(size=q.shape)
    h = 2e-7
    plus = model.evaluate(q + h * direction, section=section, tangent=False)
    minus = model.evaluate(q - h * direction, section=section, tangent=False)
    assert (plus.energy - minus.energy) / (2 * h) == pytest.approx(response.residual @ direction.ravel(), rel=1e-8)
    np.testing.assert_allclose(response.tangent @ direction.ravel(), (plus.residual - minus.residual) / (2 * h),
                               rtol=1e-8, atol=0.1)
    with pytest.raises(ValueError):
        model.energy(q, np.zeros((1, 3)), section=section)


@pytest.mark.parametrize("field,value", [
    ("stiffness", np.zeros((1, 3, 3))), ("eigen_resultants", np.zeros((2, 2, 3))),
    ("eigen_energy", [-1.]), ("shear_integral", [0.]), ("shear_integral", [np.nan]),
])
def test_invalid_external_sections_fail(field, value):
    fields = dict(stiffness=np.ones((1, 3, 3, 3)), eigen_resultants=np.zeros((1, 2, 3)),
                  eigen_energy=np.zeros(1), shear_integral=np.ones(1))
    fields[field] = value
    with pytest.raises(ValueError):
        ElasticSection(**fields)


def test_fraction_scales_all_energy_residual_and_tangent_terms():
    base = rectangle()
    fraction = 0.37
    model = MITC4Shell(base.nodal_xy, base.connectivity, 0.01, 210e9, 0.3, area_fraction=fraction)
    q = np.random.default_rng(72).normal(size=(4, 5)) * 0.002
    full, partial = base.evaluate(q), model.evaluate(q)
    assert partial.energy == pytest.approx(fraction * full.energy, rel=1e-13)
    np.testing.assert_allclose(partial.residual, fraction * full.residual, rtol=1e-13)
    np.testing.assert_allclose(partial.tangent.toarray(), fraction * full.tangent.toarray(), rtol=1e-13, atol=1e-7)


def test_section_offset_includes_membrane_bending_coupling():
    base = rectangle()
    thickness, offset, curvature = 0.01, 0.008, 0.012
    model = MITC4Shell(base.nodal_xy, base.connectivity, thickness, 210e9, 0.3, section_offset=offset)
    q = np.zeros((4, 5))
    q[:, 4] = curvature * base.nodal_xy[:, 0]
    expected_z = base.thickness_coordinates + offset
    np.testing.assert_allclose(model.thickness_coordinates, expected_z, rtol=1e-13)
    strain = model.kinematics(q).strain
    np.testing.assert_allclose(strain[0, :, :, 0], np.broadcast_to(curvature * expected_z, (4, 5)), rtol=1e-13)
    expected = 0.5 * 2 * plane_stress_matrix(210e9, 0.3)[0, 0] * curvature ** 2 * (thickness ** 3 / 12 + thickness * offset ** 2)
    response = model.evaluate(q)
    assert response.energy == pytest.approx(expected, rel=1e-13)
    assert np.linalg.norm(response.residual.reshape(4, 5)[:, 0]) > 1e4


def test_variable_shear_modulus_integrates_at_all_thickness_points():
    model = rectangle()
    q = np.zeros((4, 5))
    q[:, 4] = 0.02
    kin = model.kinematics(q)
    z = model.thickness_coordinates[:, None, :]
    shear = model.shear_modulus[:, None, None] * (1 + z ** 2 / model.thickness[:, None, None] ** 2)
    c = np.broadcast_to(model.elasticity[:, None, None], (1, 4, 5, 3, 3))
    zero = np.zeros_like(kin.strain)
    base = model.assemble(kin, zero, c)
    altered = model.assemble(kin, zero, c, shear_modulus=shear)
    np.testing.assert_allclose(altered.residual, (1 + 1 / 12) * base.residual, rtol=1e-13)
    h = 1e-7
    direction = np.random.default_rng(5).normal(size=(4, 5))
    residuals = []
    for displaced in (q + h * direction, q - h * direction):
        current = model.kinematics(displaced)
        stress = np.einsum("eptij,eptj->epti", c, current.strain)
        residuals.append(model.assemble(current, stress, tangent=False, shear_modulus=shear).residual)
    numerical = (residuals[0] - residuals[1]) / (2 * h)
    np.testing.assert_allclose(altered.tangent @ direction.ravel(), numerical, rtol=1e-8, atol=0.1)


@pytest.mark.parametrize("options", [{"area_fraction": 0.}, {"area_fraction": 1.1}, {"section_offset": np.nan}])
def test_invalid_section_values_fail(options):
    base = rectangle()
    with pytest.raises(ValueError):
        MITC4Shell(base.nodal_xy, base.connectivity, 0.01, 210e9, 0.3, **options)


def test_real_j2_material_response_has_consistent_global_tangent():
    path = reference_path('SFMARC_TEST_MATERIAL')
    material = Material.from_xmt(path.read_bytes())
    model = rectangle(0.01, material.elastic_modulus(573.15), material.poisson)
    q = np.zeros((4, 5))
    q[:, 0] = 0.012 * model.nodal_xy[:, 0]
    q[:, 1] = -0.001 * model.nodal_xy[:, 1]
    q[:, 2] = 0.01 * model.nodal_xy[:, 0] * model.nodal_xy[:, 1]
    q[:, 4] = 0.4 * model.nodal_xy[:, 0]
    temperature = np.full((1, 4, 5), 573.15)
    history = MaterialState.zeros((1, 4, 5))
    kin = model.kinematics(q)
    stress, tangent, state, _ = plane_stress_update(material, kin.strain, temperature, history)
    assert np.all(state.equivalent > 0)
    response = model.assemble(kin, stress, tangent)
    assert response.energy is None
    direction = np.random.default_rng(415).normal(size=(4, 5))
    h = 1e-7
    residuals = []
    for displaced in (q + h * direction, q - h * direction):
        current = model.kinematics(displaced)
        current_stress, _, _, _ = plane_stress_update(material, current.strain, temperature, history)
        residuals.append(model.assemble(current, current_stress, tangent=False).residual)
    numerical = (residuals[0] - residuals[1]) / (2 * h)
    np.testing.assert_allclose(response.tangent @ direction.ravel(), numerical, rtol=2e-6, atol=0.05)
    np.testing.assert_array_equal(history.equivalent, 0)


def test_four_thousand_elements_sparse_assembly():
    nx, ny = 80, 50
    x, y = np.meshgrid(np.linspace(0, 0.08, nx + 1), np.linspace(0, 0.05, ny + 1))
    xy = np.column_stack((x.ravel(), y.ravel()))
    indices = np.arange((nx + 1) * (ny + 1)).reshape(ny + 1, nx + 1)
    connectivity = np.column_stack((indices[:-1, :-1].ravel(), indices[:-1, 1:].ravel(),
                                    indices[1:, 1:].ravel(), indices[1:, :-1].ravel()))
    start = perf_counter()
    model = MITC4Shell(xy, connectivity, 0.0002, 130e9, 0.33)
    setup_seconds = perf_counter() - start
    q = np.zeros((len(xy), 5))
    q[:, 0] = 0.001 * xy[:, 0]
    q[:, 2] = 0.0001 * np.sin(np.pi * xy[:, 0] / 0.08) * np.sin(np.pi * xy[:, 1] / 0.05)
    timings = {name: [] for name in ("full_quadrature", "section_tangent", "residual", "energy", "external_section")}
    z = model.thickness_coordinates
    dz = model.volume_weights[:, 0] / model.area_weights[:, 0, None]
    moments = np.einsum("et,etp->ep", dz, np.array([np.ones_like(z), z, z ** 2]).transpose(1, 2, 0))
    section = ElasticSection(moments[:, :, None, None] * model.elasticity[:, None],
                             np.zeros((len(connectivity), 2, 3)), np.zeros(len(connectivity)),
                             model.thickness * model.shear_modulus)
    for _ in range(3):
        start = perf_counter()
        kin = model.kinematics(q)
        stress = np.einsum("eij,eptj->epti", model.elasticity, kin.strain)
        reference = model.assemble(kin, stress, np.broadcast_to(model.elasticity[:, None, None], (nx * ny, 4, 5, 3, 3)))
        timings["full_quadrature"].append(perf_counter() - start)
        start = perf_counter()
        response = model.evaluate(q)
        timings["section_tangent"].append(perf_counter() - start)
        start = perf_counter()
        without_tangent = model.evaluate(q, tangent=False)
        timings["residual"].append(perf_counter() - start)
        start = perf_counter()
        energy = model.energy(q)
        timings["energy"].append(perf_counter() - start)
        start = perf_counter()
        external = model.evaluate(q, section=section)
        timings["external_section"].append(perf_counter() - start)
    assert response.tangent.shape == (20655, 20655)
    assert response.tangent.nnz < 1000000
    assert np.isfinite(response.tangent.data).all()
    assert np.isfinite(response.residual).all()
    assert response.energy > 0
    assert energy == without_tangent.energy == response.energy
    assert external.energy == pytest.approx(energy, rel=1e-13)
    np.testing.assert_allclose(response.residual, reference.residual, rtol=1e-12, atol=1e-9)
    np.testing.assert_allclose(response.tangent.data, reference.tangent.data, rtol=1e-12, atol=1e-8)
    np.testing.assert_allclose(external.residual, reference.residual, rtol=1e-12, atol=1e-9)
    np.testing.assert_allclose(external.tangent.data, reference.tangent.data, rtol=1e-12, atol=1e-8)
    measured = ", ".join(f"{name}={min(values):.4f}s" for name, values in timings.items())
    print(f"4000 elements: setup={setup_seconds:.3f}s, {measured}, nnz={response.tangent.nnz}")
