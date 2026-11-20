from dataclasses import replace
from pathlib import Path
from time import perf_counter

import numpy as np
import pytest
from scipy.integrate import quad
from scipy.optimize import brentq
from scipy.special import erf
from scipy.spatial.transform import Rotation
from shapely.geometry import box

from sfmarc_workbench.deck import Deck
from sfmarc_workbench.geometry import SurfaceModel
from sfmarc_workbench.plate_material import Material
from sfmarc_workbench.plate_geometry import PlateGrid
from sfmarc_workbench.plate_thermal import ThermalSolver, _boundary_integral, _circle, _fraction
from sfmarc_workbench.thermal_deck import read_weld_paths
from reference_data import reference_path


@pytest.fixture(scope='module')
def material():
    return Material.from_xmt(reference_path('SFMARC_TEST_MATERIAL').read_bytes())


def grid(spacing=.5, two=False, gap=.00003):
    regions = [('lower', box(0, 0, .004, .002), 0., .000421)]
    if two:
        regions.append(('upper', box(0, 0, .004, .002), .000421 + gap, .000501 + gap))
    return PlateGrid.from_regions(regions, spacing)


def path(radius=.08, depth=.1, power=1., kind=7, z=.000421):
    source = dict(id=1, name='source', kind=kind, power_W=power, efficiency=.8,
                  gaussian=1., radius_mm=radius, depth_mm=depth,
                  upper_radius_mm=radius, lower_radius_mm=radius / 2, cutoff_mm=2 * radius,
                  power_table=[[0., 0.], [.005, 1.], [.01, .5]])
    return dict(id=1, points_mm=[[.7, 1., z * 1000], [3.3, 1., z * 1000]],
                normals=[[0., 0., 1.], [0., 0., 1.]], start_s=0., end_s=.01,
                speed_mm_s=260., sources=[source])


def test_adiabatic_enthalpy_and_variable_thickness(material):
    plate = PlateGrid.from_regions([
        ('plate', box(0, 0, .002, .002), 0., .00005),
        ('plate', box(.002, 0, .004, .002), 0., .00008),
    ], .5)
    solver = ThermalSolver(plate, material, {}, [])
    solver.temperature[0, :, :4] = 700.
    initial = solver.total_enthalpy()
    for _ in range(50):
        solver.step(.02)
    print('ADIABATIC', 'relative_enthalpy_error', solver.total_enthalpy() / initial - 1, 'temperature_range_K', np.ptp(solver.temperature))
    assert solver.total_enthalpy() == pytest.approx(initial, rel=2e-10)
    assert np.ptp(solver.temperature) < .05


def test_external_area_on_aligned_boundary_and_hole(material):
    polygon = box(0., 0., .004, .004).difference(box(.001, .001, .003, .003))
    thickness = .000421
    plate = PlateGrid.from_regions([('plate', polygon, 0., thickness)], .5)
    solver = ThermalSolver(plate, material, {}, [])
    assert np.count_nonzero(solver.external_area[~solver.mask]) == 0
    side_area = solver.external_area.sum() - 2 * polygon.area
    assert side_area == pytest.approx(polygon.boundary.length * thickness, rel=1e-12)


def test_contact_uses_active_cells_at_fractional_boundary(material):
    polygon = box(.004468213730834799, -.0397193618846552, .0084682137308348, -.035719361884655194)
    plate = PlateGrid.from_regions([
        ('lower', polygon, 0., .000421),
        ('upper', polygon, .000451, .000531),
    ], .5)
    solver = ThermalSolver(plate, material, {'gap_h': 400.}, [])
    solver.temperature[0][solver.mask[0]] = 300.
    initial = solver.total_enthalpy()
    solver.step(.01)
    assert solver.total_enthalpy() == pytest.approx(initial, rel=2e-10)


def test_extended_temperature_heating_and_conduction_conserve_energy(material):
    extended = replace(material, thermal_extrapolation=True)
    plate = PlateGrid.from_regions([('plate', box(0, 0, .001, .0005), 0., .00005)], .5)
    solver = ThermalSolver(plate, extended, {}, [])
    initial = solver.total_enthalpy()
    energy = np.zeros(solver.shape)
    energy[0, 0, 0] = solver.mass[0, 0, 0] * (extended.enthalpy(3000.) - extended.enthalpy(298.15))
    solver.add_energy(energy)
    assert solver.temperature[0, 0, 0] == pytest.approx(3000.)
    solver.step(.0001)
    assert material.temperature_max < solver.temperature[0, 0, 0] < 3000.
    assert solver.temperature[0, 0, 1] > 298.15
    assert solver.total_enthalpy() - initial == pytest.approx(energy.sum(), abs=1e-11)


