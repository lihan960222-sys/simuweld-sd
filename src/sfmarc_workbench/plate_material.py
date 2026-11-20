from dataclasses import dataclass
from io import StringIO
import xml.etree.ElementTree as ET

import numpy as np


_UNITS = {
    "K": (1.0, 0.0), "°C": (1.0, 273.15),
    "Pa": (1.0, 0.0), "MPa": (1e6, 0.0), "GPa": (1e9, 0.0),
    "kg/m³": (1.0, 0.0), "-": (1.0, 0.0), "1/K": (1.0, 0.0),
    "W/(m·K)": (1.0, 0.0), "J/(m·s·K)": (1.0, 0.0), "J/(kg·K)": (1.0, 0.0),
    "kJ/(kg·K)": (1000.0, 0.0), "J/kg": (1.0, 0.0),
    "kJ/kg": (1000.0, 0.0), "1/s": (1.0, 0.0),
}
_IDENTITY = np.array([1., 1., 1., 0., 0., 0.])
_VOLUME = np.outer(_IDENTITY, _IDENTITY)
_DEVIATOR = np.eye(6) - _VOLUME / 3


def _convert(values, symbol, allowed):
    if symbol not in allowed:
        raise ValueError(f"不支持的 XMT 单位：{symbol}；要求 {allowed}")
    scale, offset = _UNITS[symbol]
    result = np.asarray(values, dtype=float) * scale + offset
    if not np.isfinite(result).all():
        raise ValueError("XMT 包含非有限数值")
    return result


def _scalar(node, allowed):
    if node is None:
        raise ValueError("XMT 缺少必需的材料字段")
    return float(_convert(float(node.text), node.get("unit_symbol"), allowed))


def _table(node, x_units, y_units):
    if node is None:
        raise ValueError("XMT 缺少必需的材料表格")
    columns = node.findall("columndata")
    rows = node.findall("row")
    if len(columns) != 2 or len(rows) < 2 or int(node.get("columns")) != 2:
        raise ValueError("材料表格必须包含两列和至少两行")
    data = np.loadtxt(StringIO("\n".join(row.text for row in rows)), delimiter=";", ndmin=2)
    if data.shape != (int(node.get("rows")), 2):
        raise ValueError("材料表格尺寸与声明不一致")
    data[:, 0] = _convert(data[:, 0], columns[0].get("unit_symbol"), x_units)
    data[:, 1] = _convert(data[:, 1], columns[1].get("unit_symbol"), y_units)
    if np.any(np.diff(data[:, 0]) <= 0):
        raise ValueError("材料表格横坐标必须严格递增")
    return data


def _primitive(table, values):
    x, y = table.T
    integral = np.concatenate(([0.], np.cumsum(np.diff(x) * (y[:-1] + y[1:]) / 2)))
    index = np.clip(np.searchsorted(x, values, side="right") - 1, 0, len(x) - 2)
    delta = values - x[index]
    slope = (y[index + 1] - y[index]) / (x[index + 1] - x[index])
    return integral[index] + delta * (y[index] + slope * delta / 2)


