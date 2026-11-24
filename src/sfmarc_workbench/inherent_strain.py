from io import BytesIO
from dataclasses import replace
import json
import logging
from tempfile import TemporaryFile
from pathlib import Path
import sys
from threading import Event, RLock, Thread
from time import perf_counter
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
import shapely

from .plate_geometry import PlateGrid
from .plate_thermal import ThermalSolver
from .plate_structure import Structure
from .plate_mechanics import Mechanics
from .plate_inherent import constrained_thermal_update
from .plate_material import MaterialState
from .thermal_deck import read_weld_paths
from .contact_geometry import read_thermal_conditions


class TemperatureJob:
    def __init__(self, model, model_id, revision, settings):
        self.model, self.model_id, self.revision = model, model_id, revision
        self.job_id = uuid4().hex
        self.settings = settings
        self.grid = None
        self.preview = None
        self.material = None
        self.material_info = None
        self.frames = []
        self.peak = None
        self.mechanical_result = None
        self.history = None
        self.strain_result = None
        self.lock = RLock()
        self.cancel = Event()
        self.thread = None
        self.started = perf_counter()
        self.state = dict(job_id=self.job_id, model_id=model_id, revision=revision, status='running',
                          stage='正在准备计算数据', operation='prepare', phase='geometry', time_s=0., target_s=0.,
                          completed=0., total=None, progress_unit='', prepare_elapsed_s=0., calculation_elapsed_s=0.,
                          max_temperature_c=None, steps=0, frames=0, energy={}, error=None, elapsed_s=0.,
                          step_min_s=None, step_max_s=None)

    def status(self):
        with self.lock:
            state = dict(self.state)
            running = self.thread is not None and self.thread.is_alive()
            if running:
                elapsed = perf_counter() - self.started
                state.update(elapsed_s=elapsed)
                state['prepare_elapsed_s' if state['operation'] == 'prepare' else 'calculation_elapsed_s'] = elapsed
            return dict(state, can_stop=running and not self.cancel.is_set(), stop_requested=self.cancel.is_set())

    def update(self, **values):
        with self.lock:
            self.state.update(values)

    def run(self, action, operation=None):
        if self.thread is not None and self.thread.is_alive():
            raise ValueError('当前任务尚未结束')
        self.cancel.clear()
        self.started = perf_counter()
        operation = operation or ('prepare' if self.grid is None else 'calculate')
        self.update(status='running', operation=operation, phase='geometry' if operation == 'prepare' else 'thermal',
                    stage='正在准备计算数据' if operation == 'prepare' else '正在初始化计算',
                    error=None, elapsed_s=0., **{operation + '_elapsed_s' if operation == 'prepare' else 'calculation_elapsed_s': 0.})

        def worker():
            try:
                action()
            except InterruptedError:
                self.update(status='cancelled', stage='准备已停止' if operation == 'prepare' else '计算已停止，已完成的温度帧可查看和导出')
            except Exception as error:
                root = Path(getattr(sys, '_sd_root', Path.cwd())) / 'work' / 'inherent-strain'
                root.mkdir(parents=True, exist_ok=True)
                handler = logging.FileHandler(root / 'temperature-errors.log', encoding='utf-8')
                logger = logging.getLogger('sfmarc_workbench.inherent_strain')
                logger.addHandler(handler)
                try:
                    logger.exception('温度任务 %s 失败', self.job_id)
                finally:
                    logger.removeHandler(handler)
                    handler.close()
                self.update(status='failed', stage='任务失败', error=str(error))
            finally:
                elapsed = perf_counter() - self.started
                self.update(elapsed_s=elapsed, **{'prepare_elapsed_s' if operation == 'prepare' else 'calculation_elapsed_s': elapsed})

        self.thread = Thread(target=worker, name='inherent-temperature-' + self.job_id, daemon=True)
        self.thread.start()

    def check_cancel(self):
        if self.cancel.is_set():
            raise InterruptedError('任务已取消')

    def prepare(self):
        plan = read_weld_paths(self.model.deck, self.settings['length_unit'],
                               power_scale_W=1. if self.settings['length_unit'] == 'm' else .001)
        self.boundaries = read_thermal_conditions(self.model.deck)
        sources = [source for path in plan['paths'] for source in path['sources']]
        radii = [source[key] for source in sources for key in
                 (('radius_mm',) if source['kind'] == 7 else ('upper_radius_mm', 'lower_radius_mm'))
                 if source.get(key, 0) > 0]
        if not radii:
            raise ValueError('DAT 热源没有有效半径，无法确定计算网格')
        near = min(radii) / 4
        defaults = dict(weld_spacing_mm=near, spacing_mm=4 * near,
                        refine_distance_mm=max(source['cutoff_mm'] for source in sources) + 2 * near,
                        end_s=plan['end_s'], initial_c=plan['room_c'] if plan['room_c'] is not None else 25.,
                        ambient_c=25., convection_h=10., emissivity=.2, contact_h=0., gap_h=0., weld_h=10000.,
                        fixture_h=0., fixture_c=25., record_interval_s=max(plan['end_s'] / 200, 1e-6))
        for name in ('spacing_mm', 'weld_spacing_mm', 'refine_distance_mm'):
            if self.settings.get(name) is None:
                self.settings[name] = defaults[name]
        self.update(grid_parameters={name: self.settings[name] for name in ('spacing_mm', 'weld_spacing_mm', 'refine_distance_mm')})
        def checkpoint(message):
            self.check_cancel()
            self.update(stage=message)
        grid = PlateGrid.from_model(self.model, self.settings['length_unit'], self.settings['spacing_mm'],
                                    weld_spacing_mm=self.settings.get('weld_spacing_mm'),
                                    refine_distance_mm=self.settings['refine_distance_mm'], checkpoint=checkpoint)
        bodies = self.boundaries['bodies']
        plates = {body['name']: body for body in bodies if body['kind'] == 0}
        if plates and any(layer.name not in plates for layer in grid.layers):
            raise ValueError('CONTACT 工件名称与二维板层名称不一致')
        sources_info = {name: '程序根据热源与几何计算' for name in defaults}
        sources_info.update(end_s='DAT 阶段持续时间', initial_c='DAT INITIAL TEMP' if plan['room_c'] is not None else '程序默认 25 ℃',
                            weld_h='程序推荐焊后连接系数 10000 W·m⁻²·K⁻¹')
        for name in ('ambient_c', 'convection_h', 'emissivity'):
            if plates:
                defaults[name] = plates[grid.layers[0].name][name]
                sources_info[name] = 'DAT CONTACT，按工件分别使用；输入框显示首个板层'
            else:
                sources_info[name] = '程序默认值，DAT 未定义环境换热'
        by_id = {body['id']: body for body in bodies}
        plate_pairs, fixture_pairs = [], []
        for pair in self.boundaries['pairs']:
            first, second = by_id[pair['first']], by_id[pair['second']]
            (plate_pairs if first['kind'] == second['kind'] == 0 else fixture_pairs).append(pair)
        for name in ('contact_h', 'gap_h'):
            defaults[name] = plate_pairs[0]['h'] if plate_pairs else 0.
            sources_info[name] = ('程序等效值 1000 W·m⁻²·K⁻¹，DAT 使用 Simufact 特殊接触定义'
                                  if any(pair['equivalent'] for pair in plate_pairs) else
                                  'DAT CONTACT TABLE，按接触对使用' if plate_pairs else 'DAT 未定义工件接触换热，取 0')
        if fixture_pairs:
            pair = fixture_pairs[0]
            rigid = next(by_id[ident] for ident in (pair['first'], pair['second']) if by_id[ident]['kind'] == 1)
            defaults.update(fixture_h=pair['h'], fixture_c=rigid['temperature_c'])
        sources_info.update(fixture_h='DAT CONTACT TABLE，按夹具接触区域使用' if fixture_pairs else 'DAT 未定义夹具换热，取 0',
                            fixture_c='DAT CONTACT 夹具温度' if fixture_pairs else '程序默认 25 ℃',
                            dt_s='程序按热源尺寸、网格和速度计算；求解时自动调整',
                            record_interval_s='程序根据 DAT 工艺时间计算')
        fixture_outlines = []
        if any(body['kind'] == 1 for body in bodies):
            surfaces = Structure._fixtures(self.model, self.settings['length_unit'], None, grid)
            for role, records in surfaces.items():
                for polygon, plane in records:
                    for part in shapely.get_parts(polygon):
                        for ring in [part.exterior, *part.interiors]:
                            xy = np.asarray(ring.coords)
                            world = (np.c_[xy, xy @ plane[:2] + plane[2]] @ grid.basis + grid.origin) * 1000
                            fixture_outlines.append(dict(name=role, points_mm=world.ravel().tolist()))
        scales = []
        for path in plan['paths']:
            distance = np.linalg.norm(np.diff(np.asarray(path['points_mm']), axis=0), axis=1).sum()
            speed = distance / (path['end_s'] - path['start_s'])
            scales.append(.25 * min(min(radii), np.diff(grid.x).min() * 1000, np.diff(grid.y).min() * 1000) / speed)
        defaults['dt_s'] = min(.1, *scales)
        self.check_cancel()
        self.update(phase='mesh', stage='生成计算单元轮廓', total=sum(int(layer.mask.sum()) for layer in grid.layers), progress_unit='单元')
        positions, triangles, triangle_cells, outlines, outline_layers, cells, layers = [], [], [], [], [], [], []
        self.active = []
        for index, layer in enumerate(grid.layers):
            self.check_cancel()
            count = 0
            for flat in np.flatnonzero(layer.mask.ravel()):
                self.check_cancel()
                polygon = layer.polygons.ravel()[flat]
                height = layer.top.ravel()[flat]
                cell_id = len(cells)
                self.active.append(index * grid.cells.size + int(flat))
                cells.append([index, int(flat)])
                for part in shapely.get_parts(polygon):
                    if part.geom_type != 'Polygon':
                        continue
                    for ring in [part.exterior, *part.interiors]:
                        xy = np.asarray(ring.coords)
                        world = (np.c_[xy, np.full(len(xy), height)] @ grid.basis + grid.origin) * 1000
                        for first, second in zip(world[:-1], world[1:]):
                            outlines.extend([*first, *second])
                            outline_layers.append(index)
                    for triangle in shapely.get_parts(shapely.constrained_delaunay_triangles(part)):
                        xy = np.asarray(triangle.exterior.coords)[:3]
                        world = (np.c_[xy, np.full(3, height)] @ grid.basis + grid.origin) * 1000
                        start = len(positions) // 3
                        positions.extend(world.ravel().tolist())
                        triangles.extend([start, start + 1, start + 2])
                        triangle_cells.append(cell_id)
                count += 1
                if count % 100 == 0:
                    self.update(completed=len(cells))
            thickness = layer.thickness[layer.mask] * 1000
            layers.append(dict(index=index, name=layer.name, cell_count=count,
                               area_mm2=float(layer.area.sum() * 1e6), thickness_mm=[float(thickness.min()), float(thickness.max())],
                               bottom_mm=float(min(part.bottom for part in layer.regions) * 1000),
                               top_mm=float(max(part.top for part in layer.regions) * 1000)))
        self.grid = grid
        self.preview = dict(job_id=self.job_id, model_id=self.model_id, revision=self.revision,
                            spacing_mm=grid.spacing * 1000, cell_count=len(cells), layers=layers,
                            positions_mm=positions, triangles=triangles, triangle_cells=triangle_cells,
                            cells=cells, outlines_mm=outlines, outline_layers=outline_layers, reference_origin_mm=(grid.origin * 1000).tolist(),
                            basis=grid.basis.tolist(), contacts=[dict(lower=c.lower, upper=c.upper, gap_mm=c.gap * 1000) for c in grid.contacts],
                            paths=grid.plan['paths'], end_s=grid.plan['end_s'], defaults=defaults,
                            fixtures=fixture_outlines,
                            boundaries=dict(bodies=[{key: value for key, value in body.items() if key not in ('points', 'faces')}
                                                    for body in bodies], pairs=self.boundaries['pairs'], stage_count=self.boundaries['stage_count']),
                            effective_grid={name: self.settings[name] for name in ('spacing_mm', 'weld_spacing_mm', 'refine_distance_mm')},
                            sources=sources_info)
        self.check_cancel()
        self.update(status='ready', phase='ready', stage='计算数据已准备完成', target_s=grid.plan['end_s'], completed=len(cells), total=len(cells))

    def calculate(self, settings):
        self.reset_stages()
        settings = dict(settings)
        overrides = {name for name, value in settings.items() if value is not None}
        for name in self.preview['defaults']:
            if name in ('spacing_mm', 'weld_spacing_mm', 'refine_distance_mm'):
                continue
            if settings.get(name) is None:
                settings[name] = self.preview['defaults'][name]
        settings['manual_parameters'] = sorted(overrides - {'model_id', 'revision', 'job_id', 'mechanics', 'staged'})
        self.calculation = settings
        self.frames = []
        self.mechanical_result = None
        thermal = dict(ambient=settings['ambient_c'] + 273.15, initial_temperature=settings['initial_c'] + 273.15,
                       convection_h=settings['convection_h'], emissivity=settings['emissivity'],
                       contact_h=settings['contact_h'], gap_h=settings['gap_h'], weld_h=settings['weld_h'],
                       fixture_h=settings['fixture_h'], fixture_temperature=settings['fixture_c'] + 273.15)
        bodies = {body['id']: body for body in self.boundaries['bodies']}
        plates = {body['name']: body for body in bodies.values() if body['kind'] == 0}
        for name in ('ambient_c', 'convection_h', 'emissivity'):
            if name not in overrides and plates:
                thermal['ambient' if name == 'ambient_c' else name] = np.array(
                    [plates[layer.name][name] + (273.15 if name == 'ambient_c' else 0.) for layer in self.grid.layers])[:, None, None]
        pair_lookup = {frozenset((bodies[pair['first']]['name'], bodies[pair['second']]['name'])): pair for pair in self.boundaries['pairs']}
        for name in ('contact_h', 'gap_h'):
            if name not in overrides:
                thermal[name] = [pair_lookup.get(frozenset((self.grid.layers[contact.lower].name, self.grid.layers[contact.upper].name)), {}).get('h', 0.)
                                 for contact in self.grid.contacts]
        fixture_pairs = []
        for pair in self.boundaries['pairs']:
            first, second = bodies[pair['first']], bodies[pair['second']]
            if first['kind'] != second['kind']:
                fixture_pairs.append((first if first['kind'] == 0 else second, second if second['kind'] == 1 else first, pair['h']))
        if settings['fixture_h'] > 0 or fixture_pairs:
            fixture = np.zeros((len(self.grid.layers), *self.grid.cells.shape))
            conductance, reservoir = np.zeros_like(fixture), np.zeros_like(fixture)
            if not fixture_pairs:
                fixture_pairs = [(None, body, settings['fixture_h']) for body in bodies.values() if body['kind'] == 1]
            for plate, rigid, h in fixture_pairs:
                self.check_cancel()
                surfaces = {role: Structure._fixtures(self.model, self.settings['length_unit'], {role: [rigid['name']]}, self.grid)[role]
                            for role in ('base', 'cover')}
                h = settings['fixture_h'] if 'fixture_h' in overrides else h
                temperature = settings['fixture_c'] if plate is None or 'fixture_c' in overrides else rigid['temperature_c']
                for index, layer in enumerate(self.grid.layers):
                    if plate is not None and layer.name != plate['name']:
                        continue
                    for role, records in surfaces.items():
                        height = layer.bottom if role == 'base' else layer.top
                        coverage = np.zeros(layer.area.shape)
                        for polygon, plane in records:
                            overlap = shapely.intersection(layer.polygons, polygon)
                            boundary = self.grid.centers @ plane[:2] + plane[2]
                            coverage += np.where(layer.mask & (np.abs(height - boundary) <= 1e-7), shapely.area(overlap), 0.)
                        fraction = np.minimum(coverage, layer.area) / np.where(layer.mask, layer.area, 1.)
                        fixture[index] += fraction
                        conductance[index] += fraction * h
                        reservoir[index] += fraction * h * (temperature + 273.15)
            thermal['fixture_mask'] = fixture
            thermal['fixture_h'] = np.divide(conductance, fixture, out=np.zeros_like(fixture), where=fixture > 0)
            thermal['fixture_temperature'] = np.divide(reservoir, conductance, out=np.full_like(fixture, settings['fixture_c'] + 273.15), where=conductance > 0)
        self.update(operation='calculate', phase='thermal', stage='初始化温度计算', time_s=0., steps=0, frames=0,
                    completed=0., total=settings['end_s'], progress_unit='s', step_min_s=None, step_max_s=None,
                    target_s=settings['end_s'], max_temperature_c=settings['initial_c'], energy={})
        material = replace(self.material, thermal_extrapolation=True)
        solver = ThermalSolver(self.grid, material, thermal, self.grid.plan['paths'])
        mechanics = None
        strain_state = (MaterialState.zeros((len(self.active),), thermal['initial_temperature'])
                        if settings.get('mechanics', False) else None)

        staged = settings.get("staged", False)
        if staged:
            self.history = TemporaryFile()

        def accumulate(temperature):
            nonlocal strain_state
            if staged:
                self.history.write(temperature.ravel()[self.active].astype(np.float64).tobytes())
            elif strain_state is not None:
                _, strain_state, _ = constrained_thermal_update(
                    self.material, temperature.ravel()[self.active], strain_state,
                    allow_constant_extension=True, reference_temperature=thermal['initial_temperature'])
        self.peak = solver.temperature.ravel()[self.active].copy() - 273.15
        self.check_cancel()

        accepted_time = 0.
        accepted_values = self.peak.copy()
        def save():
            with self.lock:
                self.frames.append((accepted_time, accepted_values.copy()))
                self.state['frames'] = len(self.frames)

        save()
        next_record = settings['record_interval_s']
        events = {value for path in solver.paths for value in
                  [*path['_times'], *[row[0] for source in path['sources'] for row in source['power_table']]]}
        steps = 0
        try:
            for time, temperature, _ in solver.iter_steps(settings['end_s'], settings['dt_s'], cancelled=self.cancel, adaptive=True):
                step = time - accepted_time
                accepted_time, accepted_values = float(time), temperature.ravel()[self.active].copy() - 273.15
                steps += 1
                with self.lock:
                    np.maximum(self.peak, temperature.ravel()[self.active] - 273.15, out=self.peak)
                accumulate(temperature)
                if time >= next_record - 1e-12 or time >= settings['end_s'] - 1e-12 or any(abs(time - event) < 1e-12 for event in events):
                    save()
                    next_record = (np.floor(time / settings['record_interval_s']) + 1) * settings['record_interval_s']
                self.update(stage='计算 DAT 工艺温度' if staged else '计算 DAT 工艺温度与累计应变', time_s=float(time), steps=steps, completed=float(time),
                            step_min_s=min(self.state['step_min_s'] or step, step), step_max_s=max(self.state['step_max_s'] or step, step),
                            max_temperature_c=float(temperature.ravel()[self.active].max() - 273.15), energy=dict(solver.energy))
            self.check_cancel()
            if strain_state is not None:
                if solver.time >= self.grid.plan['end_s']:
                    self.update(phase='cooling', stage='冷却至结构计算条件，目标温差 5 ℃', completed=0., total=None)
                    for time, temperature, _ in solver.iter_steps(solver.time, .5, cooling_tolerance=5.,
                                                                  cooling_limit=3600., cancelled=self.cancel, adaptive=True):
                        step = time - accepted_time
                        accepted_time, accepted_values = float(time), temperature.ravel()[self.active].copy() - 273.15
                        steps += 1
                        with self.lock:
                            np.maximum(self.peak, temperature.ravel()[self.active] - 273.15, out=self.peak)
                        accumulate(temperature)
                        if time >= next_record:
                            save()
                            next_record = time + max(settings['record_interval_s'], .1)
                        self.update(time_s=float(time), steps=steps, stage='焊后冷却，目标温差 5 ℃' if staged else '焊后冷却与塑性应变累计；目标温差 5 ℃',
                                    step_min_s=min(self.state['step_min_s'] or step, step), step_max_s=max(self.state['step_max_s'] or step, step),
                                    max_temperature_c=float(temperature.ravel()[self.active].max() - 273.15),
                                    energy=dict(solver.energy))
                self.check_cancel()
                if staged:
                    self.update(status='thermal_done', phase='thermal_done', stage='温度场已完成，可计算固有应变')
                    return
                mechanics = self.solve_displacement(strain_state, thermal['initial_temperature'], solver.time)
            self.update(status='complete', phase='complete', completed=1., total=1., progress_unit='', target_s=float(solver.time), stage=('夹具释放与鼓包计算已完成' if mechanics is not None and not mechanics.cover
                                                 else '指定时段计算已完成'))
        finally:
            if not self.frames or self.frames[-1][0] != accepted_time:
                save()

    def reset_stages(self):
        if self.history is not None:
            self.history.close()
            self.history = None
        self.strain_result = None

    def solve_strain(self):
        reference = self.calculation['initial_c'] + 273.15
        state = MaterialState.zeros((len(self.active),), reference)
        width = len(self.active) * 8
        self.history.seek(0)
        step = 0
        self.update(phase='strain', stage='按温度历程计算固有应变', total=self.state['steps'], completed=0)
        while data := self.history.read(width):
            self.check_cancel()
            _, state, _ = constrained_thermal_update(
                self.material, np.frombuffer(data, dtype=np.float64), state,
                allow_constant_extension=True, reference_temperature=reference)
            step += 1
            self.update(completed=step)
        self.history.close()
        self.history = None
        self.strain_result = state
        self.update(status='strain_done', phase='strain_done', stage='固有应变已完成，可计算位移')

    def solve_staged_displacement(self):
        self.solve_displacement(self.strain_result, self.calculation['initial_c'] + 273.15, self.state['time_s'])
        self.update(status='complete', phase='complete', stage='位移计算完成', completed=1, total=1)

    def solve_displacement(self, strain_state, initial_temperature, end_time):
        self.update(phase='structure', stage='生成 Shell 网格与 DAT 夹具约束', total=None)
        structure = Structure(self.grid, self.material, spacing_mm=self.grid.spacing * 1000,
                              model=self.model, length_unit=self.settings['length_unit'])
        if not structure.fixture_surfaces['base'] or not structure.fixture_surfaces['cover']:
            raise ValueError('DAT 中缺少可确定作用区域的 Base 或 Cover 数据')
        if not len(structure.cover_dofs) or not np.any(structure.contact_types == 0):
            raise ValueError('DAT 夹具与板件没有有效接触区域，请检查坐标、单位和接触位置')
        shape = (len(structure.connectivity), 4, 5)
        state = MaterialState(
            np.broadcast_to((structure.map @ strain_state.plastic)[:, None, None, :], (*shape, 6)).copy(),
            np.broadcast_to((structure.map @ strain_state.equivalent)[:, None, None], shape).copy(),
            np.broadcast_to((structure.map @ strain_state.peak_temperature)[:, None, None], shape).copy(),
            np.broadcast_to((structure.map @ np.asarray(strain_state.extrapolated, dtype=float))[:, None, None] > 0, shape).copy())
        mechanics = Mechanics(structure, self.material, initial_temperature=initial_temperature,
                              inherent_state=state, cancelled=self.cancel.is_set,
                              progress=lambda message: self.update(stage=message))
        mechanics.time_s = float(end_time)
        self.update(stage='整体施加累计固有应变并求解 Shell 平衡')
        mechanics.apply_inherent()
        if end_time >= self.grid.plan['end_s']:
            self.update(phase='release', stage='解除 Cover 约束并求解最终变形')
            mechanics.release()
        self.mechanical_result = self.mechanics_preview(mechanics)
        return mechanics

    def mechanics_preview(self, mechanics):
        structure = mechanics.structure
        result = mechanics.result()
        cells = np.asarray(self.preview['cells'])
        positions = np.asarray(self.preview['positions_mm']).reshape(-1, 3)
        projected = self.grid.project(positions / 1000)
        vertex_cells = np.repeat(np.asarray(self.preview['triangle_cells']), 3)
        layers = cells[vertex_cells, 0]
        ii = np.clip(np.searchsorted(structure.grid.x, projected[:, 0], side='right') - 1, 0, len(structure.grid.x) - 2)
        jj = np.clip(np.searchsorted(structure.grid.y, projected[:, 1], side='right') - 1, 0, len(structure.grid.y) - 2)
        lookups = np.asarray(structure.element_lookups)
        elements = lookups[layers, jj, ii]
        for dj, di in ((0, -1), (-1, 0), (-1, -1)):
            missing = elements < 0
            candidate_j, candidate_i = np.maximum(0, jj + dj), np.maximum(0, ii + di)
            inside = ((projected[:, 0] >= structure.grid.x[candidate_i] - 1e-12)
                      & (projected[:, 0] <= structure.grid.x[candidate_i + 1] + 1e-12)
                      & (projected[:, 1] >= structure.grid.y[candidate_j] - 1e-12)
                      & (projected[:, 1] <= structure.grid.y[candidate_j + 1] + 1e-12))
            candidate = lookups[layers, candidate_j, candidate_i]
            selected = missing & inside & (candidate >= 0)
            elements[selected] = candidate[selected]
        if np.any(elements < 0):
            raise ValueError('温度预览顶点没有对应的 Shell 单元')
        j, i = structure.cells[elements].T
        xi = (projected[:, 0] - structure.grid.x[i]) / np.diff(structure.grid.x)[i]
        eta = (projected[:, 1] - structure.grid.y[j]) / np.diff(structure.grid.y)[j]
        weights = np.array([(1-xi)*(1-eta), xi*(1-eta), xi*eta, (1-xi)*eta]).T
        nodal = result['q'][structure.connectivity[elements]]
        q = np.einsum('vi,vij->vj', weights, nodal)
        z = projected[:, 2] - structure.references[structure.element_layers[elements]]
        displacement = np.c_[q[:, 0] + z*q[:, 4], q[:, 1] - z*q[:, 3], q[:, 2]]
        displacement = displacement @ self.grid.basis * 1000
        section_weights = np.polynomial.legendre.leggauss(5)[1] / 2
        plastic = np.einsum('ejk,j->ek', result['plastic'].mean(axis=1), section_weights)
        equivalent = np.einsum('ej,j->e', result['equivalent'].mean(axis=1), section_weights)
        normal = result['q'][structure.connectivity, 2].mean(axis=1) * 1000
        thermal_area = np.array([self.grid.layers[layer].area.ravel()[flat] for layer, flat in cells])
        transfer = structure.map.T.multiply(structure.area[None, :]).tocsr()
        normal_preview = (transfer @ normal) / thermal_area
        equivalent_preview = (transfer @ equivalent) / thermal_area
        plastic_preview = (transfer @ plastic) / thermal_area[:, None]
        self.mechanical_arrays = result
        self.mechanical_structure = structure
        return dict(job_id=self.job_id, released=not result['cover'], positions_mm=positions.ravel().tolist(),
                    displacement_mm=displacement.ravel().tolist(), normal_displacement_mm=normal_preview.tolist(),
                    equivalent_plastic_strain=equivalent_preview.tolist(), plastic_strain=plastic_preview.tolist(),
                    summary=dict(time_s=result['time_s'], residual_N=result['residual_N'],
                                 minimum_mm=float(normal.min()), maximum_mm=float(normal.max()),
                                 formulation=result['formulation'], structure=structure.metadata()))

    def export(self):
        with self.lock:
            times = np.array([row[0] for row in self.frames])
            temperatures = np.array([row[1] for row in self.frames])
            state = self.status()
        metadata = dict(state, parameters=self.calculation, grid_parameters=self.settings, material=self.material_info,
                        dat_sha256=self.model.deck.digest, layers=self.preview['layers'],
                        reference_origin_mm=self.preview['reference_origin_mm'], basis=self.preview['basis'],
                        contacts=self.preview['contacts'], weld_end_s=self.grid.plan['end_s'],
                        parameter_sources=self.preview['sources'], defaults=self.preview['defaults'],
                        thermal_boundaries=self.preview['boundaries'],
                        cooling_time_s=max(0., state['time_s'] - self.grid.plan['end_s']),
                        full_weld_completed=state['status'] == 'complete' and state['time_s'] >= self.grid.plan['end_s'])
        arrays = BytesIO()
        np.savez_compressed(arrays, temperature_c=temperatures, times_s=times,
                            peak_temperature_c=self.peak,
                            positions_mm=np.array(self.preview['positions_mm']).reshape(-1, 3),
                            triangles=np.array(self.preview['triangles'], dtype=np.int64).reshape(-1, 3),
                            triangle_cells=np.array(self.preview['triangle_cells'], dtype=np.int64),
                            cells=np.array(self.preview['cells'], dtype=np.int64), active=np.array(self.active, dtype=np.int64),
                            x_m=self.grid.x, y_m=self.grid.y,
                            bottom_m=np.array([layer.bottom for layer in self.grid.layers]),
                            top_m=np.array([layer.top for layer in self.grid.layers]),
                            area_m2=np.array([layer.area for layer in self.grid.layers]),
                            mask=np.array([layer.mask for layer in self.grid.layers]))
        output = BytesIO()
        with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
            archive.writestr('parameters.json', json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False))
            archive.writestr('temperature.npz', arrays.getvalue())
            if self.strain_result is not None:
                strain = BytesIO()
                np.savez_compressed(strain, plastic_mandel=self.strain_result.plastic,
                                    equivalent=self.strain_result.equivalent, active=self.active)
                archive.writestr('inherent_strain.npz', strain.getvalue())
            if self.mechanical_result is not None:
                archive.writestr('mechanics.json', json.dumps(self.mechanical_result, ensure_ascii=False, allow_nan=False))
                structure = self.mechanical_structure
                result = self.mechanical_arrays
                mechanical = BytesIO()
                np.savez_compressed(mechanical, nodal_xy_m=structure.xy, connectivity=structure.connectivity,
                                    node_layers=structure.node_layers, layer_reference_m=structure.references,
                                    element_layers=structure.element_layers, thickness_m=structure.shell.thickness,
                                    displacement_rotation=result['q'], plastic_strain_mandel=result['plastic'],
                                    equivalent_plastic_strain=result['equivalent'],
                                    peak_temperature_K=result['peak_temperature_K'], contact_gap_m=result['contact_gap_m'],
                                    extrapolated=result['extrapolated'],
                                    temperature_extrapolated=result['temperature_extrapolated'],
                                    plastic_extrapolated=result['plastic_extrapolated'],
                                    weld_reference=result['weld_reference'])
                archive.writestr('mechanics.npz', mechanical.getvalue())
                archive.writestr('equilibrium.json', json.dumps(result['history'], allow_nan=False))
        return output.getvalue()