def test_two_plate_exchange_matches_exponential(material):
    plate = grid(4., two=True)
    solver = ThermalSolver(plate, material, {'contact_h': 400., 'gap_h': 400.}, [])
    solver.temperature[0] = 299.15
    initial = solver.total_enthalpy()
    difference = []
    for _ in range(100):
        solver.step(.0001)
        difference.append(float(solver.temperature[0, 0, 0] - solver.temperature[1, 0, 0]))
    capacities = material.density * material.capacity(298.65) * np.array([.000421, .00008])
    expected = np.exp(-400. * .01 * np.sum(1 / capacities))
    print('EXCHANGE', 'difference_K', difference[-1], 'reference_K', expected, 'relative_error', difference[-1] / expected - 1)
    assert difference[-1] == pytest.approx(expected, rel=.002)
    assert solver.total_enthalpy() == pytest.approx(initial, rel=2e-10)


def test_time_grid_reaches_end_without_extra_terminal_step(material):
    solver = ThermalSolver(grid(4.), material, {}, [])
    times = np.array([time for time, _, _ in solver.iter_steps(2.6, .001)])
    assert len(times) == 2600
    np.testing.assert_array_equal(times, np.arange(1, 2601) * .001)
    assert solver.time == 2.6
    continued = np.array([time for time, _, _ in solver.iter_steps(2.6055, .001)])
    np.testing.assert_allclose(continued, [2.601, 2.602, 2.603, 2.604, 2.605, 2.6055], atol=1e-14, rtol=0)
    assert solver.time == 2.6055


def test_plane_diffusion_cosine_convergence(material):
    errors = []
    ambient, amplitude, end = 298.15, .01, .001
    diffusivity = float(material.conductivity(ambient) / (material.density * material.capacity(ambient)))
    expected = amplitude * np.exp(-diffusivity * (np.pi / .004) ** 2 * end)
    for spacing, dt in ((1., .0001), (.5, .000025), (.25, .00000625)):
        plate = grid(spacing)
        cosine = np.cos(np.pi * plate.centers[..., 0] / .004)
        solver = ThermalSolver(plate, material, {}, [])
        solver.temperature[0] = ambient + amplitude * cosine
        for _ in range(round(end / dt)):
            solver.step(dt)
        actual = float(np.sum((solver.temperature[0] - ambient) * cosine) / np.sum(cosine ** 2))
        errors.append(abs(actual / expected - 1))
    print('PLANE_DIFFUSION', 'diffusivity_m2_s', diffusivity, 'amplitude_reference_K', expected, 'relative_errors', errors)
    assert errors[2] < errors[1] < errors[0]
    assert errors[2] < .001
    plate = grid(.5)
    cosine = np.cos(np.pi * plate.centers[..., 0] / .004)
    eigenvalue = 4 * np.sin(np.pi * plate.spacing / (.008)) ** 2 / plate.spacing ** 2
    reference = amplitude * np.exp(-diffusivity * eigenvalue * .005)
    time_errors = []
    for dt in (.001, .00025, .0000625):
        solver = ThermalSolver(plate, material, {}, [])
        solver.temperature[0] = ambient + amplitude * cosine
        for _ in range(round(.005 / dt)):
            solver.step(dt)
        actual = float(np.sum((solver.temperature[0] - ambient) * cosine) / np.sum(cosine ** 2))
        time_errors.append(abs(actual / reference - 1))
    print('DIFFUSION_TIME', 'relative_errors', time_errors)
    assert time_errors[2] < time_errors[1] < time_errors[0]


def test_cone_slab_integral_against_independent_quadrature(material):
    source_path = path(kind=5, depth=.2, z=.00008)
    source = source_path['sources'][0]
    solver = ThermalSolver(PlateGrid.from_regions([('plate', box(0, 0, .004, .002), 0., .00008)], .5), material, {}, [source_path])
    center = np.array([.002, .001, .00008])
    def section(z):
        radius = .00008 - .00004 * z / .0002
        limit = min(radius, np.sqrt(max(0., .00016 ** 2 - z ** 2)))
        return quad(lambda r: 2 * np.pi * r * np.exp(-(r / radius) ** 2), 0., limit, epsabs=1e-22)[0]
    reference = quad(section, 0., .00008, epsabs=1e-23)[0] / quad(section, 0., .00016, epsabs=1e-23, points=[.00015])[0]
    actual = float(solver._source_weights(source, center).sum())
    print('CONE_SLAB', 'fraction', actual, 'reference', reference, 'absolute_error', actual - reference)
    assert actual == pytest.approx(reference, abs=1e-8)