@dataclass
class Material:
    density: float
    poisson: float
    temperature_min: float
    temperature_max: float
    solidus: float
    liquidus: float
    latent_heat: float
    strain_rate: float
    source_comment: str
    _properties: dict
    _peaks: np.ndarray
    _temperatures: np.ndarray
    _plastic_axis: np.ndarray
    _flow_values: np.ndarray
    _enthalpy_nodes: np.ndarray
    _enthalpy_values: np.ndarray
    _capacity_start: np.ndarray
    _capacity_slope: np.ndarray
    thermal_extrapolation: bool = False

    @classmethod
    def from_xmt(cls, content: bytes):
        root = ET.fromstring(content)
        if root.tag != "sfMaterialData":
            raise ValueError("要求 sfMaterialData XMT 材料")
        minimum = _scalar(root.find("min_temperature"), ("K", "°C"))
        maximum = _scalar(root.find("max_temperature"), ("K", "°C"))
        density = _scalar(root.find("density"), ("kg/m³",))
        poisson = _scalar(root.find("transverse_contraction"), ("-",))
        if density <= 0 or not -1 < poisson < 0.5 or minimum >= maximum:
            raise ValueError("材料密度、Poisson's ratio 或温度范围无效")
        for name in ("density", "transverse_contraction"):
            if root.findtext(f"{name}_user_selection_datatype") != "0":
                raise ValueError(f"当前计算要求恒定 {name}")
        properties = {}
        for name, units in (
            ("youngs_modulus", ("Pa", "MPa", "GPa")),
            ("thermal_expansion", ("1/K",)),
            ("thermal_conductivity", ("W/(m·K)", "J/(m·s·K)")),
            ("specific_heat_capacity", ("J/(kg·K)", "kJ/(kg·K)")),
        ):
            if root.findtext(f"{name}_user_selection_datatype") != "1":
                raise ValueError(f"当前计算要求 {name} 温度表格")
            pairs = root.findall(f"{name}_table_peak_temperature_pairs/table_peak_temperature_pair")
            if len(pairs) > 1:
                raise ValueError(f"当前计算未实现 {name} 的历史最高温度相关表格")
            table = _table(root.find(f"{name}_table/sfDataTable"), ("K", "°C"), units)
            if table[0, 0] > minimum or table[-1, 0] < maximum:
                raise ValueError(f"{name} 表格未覆盖材料温度范围")
            if np.any(table[:, 1] < 0) or (name != "thermal_expansion" and np.any(table[:, 1] == 0)):
                raise ValueError(f"{name} 表格包含无效物性")
            properties[name] = table
        solidus = _scalar(root.find("solidus_temperature"), ("K", "°C"))
        liquidus = _scalar(root.find("melting_point"), ("K", "°C"))
        latent = _scalar(root.find("latent_heat"), ("J/kg", "kJ/kg"))
        if not minimum < solidus < liquidus < maximum or latent < 0:
            raise ValueError("固相线、液相线或潜热无效")
        flow = root.find("flow_curves_table_approach")
        if root.findtext("flow_curves_used_approach") != "0" or flow is None or flow.findtext("usePeakTemperatures") != "true":
            raise ValueError("要求历史最高温度相关流动曲线")
        rates = [_scalar(node, ("1/s",)) for node in flow.findall("strain_rates/strain_rate")]
        if len(rates) != 1 or rates[0] <= 0:
            raise ValueError("速率无关计算要求单组正值应变率数据")
        peaks = np.array([_scalar(node, ("K", "°C")) for node in flow.findall("peak_temperatures/peak_temperature")])
        temperatures = np.array([_scalar(node, ("K", "°C")) for node in flow.findall("temperatures/temperature")])
        if len(peaks) < 2 or len(temperatures) < 2 or np.any(np.diff(peaks) <= 0) or np.any(np.diff(temperatures) <= 0):
            raise ValueError("流动曲线温度坐标必须严格递增且包含至少两个节点")
        if temperatures[0] > minimum or temperatures[-1] < maximum:
            raise ValueError("流动曲线未覆盖材料温度范围")
        curves = {}
        plastic_axis = None
        for curve in flow.findall("flow_curve_table"):
            peak = _scalar(curve.find("fc_peak_temperature"), ("K", "°C"))
            temperature = _scalar(curve.find("fc_temperature"), ("K", "°C"))
            rate = _scalar(curve.find("fc_strainrate"), ("1/s",))
            if peak not in peaks or temperature not in temperatures or rate != rates[0] or (peak, temperature) in curves:
                raise ValueError("流动曲线坐标重复或与声明不一致")
            table = _table(curve.find("sfDataTable"), ("-",), ("Pa", "MPa", "GPa"))
            if table[0, 0] != 0 or np.any(table[:, 1] <= 0) or np.any(np.diff(table[:, 1]) < 0):
                raise ValueError("要求从零塑性应变开始的正值各向同性硬化曲线")
            if plastic_axis is not None and not np.array_equal(plastic_axis, table[:, 0]):
                raise ValueError("流动曲线必须使用共同塑性应变坐标")
            plastic_axis = table[:, 0]
            curves[peak, temperature] = table[:, 1]
        if len(curves) != len(peaks) * len(temperatures):
            raise ValueError("流动曲线温度组合不完整")
        values = np.array([[curves[peak, temperature] for temperature in temperatures] for peak in peaks])
        cp = properties["specific_heat_capacity"]
        knots = np.unique(np.concatenate(([minimum, maximum, solidus, liquidus], cp[:, 0])))
        knots = knots[(knots >= minimum) & (knots <= maximum)]
        sensible = np.interp(knots, cp[:, 0], cp[:, 1])
        widths = np.diff(knots)
        midpoints = (knots[:-1] + knots[1:]) / 2
        # 固液区间采用均匀液相比例，焓与表观比热保持一致。
        latent_capacity = latent / (liquidus - solidus) * ((midpoints > solidus) & (midpoints < liquidus))
        capacity_start = sensible[:-1] + latent_capacity
        capacity_slope = np.diff(sensible) / widths
        enthalpy = np.concatenate(([0.], np.cumsum(widths * (capacity_start + capacity_slope * widths / 2))))
        return cls(density, poisson, minimum, maximum, solidus, liquidus, latent, rates[0],
                   root.findtext("comment", ""), properties, peaks, temperatures, plastic_axis,
                   values, knots, enthalpy, capacity_start, capacity_slope)

    @property
    def thermal_temperature_max(self):
        return np.inf if self.thermal_extrapolation else self.temperature_max

    def _check_temperature(self, values, thermal=False):
        values = np.asarray(values, dtype=float)
        maximum = self.thermal_temperature_max if thermal else self.temperature_max
        if not np.isfinite(values).all() or np.any(values < self.temperature_min) or np.any(values > maximum):
            raise ValueError(f"温度要求在 {self.temperature_min}–{maximum} K 范围内")
        return values

    def _property(self, name, temperature):
        temperature = self._check_temperature(temperature, thermal=name in ("thermal_conductivity", "specific_heat_capacity"))
        table = self._properties[name]
        return np.interp(temperature, table[:, 0], table[:, 1])

    def conductivity(self, T_K):
        return self._property("thermal_conductivity", T_K)

    def elastic_modulus(self, T_K):
        return self._property("youngs_modulus", T_K)

    def capacity(self, T_K):
        temperature = self._check_temperature(T_K, thermal=True)
        sensible = self._property("specific_heat_capacity", temperature)
        return sensible + self.latent_heat / (self.liquidus - self.solidus) * ((temperature >= self.solidus) & (temperature < self.liquidus))

    def thermal_strain(self, T_K, reference=298.15):
        temperature = self._check_temperature(T_K)
        reference = self._check_temperature(reference)
        return _primitive(self._properties["thermal_expansion"], temperature) - _primitive(self._properties["thermal_expansion"], reference)

    def enthalpy(self, T_K):
        temperature = self._check_temperature(T_K, thermal=True)
        excess = np.maximum(temperature - self.temperature_max, 0.)
        temperature = np.minimum(temperature, self.temperature_max)
        index = np.clip(np.searchsorted(self._enthalpy_nodes, temperature, side="right") - 1, 0, len(self._enthalpy_nodes) - 2)
        delta = temperature - self._enthalpy_nodes[index]
        return (self._enthalpy_values[index] + delta * (self._capacity_start[index] + self._capacity_slope[index] * delta / 2)
                + excess * self.capacity(self.temperature_max))

    def temperature(self, H_J_per_kg):
        enthalpy = np.asarray(H_J_per_kg, dtype=float)
        if not np.isfinite(enthalpy).all() or np.any(enthalpy < 0) or (not self.thermal_extrapolation and np.any(enthalpy > self._enthalpy_values[-1])):
            raise ValueError("焓超出材料温度范围对应的有效区间")
        # 热学外推保持末端比热，增加的能量全部计入焓。
        excess = np.maximum(enthalpy - self._enthalpy_values[-1], 0.)
        enthalpy = np.minimum(enthalpy, self._enthalpy_values[-1])
        index = np.clip(np.searchsorted(self._enthalpy_values, enthalpy, side="right") - 1, 0, len(self._enthalpy_values) - 2)
        delta = enthalpy - self._enthalpy_values[index]
        c0 = self._capacity_start[index]
        return (self._enthalpy_nodes[index] + 2 * delta / (c0 + np.sqrt(c0 ** 2 + 2 * self._capacity_slope[index] * delta))
                + excess / self.capacity(self.temperature_max))

    @property
    def plastic_limit(self):
        return float(self._plastic_axis[-1])

    def _yield_and_slope(self, temperature, plastic, peak):
        temperature, plastic, peak = np.broadcast_arrays(self._check_temperature(temperature), np.asarray(plastic, dtype=float), self._check_temperature(peak))
        if not np.isfinite(plastic).all() or np.any(plastic < 0):
            raise ValueError("累计等效塑性应变必须为有限非负数值")
        peak = np.clip(peak, self._peaks[0], self._peaks[-1])
        coordinates = (peak, temperature, plastic)
        axes = (self._peaks, self._temperatures, self._plastic_axis)
        indices = [np.clip(np.searchsorted(axis, value, side="right") - 1, 0, len(axis) - 2) for axis, value in zip(axes, coordinates)]
        weights = [(value - axis[index]) / (axis[index + 1] - axis[index]) for axis, value, index in zip(axes, coordinates, indices)]
        result = np.zeros(temperature.shape)
        slope = np.zeros(temperature.shape)
        ip, it, ie = indices
        wp, wt, we = weights
        for dp in (0, 1):
            for dt in (0, 1):
                weight = (wp if dp else 1 - wp) * (wt if dt else 1 - wt)
                lower = self._flow_values[ip + dp, it + dt, ie]
                upper = self._flow_values[ip + dp, it + dt, ie + 1]
                result += weight * (lower + we * (upper - lower))
                slope += weight * (upper - lower) / (self._plastic_axis[ie + 1] - self._plastic_axis[ie])
        return result, slope

    def yield_stress(self, T_K, plastic_equivalent, peak_K):
        return self._yield_and_slope(T_K, plastic_equivalent, peak_K)[0]


