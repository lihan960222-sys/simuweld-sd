from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).with_name('dependencies')))
import pyuff


SIGNS = np.asarray([[-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1],
                    [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1]], float)
GAUSS = SIGNS[[0, 1, 3, 2, 4, 5, 7, 6]] / np.sqrt(3)
SHAPE = np.prod(1 + GAUSS[:, None, :] * SIGNS[None, :, :], axis=2) / 8
DERIVATIVES = np.empty((8, 8, 3))
for axis in range(3):
    other = [index for index in range(3) if index != axis]
    DERIVATIVES[:, :, axis] = SIGNS[None, :, axis] * np.prod(
        1 + GAUSS[:, None, other] * SIGNS[None, :, other], axis=2) / 8


def strain_tensor(values):
    tensor = np.zeros((*values.shape[:-1], 3, 3))
    tensor[..., 0, 0], tensor[..., 1, 1], tensor[..., 2, 2] = values[..., 0], values[..., 2], values[..., 5]
    for index, first, second in ((1, 0, 1), (3, 0, 2), (4, 1, 2)):
        tensor[..., first, second] = tensor[..., second, first] = values[..., index] / 2
    return tensor


def read_component(path):
    reader = pyuff.UFF(str(path))
    kinds = reader.get_set_types().tolist()
    if kinds != [164, 2411, 2412, 2414, 2414, 2414, 2414, 2414, 2414]:
        raise ValueError('UNV 数据集与 Simufact 六字段导出定义不符：' + str(path))
    nodes, mesh, total, temperature, displacement, plastic, elastic = [reader.read_sets(index) for index in range(1, 8)]
    if set(mesh) != {'type', 115}:
        raise ValueError('当前转换需要导出为八节点 hexa，包括退化单元。')
    elements = sorted(mesh[115], key=lambda item: item['element_nums'])
    element_ids = np.asarray([row['element_nums'] for row in elements], dtype=np.int64)
    node_ids = np.asarray(nodes['node_nums'], dtype=np.int64)
    if len(set(node_ids)) != len(node_ids) or len(set(element_ids)) != len(element_ids):
        raise ValueError('UNV 节点或单元编号重复。')
    index = {int(ident): number for number, ident in enumerate(node_ids)}
    connection = np.asarray([[index[int(node)] for node in row['nodes_nums']] for row in elements])
    points = np.column_stack([nodes[key] for key in ('x', 'y', 'z')]) * 1000
    xyz = points[connection]
    # Marc 八点 Gauss 顺序通过真实结果的位移梯度进行独立核对。
    jacobian = np.einsum('eni,pnj->epij', xyz, DERIVATIVES)
    determinant = np.linalg.det(jacobian)
    if not np.isfinite(determinant).all() or np.any(determinant <= 1e-18):
        raise ValueError('UNV 单元含非正体积或无效坐标。')
    values = {}
    for name, field, result_type in [('total', total, 3), ('plastic', plastic, 34), ('elastic', elastic, 150)]:
        order = np.argsort(field['element_nums'])
        if (field['dataset_location'] != 3 or field['data_characteristic'] != 4
                or field['result_type'] != result_type
                or not np.array_equal(np.asarray(field['element_nums'])[order], element_ids)
                or not np.all(field['number_of_nodes'] == 8)
                or not np.all(field['number_of_values_per_node'] == 6)):
            raise ValueError('UNV {} 的张量定义或结果位置数量不符。'.format(name))
        array = np.asarray(field['data_at_nodes_on_element'], dtype=float)[order]
        if array.shape != (len(elements), 8, 6) or not np.isfinite(array).all():
            raise ValueError('UNV 应变数据缺失或含非有限数值。')
        values[name] = strain_tensor(array)
    if temperature['result_type'] != 5 or displacement['result_type'] != 8:
        raise ValueError('UNV 温度或位移字段定义不符。')
    disp = dict(zip(displacement['node_nums'], displacement['data_at_node']))
    displacement_mm = np.asarray([disp[int(node)] for node in node_ids]) * 1000
    temperatures = np.asarray(temperature['data_at_node'], dtype=float).reshape(-1) - 273.15
    if not np.isfinite(displacement_mm).all() or not np.isfinite(temperatures).all():
        raise ValueError('UNV 温度或位移含非有限数值。')
    return dict(values, points_mm=points, connection=connection, element_ids=element_ids,
                positions_mm=np.einsum('pn,enj->epj', SHAPE, xyz), weights_mm3=determinant,
                jacobian=jacobian, displacement_mm=displacement_mm,
                temperature_max_c=float(temperatures.max()), temperature_min_c=float(temperatures.min()))


def validate_locations(value):
    physical = np.einsum('pnj,epjk->epnk', DERIVATIVES, np.linalg.inv(value['jacobian']))
    gradient = np.einsum('eni,epnj->epij', value['displacement_mm'][value['connection']], physical)
    symmetric = .5 * (gradient + gradient.swapaxes(-1, -2))
    observed = value['total']
    signal = float(np.sqrt(np.mean(observed ** 2)))
    error = float(np.sqrt(np.mean((symmetric - observed) ** 2)))
    relative = error / max(signal, 1e-12)
    if signal > 1e-9 and relative > .08:
        raise ValueError('积分点位置/剪应变核对误差 {:.1%}，超出当前小应变等效处理范围。'.format(relative))
    return {'relative_rms': relative, 'signal_rms': signal, 'engineering_shear': True,
            'profile': 'simufact-unv-hex8-gauss-bit-order',
            'component_order': ['XX', 'XY', 'YY', 'XZ', 'YZ', 'ZZ'],
            'validation': '节点位移梯度数值核对；有限应变与单元假定应变会产生差异'}