def test_subcell_moving_source_power_and_grid_independence(material):
    totals = []
    for spacing in (.2, 1.):
        solver = ThermalSolver(grid(spacing), material, {}, [path()])
        initial = solver.total_enthalpy()
        solver.step(.01)
        assert solver.energy['input'] == pytest.approx(.8 * .01 * .625, rel=1e-12)
        assert solver.energy['absorbed'] + solver.energy['outside'] == pytest.approx(solver.energy['input'], abs=1e-13)
        assert solver.total_enthalpy() - initial == pytest.approx(solver.energy['absorbed'], rel=1e-8)
        totals.append(solver.energy['absorbed'])
    np.testing.assert_allclose(totals, .005, rtol=3e-6)
    print('MOVING_GRID', 'absorbed_J', totals)


def test_moving_source_time_integration_convergence(material):
    energies = []
    for movement in (.5, .25, .125):
        solver = ThermalSolver(grid(.5), material, {'movement_fraction': movement}, [path()])
        energy, incoming = solver._source_energy(0., .01)
        energies.append(energy)
        assert incoming == pytest.approx(.005, abs=1e-14)
    errors = [float(np.sum(np.abs(energy - energies[-1])) / .005) for energy in energies[:-1]]
    print('TIME_INTEGRATION', 'relative_distribution_L1_errors', errors)
    assert errors[0] < 1e-4
    assert errors[1] < 1e-4


def test_finite_depth_and_outside_power(material):
    plate = grid(.5, two=True, gap=.001)
    solver = ThermalSolver(plate, material, {}, [path(kind=5, z=.001501, depth=.1)])
    solver.step(.01)
    np.testing.assert_allclose(solver.temperature[0], 298.15, atol=1e-10)
    assert 0 < solver.energy['absorbed'] < solver.energy['input']
    outside = path()
    outside['points_mm'] = [[-.5, 1., .421], [-.3, 1., .421]]
    solver = ThermalSolver(grid(), material, {}, [outside])
    solver.step(.01)
    assert solver.energy['absorbed'] == 0
    assert solver.energy['outside'] == pytest.approx(solver.energy['input'])


def test_potential_latent_heat_and_cooling_stop(material):
    plate = grid(4.)
    solver = ThermalSolver(plate, material, {}, [])
    solver.temperature[:] = material.solidus
    required = float(material.enthalpy(material.liquidus) - material.enthalpy(material.solidus))
    assert required == pytest.approx(513 * 44 + 205000)
    solver.add_energy(np.full(solver.temperature.shape, solver.mass[0, 0, 0] * required))
    np.testing.assert_allclose(solver.temperature, material.liquidus, atol=1e-10)
    solver = ThermalSolver(plate, material, {'convection_h': 30.}, [])
    solver.temperature[:] = 300.
    list(solver.iter_steps(end=.1, dt=.1, cooling_tolerance=.1, cooling_limit=100.))
    assert np.max(np.abs(solver.temperature - 298.15)) <= .1
    with pytest.raises(RuntimeError, match='冷却'):
        list(ThermalSolver(plate, material, {}, []).iter_steps(end=0, dt=.1, cooling_tolerance=-1))


@pytest.mark.parametrize('hot,cold,dt', [(1600., 1200., .001), (1600., 298.15, .001),
                                       (1800., 1200., .01), (1400., 1000., .005)])
def test_conduction_through_melting_matches_energy_root(material, hot, cold, dt):
    plate = PlateGrid.from_regions([('plate', box(0, 0, .001, .0005), 0., .00005)], .5)
    solver = ThermalSolver(plate, material, {}, [])
    solver.temperature[0, 0] = [hot, cold]
    mass = float(solver.mass[0, 0, 0])
    total = float(material.enthalpy(hot) + material.enthalpy(cold))
    equilibrium = float(material.temperature(total / 2))

    def residual(first):
        second = float(material.temperature(total - material.enthalpy(first)))
        k = material.conductivity(np.array([first, second]))
        conductance = .00005 * 2 / (1 / k[0] + 1 / k[1])
        return mass * (material.enthalpy(first) - material.enthalpy(hot)) + dt * conductance * (first - second)

    expected_first = brentq(residual, equilibrium, hot, xtol=1e-10)
    expected_second = material.temperature(total - material.enthalpy(expected_first))
    initial = solver.total_enthalpy()
    solver.step(dt)
    np.testing.assert_allclose(solver.temperature[0, 0], [expected_first, expected_second], atol=1e-7, rtol=0)
    assert solver.total_enthalpy() == pytest.approx(initial, abs=1e-11)