@dataclass(frozen=True)
class MaterialState:
    plastic: np.ndarray
    equivalent: np.ndarray
    peak_temperature: np.ndarray
    extrapolated: np.ndarray | bool = False

    @classmethod
    def zeros(cls, shape=(), temperature=298.15):
        return cls(np.zeros((*shape, 6)), np.zeros(shape), np.full(shape, temperature, dtype=float), np.zeros(shape, dtype=bool))


def _return_3d(material, strain, temperature, previous, equivalent, peak, shear, bulk):
    trial = 2 * shear[:, None] * (strain - previous)
    trial += (bulk - 2 * shear / 3)[:, None] * np.sum((strain - previous)[:, :3], axis=1)[:, None] * _IDENTITY
    deviator = trial - np.mean(trial[:, :3], axis=1)[:, None] * _IDENTITY
    q = np.sqrt(1.5 * np.sum(deviator ** 2, axis=1))
    initial_yield = material.yield_stress(temperature, equivalent, peak)
    active = q > initial_yield
    gamma = np.zeros(len(q))
    lower = np.zeros(len(q))
    upper = q / (3 * shear)
    for _ in range(60):
        yield_value, hardening = material._yield_and_slope(temperature, equivalent + gamma, peak)
        residual = q - 3 * shear * gamma - yield_value
        unconverged = active & (np.abs(residual) > 1e-6 + 1e-12 * q)
        if not np.any(unconverged):
            break
        lower = np.where(unconverged & (residual > 0), gamma, lower)
        upper = np.where(unconverged & (residual < 0), gamma, upper)
        candidate = gamma + residual / (3 * shear + hardening)
        candidate = np.where((candidate < lower) | (candidate > upper), (lower + upper) / 2, candidate)
        gamma = np.where(unconverged, candidate, gamma)
    else:
        raise RuntimeError("J2 局部硬化一致性方程未收敛")
    normal = np.divide(1.5 * deviator, q[:, None], out=np.zeros_like(deviator), where=q[:, None] > 0)
    plastic = previous + gamma[:, None] * normal
    stress = trial - (2 * shear * gamma)[:, None] * normal
    ratio = np.divide(gamma, q, out=np.zeros_like(gamma), where=q > 0)
    tangent = bulk[:, None, None] * _VOLUME + (2 * shear * (1 - 3 * shear * ratio))[:, None, None] * _DEVIATOR
    correction = np.where(active, 4 * shear ** 2 * (ratio - 1 / (3 * shear + hardening)), 0)
    tangent += correction[:, None, None] * normal[:, :, None] * normal[:, None, :]
    return stress, tangent, plastic, equivalent + gamma


