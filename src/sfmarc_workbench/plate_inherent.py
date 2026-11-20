import numpy as np

from .plate_material import MaterialState


def _check_constant_extension(material):
    for name in ("youngs_modulus", "thermal_expansion"):
        table = material._properties[name]
        knots = np.r_[material.liquidus, table[(table[:, 0] > material.liquidus) & (table[:, 0] < material.temperature_max), 0], material.temperature_max]
        values = np.interp(knots, table[:, 0], table[:, 1])
        expected = 0. if name == "thermal_expansion" else values[-1]
        if not np.allclose(values, expected, rtol=1e-13, atol=1e-15):
            raise ValueError(f"材料高温延续要求液相区间 {name} 保持常值，thermal_expansion 必须为零")
    knots = np.r_[material.liquidus, material._temperatures[material._temperatures > material.liquidus]]
    values = material.yield_stress(knots[:, None, None], material._plastic_axis[None, None, :], material._peaks[None, :, None])
    if not np.allclose(values, values[-1:], rtol=1e-13, atol=1e-6):
        raise ValueError("材料高温延续要求各最高温度与塑性坐标的液相流动应力保持常值")


def constrained_thermal_update(material, totalTemp, state, *, allow_constant_extension=False, reference_temperature=298.15):
    temperature, equivalent, old_peak = np.broadcast_arrays(
        np.asarray(totalTemp, dtype=float), np.asarray(state.equivalent, dtype=float),
        np.asarray(state.peak_temperature, dtype=float),
    )
    shape = temperature.shape
    previous = np.broadcast_to(np.asarray(state.plastic, dtype=float), (*shape, 6))
    if not np.isfinite(temperature).all() or not np.isfinite(old_peak).all() or np.any(temperature < material.temperature_min) or np.any(old_peak < material.temperature_min):
        raise ValueError("温度和历史最高温度必须为材料下限以上的有限数值")
    if not np.isfinite(previous).all() or not np.isfinite(equivalent).all() or np.any(equivalent < 0):
        raise ValueError("材料历史包含无效塑性应变")
    p = previous[..., 0]
    if (np.any(np.abs(previous[..., 1] - p) > 1e-12)
            or np.any(np.abs(previous[..., 2] + 2 * p) > 1e-12)
            or np.any(np.abs(previous[..., 3:]) > 1e-12)
            or np.any(2 * np.abs(p) > equivalent + 1e-10)):
        raise ValueError("解析受约束热循环要求等双向塑性历史及一致的累计等效塑性应变")
    peak = np.maximum(old_peak, temperature)
    if np.any(peak > material.temperature_max):
        if not allow_constant_extension:
            raise ValueError("温度超出材料范围；必须验证并显式启用高温常值延续")
        _check_constant_extension(material)
    mechanical_temperature = np.minimum(temperature, material.temperature_max)
    mechanical_peak = np.minimum(peak, material.temperature_max)
    young = material.elastic_modulus(mechanical_temperature)
    thermal = material.thermal_strain(mechanical_temperature, reference_temperature)
    modulus = young / (1 - material.poisson)
    trial = modulus * (-thermal - p)
    magnitude = np.abs(trial)
    yield_value = material.yield_stress(mechanical_temperature, equivalent, mechanical_peak)
    active = magnitude > yield_value
    gamma = np.zeros(shape)
    lower = np.zeros(shape)
    upper = 2 * magnitude / modulus
    # 等双向平面应力下，塑性面内增量为 sign(stress) * delta_equivalent / 2。
    for _ in range(60):
        yield_value, hardening = material._yield_and_slope(mechanical_temperature, equivalent + gamma, mechanical_peak)
        residual = magnitude - modulus * gamma / 2 - yield_value
        unconverged = active & (np.abs(residual) > 1e-6 + 1e-12 * magnitude)
        if not np.any(unconverged):
            break
        lower = np.where(unconverged & (residual > 0), gamma, lower)
        upper = np.where(unconverged & (residual < 0), gamma, upper)
        candidate = gamma + residual / (modulus / 2 + hardening)
        candidate = np.where((candidate < lower) | (candidate > upper), (lower + upper) / 2, candidate)
        gamma = np.where(unconverged, candidate, gamma)
    else:
        raise RuntimeError("等双向 J2 塑性一致性方程未收敛")
    updated_p = p + np.sign(trial) * gamma / 2
    plastic = np.zeros((*shape, 6))
    plastic[..., 0] = updated_p
    plastic[..., 1] = updated_p
    plastic[..., 2] = -2 * updated_p
    updated_equivalent = equivalent + gamma
    extrapolated = np.broadcast_to(state.extrapolated, shape) | (updated_equivalent > material.plastic_limit)
    updated = MaterialState(plastic, updated_equivalent.copy(), peak.copy(), extrapolated.copy())
    stress = np.zeros((*shape, 3))
    stress[..., 0] = modulus * (-thermal - updated_p)
    stress[..., 1] = stress[..., 0]
    ezz = thermal - 2 * updated_p - 2 * material.poisson * stress[..., 0] / young
    return stress, updated, ezz


def replicate_thickness_state(state, count=5):
    if not isinstance(count, int) or count < 1:
        raise ValueError("厚度积分点数量必须为正整数")
    shape = np.asarray(state.equivalent).shape
    plastic = np.repeat(np.broadcast_to(state.plastic, (*shape, 6))[..., None, :], count, axis=-2)
    equivalent = np.repeat(np.asarray(state.equivalent)[..., None], count, axis=-1)
    peak = np.repeat(np.broadcast_to(state.peak_temperature, shape)[..., None], count, axis=-1)
    extrapolated = np.repeat(np.broadcast_to(state.extrapolated, shape)[..., None], count, axis=-1)
    return MaterialState(plastic, equivalent, peak, extrapolated)