def test_weld_events_follow_trailing_source_and_exact_area(material):
    plate = grid(.5, two=True, gap=0.)
    source_path = path(kind=5, depth=.2, z=.000501)
    solver = ThermalSolver(plate, material, {}, [source_path])
    solver.step(.005)
    assert solver.events
    assert all(event['time_s'] <= solver.time for event in solver.events)
    assert all(event['geometry'].bounds[2] <= .002 + 1e-12 for event in solver.events)
    first_area = sum(event['area_m2'] for event in solver.events)
    solver.step(.005)
    assert np.any(solver.welded)
    assert sum(event['area_m2'] for event in solver.events) > first_area
    radius = .000064
    reference = 2 * radius * .0026 + np.pi * radius ** 2
    actual = float(sum(area.sum() for area in solver.welded_area))
    for event in solver.events:
        coordinates = np.array(event['geometry'].exterior.coords) if event['geometry'].geom_type == 'Polygon' else np.array(event['geometry'].representative_point().coords)
        assert np.max(event['activation_time'](coordinates)) <= event['time_s'] + 1e-12
    print('WELD_AREA', 'actual_m2', actual, 'reference_m2', reference, 'relative_error', actual / reference - 1)
    assert actual == pytest.approx(reference, rel=1e-6)
    assert actual == pytest.approx(sum(event['area_m2'] for event in solver.events), rel=1e-12)


def test_settings_and_cancellation(material):
    with pytest.raises(ValueError):
        ThermalSolver(grid(), material, {'gap_h': -1}, [])
    solver = ThermalSolver(grid(), material, {}, [])
    with pytest.raises(InterruptedError):
        list(solver.iter_steps(end=.1, dt=.01, cancelled=lambda: True))


def test_rigid_coordinates_preserve_source_and_temperature(material):
    original = grid(.5)
    basis = Rotation.from_euler('xyz', [31., -27., 48.], degrees=True).as_matrix().T
    origin = np.array([.13, -.24, .86])
    transformed = PlateGrid.from_regions([('lower', box(0, 0, .004, .002), 0., .000421)], .5,
                                         origin=origin, basis=basis)
    source_path = path()
    points = np.asarray(source_path['points_mm']) / 1000
    moved_path = dict(source_path, points_mm=((points @ basis + origin) * 1000).tolist(),
                      normals=(np.asarray(source_path['normals']) @ basis).tolist())
    np.testing.assert_allclose(transformed.project(points @ basis + origin), points, atol=2e-16)
    first = ThermalSolver(original, material, {}, [source_path])
    second = ThermalSolver(transformed, material, {}, [moved_path])
    first.step(.01)
    second.step(.01)
    np.testing.assert_allclose(second.temperature, first.temperature, atol=1e-8, rtol=0.)
    assert second.energy == pytest.approx(first.energy, abs=1e-12)


def test_gaussian_clipped_boundary_against_independent_integral():
    radius = .00008
    center = np.array([-.000025, 0.])
    lower = -center[0] / radius
    expected = quad(lambda x: np.exp(-x * x) * np.sqrt(np.pi) * erf(np.sqrt(1 - x * x)), lower, 1., epsabs=1e-12)[0] / (np.pi * (1 - np.exp(-1)))
    actual = _fraction(box(0, -.001, .001, .001), center, radius, radius, 1.)
    print('GAUSSIAN_BOUNDARY', 'fraction', actual, 'reference', expected, 'absolute_error', actual - expected)
    assert actual == pytest.approx(expected, abs=2e-6)


def test_reused_circle_edges_preserve_gauss_integrals():
    for gaussian in (0., 1., 2.):
        for radius in (.3, 1., 2.):
            circle, normalization = _circle(gaussian, radius)
            polygons = [circle.intersection(box(-.1, -.2, .7, 2.)),
                        circle.difference(box(-.05, -.05, .05, .05)), circle]
            for polygon in polygons:
                reference = _boundary_integral(polygon, gaussian)
                actual = _boundary_integral(polygon, gaussian, radius, normalization / 1024)
                assert actual == pytest.approx(reference, abs=2e-14, rel=2e-14)