def plane_stress_update(material, strain, totalTemp, state):
    strain = np.asarray(strain, dtype=float)
    if strain.ndim < 1 or strain.shape[-1] != 3 or not np.isfinite(strain).all():
        raise ValueError("面内总应变必须为有限数值且末维长度为 3")
    shape = strain.shape[:-1]
    temperature = np.broadcast_to(material._check_temperature(totalTemp), shape).reshape(-1)
    previous = np.broadcast_to(np.asarray(state.plastic, dtype=float), (*shape, 6)).reshape(-1, 6)
    equivalent = np.broadcast_to(np.asarray(state.equivalent, dtype=float), shape).reshape(-1)
    old_peak = np.broadcast_to(material._check_temperature(state.peak_temperature), shape).reshape(-1)
    if not np.isfinite(previous).all() or not np.isfinite(equivalent).all() or np.any(equivalent < 0):
        raise ValueError("材料历史包含无效塑性应变")
    if np.any(np.abs(np.sum(previous[:, :3], axis=1)) > 1e-10):
        raise ValueError("J2 塑性应变要求体积不变")
    peak = np.maximum(old_peak, temperature)
    young = material.elastic_modulus(temperature)
    shear = young / (2 * (1 + material.poisson))
    bulk = young / (3 * (1 - 2 * material.poisson))
    thermal = material.thermal_strain(temperature)
    inplane = strain.reshape(-1, 3)
    full = np.zeros((len(temperature), 6))
    full[:, :2] = inplane[:, :2] - thermal[:, None]
    full[:, 3:5] = previous[:, 3:5]
    full[:, 5] = inplane[:, 2] / np.sqrt(2)
    full[:, 2] = previous[:, 2] - material.poisson / (1 - material.poisson) * np.sum(full[:, :2] - previous[:, :2], axis=1)
    for _ in range(50):
        stress, tangent, plastic, updated_equivalent = _return_3d(material, full, temperature, previous, equivalent, peak, shear, bulk)
        unconverged = np.abs(stress[:, 2]) > 1e-6 + 1e-11 * np.max(np.abs(stress), axis=1)
        if not np.any(unconverged):
            break
        full[:, 2] -= np.where(unconverged, stress[:, 2] / tangent[:, 2, 2], 0)
    else:
        raise RuntimeError("平面应力局部 Newton 未收敛；材料历史未提交")
    selected = np.array([0, 1, 5])
    # 消去厚度应变后，将 Mandel 剪切分量转换为工程剪切分量。
    condensed = tangent[:, selected[:, None], selected] - tangent[:, selected, 2, None] * tangent[:, None, 2, selected] / tangent[:, 2, 2, None, None]
    scaling = np.array([1., 1., 1 / np.sqrt(2)])
    output_stress = (stress[:, selected] * scaling).reshape((*shape, 3))
    output_tangent = (condensed * scaling[None, :, None] * scaling[None, None, :]).reshape((*shape, 3, 3))
    extrapolated = np.broadcast_to(state.extrapolated, shape) | (updated_equivalent.reshape(shape) > material.plastic_limit)
    output_state = MaterialState(plastic.reshape((*shape, 6)).copy(), updated_equivalent.reshape(shape).copy(), peak.reshape(shape).copy(), extrapolated.copy())
    return output_stress, output_tangent, output_state, (full[:, 2] + thermal).reshape(shape)
