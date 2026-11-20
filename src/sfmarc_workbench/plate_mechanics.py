import numpy as np
from scipy.linalg import null_space
from scipy.sparse import coo_matrix, diags
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import spsolve

from .plate_inherent import _check_constant_extension
from .plate_material import MaterialState, plane_stress_update


class EquilibriumFailure(RuntimeError):
    pass


class Mechanics:
    """MITC4、von Karman 有限挠度，支持 J2 历史更新和整体固有应变加载。"""

    def __init__(self, structure, material, initial_temperature=298.15, cancelled=None, progress=None,
                 inherent_state=None):
        self.structure = structure
        self.material = material
        self.cancelled = cancelled
        self.progress = progress
        self._stage = "结构初始化"
        material._check_temperature(initial_temperature)
        self.initial_temperature = float(initial_temperature)
        self.temperature = np.full(structure.map.shape[1], initial_temperature)
        self.state = MaterialState.zeros((len(structure.connectivity), 4, 5), initial_temperature)
        self.inherent_state = inherent_state
        self.load_fraction = 0.
        if inherent_state is not None:
            shape = (len(structure.connectivity), 4, 5)
            if not isinstance(inherent_state, MaterialState):
                raise TypeError("固有应变历史必须为 MaterialState")
            if (np.shape(inherent_state.plastic) != (*shape, 6)
                    or np.shape(inherent_state.equivalent) != shape
                    or np.shape(inherent_state.peak_temperature) != shape
                    or np.shape(inherent_state.extrapolated) not in ((), shape)):
                raise ValueError("固有应变历史必须包含每单元 4×5 个积分点")
            if (not np.isfinite(inherent_state.plastic).all()
                    or not np.isfinite(inherent_state.equivalent).all()
                    or np.any(inherent_state.equivalent < 0)
                    or not np.isfinite(inherent_state.peak_temperature).all()):
                raise ValueError("固有应变历史包含无效数值")
            self.state = MaterialState(inherent_state.plastic.copy(), inherent_state.equivalent.copy(),
                                       inherent_state.peak_temperature.copy(),
                                       np.broadcast_to(inherent_state.extrapolated, shape).copy())
            self.eigenstrain = self.state.plastic[..., [0, 1, 5]].copy()
            self.eigenstrain[..., 2] *= np.sqrt(2)
        self.q = np.zeros(structure.ndof)
        self.time_s = 0.
        self.cover = True
        self.weld_count = 0
        self.weld_reference = np.zeros(len(structure.weld_times))
        self.history = []
        self._initialized = False
        self.penalty = float(material.elastic_modulus(initial_temperature)) * float(np.mean(structure.shell.thickness)) * 100
        self.contact_k = self.penalty * structure.contact_k
        self.scale = np.tile([1., 1., 1., 1 / structure.grid.spacing, 1 / structure.grid.spacing], len(structure.xy))
        self._extension_checked = False
        elements = structure.connectivity
        adjacency = coo_matrix((np.ones(3 * len(elements)),
                                (np.repeat(elements[:, 0], 3), elements[:, 1:].ravel())),
                               shape=(len(structure.xy), len(structure.xy))).tocsr()
        count, labels = connected_components(adjacency, directed=False)
        self._components = [np.flatnonzero(labels == component) for component in range(count)]
        self._rigid_modes = np.zeros((structure.ndof, 3 * count))
        for component in range(count):
            nodes = np.flatnonzero(labels == component)
            center = structure.xy[nodes].mean(axis=0)
            self._rigid_modes[5 * nodes, 3 * component] = 1
            self._rigid_modes[5 * nodes + 1, 3 * component + 1] = 1
            self._rigid_modes[5 * nodes, 3 * component + 2] = -(structure.xy[nodes, 1] - center[1]) / structure.grid.spacing
            self._rigid_modes[5 * nodes + 1, 3 * component + 2] = (structure.xy[nodes, 0] - center[0]) / structure.grid.spacing

    def _fixed(self):
        model = self.structure
        fixed = list(model.anchors[0])
        modes = self._rigid_modes
        coupled = np.vstack((model.weld[:self.weld_count] @ modes, modes[fixed]))
        rank = np.linalg.matrix_rank(coupled)
        for anchors in model.anchors[1:]:
            # 仅保留焊接尚未约束的独立面内刚体自由度。
            for dof in anchors:
                candidate = np.vstack((coupled, modes[dof]))
                new_rank = np.linalg.matrix_rank(candidate)
                if new_rank > rank:
                    fixed.append(dof)
                    coupled, rank = candidate, new_rank
        if self.cover:
            fixed.extend(model.cover_dofs)
        return np.unique(np.asarray(fixed, dtype=int))

    def _constitutive(self, q, temperature, state):
        model, material = self.structure, self.material
        if self.inherent_state is not None:
            kin = model.shell.kinematics(q.reshape(-1, 5))
            stress = np.einsum("eij,eptj->epti", model.shell.elasticity,
                               kin.strain - self.eigenstrain * self.load_fraction)
            tangent = np.broadcast_to(model.shell.elasticity[:, None, None],
                                      (*kin.strain.shape, 3))
            return kin, stress, tangent, state, model.shell.shear_modulus[:, None, None]
        element_temperature = np.asarray(model.map @ temperature)[:, None, None]
        peak = np.maximum(state.peak_temperature, element_temperature)
        if np.any(peak > material.temperature_max) and not self._extension_checked:
            _check_constant_extension(material)
            self._extension_checked = True
        bounded = np.minimum(element_temperature, material.temperature_max)
        bounded_state = MaterialState(state.plastic, state.equivalent,
                                      np.minimum(state.peak_temperature, material.temperature_max), state.extrapolated)
        kin = model.shell.kinematics(q.reshape(-1, 5))
        strain = kin.strain.copy()
        strain[..., :2] += material.thermal_strain(self.initial_temperature)
        stress, tangent, trial, _ = plane_stress_update(material, strain, bounded, bounded_state)
        trial = MaterialState(trial.plastic, trial.equivalent, peak.copy(), trial.extrapolated)
        shear = material.elastic_modulus(bounded) / (2 * (1 + material.poisson))
        return kin, stress, tangent, trial, shear

    def _evaluate(self, q, temperature, state, external=None, tangent=True):
        model = self.structure
        kin, stress, constitutive, trial, shear = self._constitutive(q, temperature, state)
        response = model.shell.assemble(kin, stress, constitutive, shear_modulus=shear, tangent=tangent)
        residual = response.residual - model.gravity
        if external is not None:
            residual -= external
        matrix = response.tangent
        gap = model.contact @ q + model.gaps
        residual += model.contact.T @ (self.contact_k * np.minimum(gap, 0.))
        if tangent:
            active = gap <= 1e-14
            contact = model.contact[active]
            matrix += contact.T @ diags(self.contact_k[active]) @ contact
        if self.weld_count:
            bond = model.weld[:self.weld_count]
            coefficients = self.penalty * model.weld_weights[:self.weld_count]
            residual += bond.T @ (coefficients * (bond @ q - self.weld_reference[:self.weld_count]))
            if tangent:
                matrix += bond.T @ diags(coefficients) @ bond
        return residual, matrix, trial

    def _potential(self, q, temperature, state, external):
        model, material = self.structure, self.material
        if self.inherent_state is not None:
            energy = model.shell.energy(q.reshape(-1, 5), self.eigenstrain * self.load_fraction)
            return self._external_potential(q, external, energy)
        kin, stress, _, trial, shear = self._constitutive(q, temperature, state)
        bounded = np.minimum(np.asarray(model.map @ temperature)[:, None, None], material.temperature_max)
        thermal = material.thermal_strain(bounded, self.initial_temperature)
        elastic = kin.strain.copy()
        elastic[..., :2] -= thermal[..., None] + trial.plastic[..., :2]
        elastic[..., 2] -= np.sqrt(2) * trial.plastic[..., 5]
        density = .5 * np.sum(stress * elastic, axis=-1)
        peak = np.minimum(trial.peak_temperature, material.temperature_max)
        lower = state.equivalent
        # 分段线性流动曲线的精确积分构成 J2 增量耗散势。
        for boundary in np.r_[material._plastic_axis[1:], np.inf]:
            upper = np.maximum(lower, np.minimum(trial.equivalent, boundary))
            width = upper - lower
            value, slope = material._yield_and_slope(bounded, lower, peak)
            density += width * (value + .5 * slope * width)
            lower = upper
        energy = float(np.sum(density * model.shell.volume_weights))
        energy += .5 * np.sum(kin.shear ** 2 * model.shell._shear_weights(shear)[..., None])
        return self._external_potential(q, external, energy)

    def _external_potential(self, q, external, energy):
        model = self.structure
        energy -= model.gravity @ q
        if external is not None:
            energy -= external @ q
        compression = np.minimum(model.contact @ q + model.gaps, 0.)
        energy += .5 * np.sum(self.contact_k * compression ** 2)
        extension = model.weld[:self.weld_count] @ q - self.weld_reference[:self.weld_count]
        energy += .5 * self.penalty * np.sum(model.weld_weights[:self.weld_count] * extension ** 2)
        return float(energy)

    def apply_inherent(self):
        if self.inherent_state is None:
            raise ValueError("整体固有应变加载需要 MaterialState")
        self.weld_count = len(self.structure.weld_times)
        self.weld_reference[:] = 0.
        if not self._initialized:
            self.load_fraction = 0.
            self._initialize()
        self._stage = "整体固有应变加载"
        increment = .1
        info = None
        while self.load_fraction < 1.:
            previous = self.load_fraction
            self.load_fraction = min(1., previous + increment)
            try:
                info = self._solve(self.temperature)
            except EquilibriumFailure:
                self.load_fraction = previous
                increment /= 2
                if increment < 1 / 4096:
                    raise
                continue
            info.update(time_s=self.time_s, stage="inherent_loading", load_fraction=self.load_fraction)
            self.history.append(info.copy())
            increment = min(.25, increment * 2)
        return info if info is not None else self._solve(self.temperature)

    def _solve(self, temperature, external=None):
        fixed = self._fixed()
        free = np.setdiff1d(np.arange(self.structure.ndof), fixed)
        q = self.q.copy()
        q[fixed] = 0
        scale = self.scale[free]
        for iteration in range(80):
            if self.cancelled is not None and self.cancelled():
                raise InterruptedError("结构计算已取消")
            residual, matrix, trial = self._evaluate(q, temperature, self.state, external)
            gradient = residual[free] * scale
            error = float(np.max(np.abs(gradient), initial=0.))
            if self.progress is not None:
                self.progress(f"{self._stage}，Newton {iteration}，残差 {error:.6g} N")
            if error < 1e-7:
                self.q, self.state = q, trial
                return dict(iterations=iteration, residual_N=error, converged=True,
                            cover=self.cover, active_weld_rows=self.weld_count)
            scaled = matrix[free][:, free].multiply(scale[:, None]).multiply(scale[None, :]).tocsc()
            contact = self.structure.contact[:, free].multiply(scale[None, :]).tocsr()
            gap = self.structure.contact @ q + self.structure.gaps
            active = gap <= 1e-14
            base_matrix = scaled - contact[active].T @ diags(self.contact_k[active]) @ contact[active]
            base_gradient = gradient - contact.T @ (self.contact_k * np.minimum(gap, 0.))
            direction = np.zeros(len(free))
            def quadratic(value):
                compression = np.minimum(gap + contact @ value, 0.)
                return .5 * value @ (base_matrix @ value) + base_gradient @ value + .5 * np.sum(self.contact_k * compression ** 2)
            # Newton 方向内部求解单边接触的分段二次问题。
            for contact_iteration in range(40):
                if self.cancelled is not None and self.cancelled():
                    raise InterruptedError("结构计算已取消")
                scaled = (base_matrix + contact[active].T @ diags(self.contact_k[active]) @ contact[active]).tocsc()
                linear_gradient = base_gradient + contact[active].T @ (self.contact_k[active] * gap[active])
                inverse = 1 / np.sqrt(np.maximum(np.abs(scaled.diagonal()), 1.))
                conditioned = scaled.multiply(inverse[:, None]).multiply(inverse[None, :]).tocsc()
                target_direction = inverse * spsolve(conditioned, -linear_gradient * inverse, permc_spec="MMD_AT_PLUS_A")
                previous_energy = quadratic(direction)
                for contact_search in range(24):
                    candidate_direction = direction + 2. ** -contact_search * (target_direction - direction)
                    if quadratic(candidate_direction) <= previous_energy:
                        break
                direction = candidate_direction
                predicted = gap + contact @ direction
                next_active = predicted < 0.
                if contact_search == 0 and np.array_equal(active, next_active):
                    break
                active = next_active
            inverse = 1 / np.sqrt(np.maximum(np.abs(scaled.diagonal()), 1.))
            conditioned = scaled.multiply(inverse[:, None]).multiply(inverse[None, :]).tocsc()
            step = np.zeros_like(q)
            energy = self._potential(q, temperature, self.state, external)
            accepted = False
            contact_direction = direction
            for regularization in (0., 1e-8, 1e-6, 1e-4, .01, 1., 100.):
                system = conditioned + diags(np.full(len(free), regularization))
                direction = contact_direction if regularization == 0 else inverse * spsolve(system, -gradient * inverse, permc_spec="MMD_AT_PLUS_A")
                slope = float(gradient @ direction)
                if not np.isfinite(direction).all() or slope >= 0:
                    continue
                step[free] = direction * scale
                maximum_step = np.max(np.abs(step / self.scale)) / self.structure.grid.spacing
                initial_alpha = min(1., .05 / maximum_step)
                for search in range(18):
                    alpha = initial_alpha * 2. ** -search
                    candidate = q + alpha * step
                    candidate_energy = self._potential(candidate, temperature, self.state, external)
                    if candidate_energy <= energy + 1e-4 * alpha * slope:
                        q, accepted = candidate, True
                        break
                if accepted:
                    break
            if not accepted:
                raise EquilibriumFailure(f"结构增量势能线搜索未收敛：residual_N={error}")
        raise EquilibriumFailure(f"结构平衡达到迭代上限：residual_N={error}")

    def _temperature_step(self, temperature, depth=0):
        previous_temperature = self.temperature.copy()
        difference = float(np.max(np.abs(temperature - previous_temperature)))
        if self.progress is not None:
            self.progress(f"{self._stage}，温度分步深度 {depth}，最大温差 {difference:.6g} K")
        try:
            info = self._solve(temperature)
        except EquilibriumFailure as error:
            if depth >= 10 or difference < 1e-5:
                raise EquilibriumFailure(f"温度分步深度 {depth}，最大温差 {difference:.6g} K；{error}") from error
            if self.progress is not None:
                self.progress(f"{self._stage}，温度分步深度 {depth} 未收敛，继续二分：{error}")
            self._temperature_step((temperature + previous_temperature) / 2, depth + 1)
            info = self._temperature_step(temperature, depth + 1)
        self.temperature = temperature.copy()
        return info

    def _activate(self, time_s):
        count = int(np.searchsorted(self.structure.weld_times, time_s + 1e-12, side="right"))
        self.weld_reference[self.weld_count:count] = self.structure.weld[self.weld_count:count] @ self.q
        self.weld_count = count

    def advance(self, time_s, temperature_active_K):
        if self.inherent_state is not None:
            raise ValueError("固有应变模式通过 apply_inherent 整体加载")
        temperature = np.asarray(temperature_active_K, dtype=float)
        if (temperature.shape != self.temperature.shape or not np.isfinite(temperature).all()
                or np.any(temperature < self.material.temperature_min)):
            raise ValueError("活动单元温度尺寸或者温度范围无效")
        if not np.isfinite(time_s) or time_s < self.time_s:
            raise ValueError("结构时间必须为有限递增数值")
        self._initialize()
        self._stage = f"结构热步 {time_s:.6g} s"
        initial_time, initial_temperature = self.time_s, self.temperature.copy()
        intervals = max(1, int(np.ceil(np.max(np.abs(temperature - initial_temperature)) / 25.)))
        times = np.linspace(initial_time, time_s, intervals + 1)[1:]
        events = self.structure.weld_times
        times = np.unique(np.r_[times, events[(events > initial_time) & (events <= time_s)]])
        self._activate(initial_time)
        for time in times:
            fraction = (time - initial_time) / (time_s - initial_time) if time_s > initial_time else 1.
            info = self._temperature_step(initial_temperature + fraction * (temperature - initial_temperature))
            self.time_s = float(time)
            self._activate(time)
            info.update(time_s=self.time_s, active_weld_rows=self.weld_count)
            self.history.append(info.copy())
        return info

    def _initialize(self):
        if not self._initialized:
            model = self.structure
            fixed = self._fixed()
            # 现有支撑约束下，组件沿重力功方向运动到首个实际接触。
            modes = np.zeros((model.ndof, 3 * len(self._components)))
            for index, nodes in enumerate(self._components):
                xy = (model.xy[nodes] - model.xy[nodes].mean(axis=0)) / model.grid.spacing
                modes[5 * nodes + 2, 3 * index] = 1
                modes[5 * nodes + 2, 3 * index + 1:3 * index + 3] = xy
                modes[5 * nodes + 4, 3 * index + 1] = -1 / model.grid.spacing
                modes[5 * nodes + 3, 3 * index + 2] = 1 / model.grid.spacing
            contact_modes = model.contact @ modes
            for _ in range(modes.shape[1]):
                gap = model.contact @ self.q + model.gaps
                constraints = np.vstack((modes[fixed], contact_modes[gap <= 1e-14]))
                if self.inherent_state is not None:
                    constraints = np.vstack((constraints, model.weld @ modes))
                basis = null_space(constraints, rcond=1e-10)
                direction = modes @ (basis @ (basis.T @ (modes.T @ model.gravity)))
                if np.max(np.abs(direction), initial=0.) < 1e-14:
                    break
                response = model.contact @ direction
                selected = response < -1e-12 * np.max(np.abs(response), initial=0.)
                if not np.any(selected):
                    raise EquilibriumFailure("重力方向不存在可承载的接触支撑")
                distance = float(np.min(np.maximum(gap[selected], 0.) / -response[selected]))
                self.q += distance * direction
            fraction, increment = 0., .1
            while fraction < 1.:
                target = min(1., fraction + increment)
                try:
                    info = self._solve(self.temperature, external=(target - 1) * self.structure.gravity)
                except EquilibriumFailure:
                    increment /= 2
                    if increment < 1 / 4096:
                        raise
                    continue
                fraction = target
                info.update(time_s=self.time_s, stage="initial_equilibrium", gravity_fraction=fraction)
                self.history.append(info.copy())
                increment = min(.25, increment * 2)
            self._initialized = True

    def release(self):
        self._initialize()
        self._stage = "Cover 卸载"
        if not self.cover:
            return self._solve(self.temperature)
        reaction = np.zeros_like(self.q)
        residual = self._evaluate(self.q, self.temperature, self.state, tangent=False)[0]
        reaction[self.structure.cover_dofs] = residual[self.structure.cover_dofs]
        self.cover = False
        fraction, increment = 0., .25
        while fraction < 1.:
            target = min(1., fraction + increment)
            try:
                info = self._solve(self.temperature, external=(1 - target) * reaction)
            except EquilibriumFailure:
                increment /= 2
                if increment < 1 / 4096:
                    raise
                continue
            fraction = target
            info.update(time_s=self.time_s, stage="cover_unloading", release_fraction=fraction)
            self.history.append(info.copy())
            increment = min(.25, increment * 2)
        info.update(time_s=self.time_s, stage="released")
        self.history.append(info.copy())
        return info

    def result(self):
        residual = self._evaluate(self.q, self.temperature, self.state, tangent=False)[0]
        free = np.setdiff1d(np.arange(self.structure.ndof), self._fixed())
        return dict(q=self.q.reshape(-1, 5).copy(), plastic=self.state.plastic.copy(),
                    equivalent=self.state.equivalent.copy(), peak_temperature_K=self.state.peak_temperature.copy(),
                    extrapolated=(np.asarray(self.state.extrapolated) | (self.state.peak_temperature > self.material.temperature_max)),
                    plastic_extrapolated=np.asarray(self.state.extrapolated).copy(),
                    temperature_extrapolated=self.state.peak_temperature > self.material.temperature_max,
                    temperature_K=self.temperature.copy(),
                    time_s=self.time_s, contact_gap_m=self.structure.contact @ self.q + self.structure.gaps,
                    weld_reference=self.weld_reference.copy(), weld_count=self.weld_count,
                    residual_N=float(np.max(np.abs(residual[free] * self.scale[free]), initial=0.)),
                    cover=self.cover, history=list(self.history),
                    load_fraction=self.load_fraction,
                    formulation=("MITC4 von Karman finite deflection, elastic inherent strain"
                                 if self.inherent_state is not None else
                                 "MITC4 von Karman finite deflection, small strain plane stress J2"))

    payload = result