def gaussian_rectangle_reference(bounds, gaussian):
    xmin, ymin, xmax, ymax = bounds
    scale = np.sqrt(gaussian)
    first, last = max(-1., xmin) * scale, min(1., xmax) * scale
    first, last = max(first, -12.), min(last, 12.)
    if last <= first:
        return 0.

    def section(u):
        extent = np.sqrt(max(0., gaussian - u * u))
        lower, upper = max(ymin * scale, -extent), min(ymax * scale, extent)
        return 0. if upper <= lower else np.exp(-u * u) * np.sqrt(np.pi) / 2 * (erf(upper) - erf(lower))

    return quad(section, first, last, epsabs=1e-13, epsrel=1e-12)[0] / (np.pi * -np.expm1(-gaussian))


@pytest.mark.parametrize('gaussian', [30., 100., 1000., 1e6])
def test_high_gaussian_cells_against_independent_integral(gaussian):
    edge = .3 / np.sqrt(gaussian)
    bounds_list = [(.01, -2., 2., 2.), (edge, -2., 2., 2.),
                   (-edge, -.7 / np.sqrt(gaussian), 2 * edge, 2.),
                   (-2., -2., -edge, 2.)]
    errors = []
    for bounds in bounds_list:
        actual = _fraction(box(*bounds), np.zeros(2), 1., 1., gaussian)
        reference = gaussian_rectangle_reference(bounds, gaussian)
        errors.append(abs(actual - reference))
        assert actual == pytest.approx(reference, abs=2e-10)
    print('HIGH_GAUSSIAN_CELLS', gaussian, 'maximum_absolute_error', max(errors))


@pytest.mark.parametrize('gaussian', [30., 100., 1000., 1e6])
def test_high_gaussian_material_sum_against_independent_integral(material, gaussian):
    radius = .001
    edge = .3 / np.sqrt(gaussian)
    plate = PlateGrid.from_regions([('plate', box(edge * radius, -radius, 2 * radius, radius), 0., .00008)], .25)
    source_path = path(radius=1., z=.00008)
    source = source_path['sources'][0]
    source['gaussian'] = gaussian
    solver = ThermalSolver(plate, material, {}, [source_path])
    actual = solver._source_weights(source, np.array([0., 0., .00008]))
    reference = np.zeros(actual.shape)
    for j, i in np.argwhere(plate.layers[0].mask):
        bounds = np.array(plate.layers[0].polygons[j, i].bounds) / radius
        reference[0, j, i] = gaussian_rectangle_reference(bounds, gaussian)
    np.testing.assert_allclose(actual, reference, atol=2e-10, rtol=0.)
    expected_sum = gaussian_rectangle_reference((edge, -1., 2., 1.), gaussian)
    print('HIGH_GAUSSIAN_MATERIAL', gaussian, 'absorbed_fraction', actual.sum(),
          'reference', expected_sum, 'maximum_cell_error', np.max(np.abs(actual - reference)))
    assert actual.sum() == pytest.approx(expected_sum, abs=2e-10)


def test_fixture_radiation_and_cooling_limit(material):
    solver = ThermalSolver(grid(4.), material, {'fixture_mask': True, 'fixture_h': 200., 'emissivity': .8}, [])
    solver.temperature[:] = 310.
    initial = solver.total_enthalpy()
    solver.step(.1)
    assert 298.15 < solver.temperature[0, 0, 0] < 310.
    assert solver.energy['loss'] == pytest.approx(initial - solver.total_enthalpy(), rel=1e-12)
    hot = ThermalSolver(grid(4.), material, {}, [])
    hot.temperature[:] = 310.
    with pytest.raises(RuntimeError, match='冷却'):
        list(hot.iter_steps(end=0., dt=.1, cooling_tolerance=.01, cooling_limit=.1))


@pytest.fixture(scope='module')
def real_grid():
    start = perf_counter()
    PYTHON_DAT = reference_path('SFMARC_TEST_C0002_DAT')
    model = SurfaceModel(Deck({PYTHON_DAT.name: PYTHON_DAT.read_bytes()}, PYTHON_DAT.name))
    result = PlateGrid.from_model(model, 'm', .5, weld_spacing_mm=.5)
    result._test_deck = model.deck
    print('REAL_GEOMETRY', result.diagnostics, 'elapsed_s', perf_counter() - start)
    return result


def test_real_geometry_and_flux_parameters(real_grid):
    assert len(real_grid.layers) == 2
    lower, upper = real_grid.layers
    np.testing.assert_allclose(lower.thickness[lower.mask], .000421, atol=1e-8)
    np.testing.assert_allclose(sorted(set(round(region.top - region.bottom, 8) for region in upper.regions if region.bottom_plane is None)), [.00005, .00008], atol=1e-8)
    assert real_grid.origin[2] + max(region.top for region in upper.regions) == pytest.approx(.9925490003445, abs=1e-11)
    gaps = [round(part[1], 8) for contact in real_grid.contacts for part in contact.regions if part[4].bottom_plane is None]
    np.testing.assert_allclose(sorted(set(gaps)), [0., .00003], atol=1e-8)
    assert all(abs(item['volume_relative_error']) < 1e-5 for item in real_grid.diagnostics)
    sources = real_grid.plan['paths'][0]['sources']
    assert len(sources) == 2
    surface = next(item for item in sources if item['kind'] == 7)
    assert surface['power_W'] == 222.
    assert surface['radius_mm'] == pytest.approx(.08)
    assert surface['efficiency'] == .2
    assert np.ptp(np.asarray(surface['power_table'])[:, 1]) > 0
    assert [path['id'] for path in real_grid.plan['paths']] == [2900000, 2900001, 2900002, 2900003]
    assert [len(path['points_mm']) for path in real_grid.plan['paths']] == [269, 70, 93, 120]
    assert real_grid.plan['end_s'] == pytest.approx(2.6)
    unspecified = read_weld_paths(real_grid._test_deck, 'mm')['paths'][0]['sources'][0]
    assert unspecified['power_W'] is None
    scaled = read_weld_paths(real_grid._test_deck, 'mm', power_scale_W=.001)['paths'][0]['sources'][0]
    assert scaled['power_W'] == pytest.approx(scaled['raw_power'] * .001)
    for contact in real_grid.contacts:
        point = np.array(contact.polygon.representative_point().coords[0])
        assert contact.gap_at(point) >= 0


def test_real_short_source(real_grid, material):
    initialized = perf_counter()
    solver = ThermalSolver(real_grid, material, {'gap_h': 20., 'contact_h': 200.}, real_grid.plan['paths'])
    print('REAL_THERMAL_INITIALIZATION', perf_counter() - initialized)
    started = perf_counter()
    for _ in range(5):
        solver.step(.00002)
    print('REAL_THERMAL', solver.energy, 'Tmax_K', np.max(solver.temperature), 'elapsed_s', perf_counter() - started)
    assert solver.energy['input'] > 0
    assert 0 < solver.energy['absorbed'] <= solver.energy['input']
    assert solver.energy['input'] == pytest.approx(solver.energy['absorbed'] + solver.energy['outside'], abs=1e-12)
    assert np.max(solver.temperature) > 298.15


def test_real_crop_surface_and_weld_contact_cells(real_grid, material):
    boundary = box(.004468213730834799, -.0397193618846552, .0084682137308348, -.035719361884655194)
    regions = []
    for layer in real_grid.layers:
        for region in layer.regions:
            polygon = region.polygon.intersection(boundary)
            if polygon.area > 0:
                regions.append((layer.name, polygon, region.bottom, region.top, region.bottom_plane, region.top_plane))
    plate = PlateGrid.from_regions(regions, .5, real_grid.origin, real_grid.basis)
    solver = ThermalSolver(plate, replace(material, thermal_extrapolation=True), {'gap_h': 20.}, real_grid.plan['paths'])
    assert np.count_nonzero(solver.external_area[~solver.mask]) == 0
    solver.step(.001)
    assert np.isfinite(solver.temperature).all()
    solver._update_welds(0., real_grid.plan['end_s'])
    assert solver.welded.any()
    assert not solver.welded[~solver.mask].any()


def test_real_millisecond_step_performance(real_grid, material):
    solver = ThermalSolver(real_grid, material, {'gap_h': 20., 'contact_h': 200.}, real_grid.plan['paths'])
    started = perf_counter()
    solver.step(.001)
    elapsed = perf_counter() - started
    print('REAL_MILLISECOND', solver.energy, 'Tmax_K', np.max(solver.temperature), 'elapsed_s', elapsed, 'estimated_2p6s_seconds', elapsed * 2600)
    assert solver.energy['absorbed'] > 0
