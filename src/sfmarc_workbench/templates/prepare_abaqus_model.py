# -*- coding: utf-8 -*-
from __future__ import print_function
import json
import os
import shutil
import sys

import numpy as np

from abaqus import mdb
from abaqusConstants import ANISOTROPIC, ON, OFF, SIMPSON, UNIFORM

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shell_mapping import element_key


def integration_points(element):
    coordinates = np.asarray([node.coordinates for node in element.getNodes()], dtype=float)
    if element.type in ('S3', 'S4R'):
        return [coordinates.mean(axis=0)]
    if element.type == 'S4':
        signs = np.asarray([[-1., -1.], [1., -1.], [1., 1.], [-1., 1.]])
        return [np.dot(np.prod(1 + point * signs, axis=1) / 4, coordinates)
                for point in signs / np.sqrt(3)]
    raise ValueError(u'当前顺序加载支持 S3、S4R 和 S4：' + element.type)


def main():
    directory = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(directory, 'sd_loading.json'), 'rb') as stream:
        config = json.load(stream)
    if config['schema'] != 'simuweld-abaqus-loading-1':
        raise ValueError(u'计算包版本不受支持。')
    name = config['job_name']
    output = os.path.join(directory, name)
    if os.path.exists(output):
        raise ValueError(u'输出目录已经存在，请保留已有结果后修改计算名称。')
    os.mkdir(output)
    model = mdb.ModelFromInputFile(name=name, inputFileName=os.path.join(directory, config['input_file']))
    if any(key != 'Initial' for key in model.steps.keys()):
        raise ValueError(u'输入模型需要保留 Initial 边界，并由本脚本创建焊接计算步骤。')
    scale = 1000. if config['model_length_unit'] == 'm' else 1.
    rows, affected, maximum_passes = [], {}, 0
    mapped_elements = set()
    section_materials = set()
    for instance_name, instance in model.rootAssembly.instances.items():
        if len(instance_name) > 80 or any(ord(character) > 127 for character in instance_name):
            raise ValueError(u'用于子程序的数据需要 ASCII 格式且不超过 80 个字符的 Instance 名称。')
        part = model.parts[instance.partName]
        sections = {}
        for assignment in part.sectionAssignments:
            for element in assignment.region.elements:
                sections[element.label] = assignment.sectionName
        for element in instance.elements:
            coordinates = np.asarray([node.coordinates for node in element.getNodes()]) * scale
            key = element_key(coordinates)
            definition = config['element_loads'].get(key)
            if definition is None:
                continue
            if key in mapped_elements:
                raise ValueError(u'官方导入后出现重复单元位置，无法唯一关联加载数据。')
            mapped_elements.add(key)
            if element.label not in sections:
                mdb.saveAs(pathName=os.path.join(output, name + '.cae'))
                raise ValueError(u'目标板件缺少 shell 截面。已保存 CAE，请完成材料及截面后重新导出来源 INP。')
            section_name = sections[element.label]
            section = model.sections[section_name]
            if not hasattr(section, 'numIntPts') or not hasattr(section, 'thickness'):
                raise ValueError(u'当前计算需要均质 shell 截面：' + section_name)
            if section.thicknessType != UNIFORM or section.preIntegrate != OFF:
                raise ValueError(u'当前计算需要统一厚度、计算期间积分的均质 shell 截面：' + section_name)
            thickness = float(section.thickness) * scale
            if thickness <= 0:
                raise ValueError(u'shell 厚度无效。')
            section_materials.add((section_name, section.material))
            count = 0
            for point_number, point in enumerate(integration_points(element), 1):
                schedule = []
                for source_event in definition['events']:
                    event = dict(source_event)
                    ratio = event['source_thickness_mm'] / thickness
                    event['membrane_global'] = [value * ratio for value in event['membrane_global']]
                    event['curvature_global_per_mm'] = [value * ratio ** 3 for value in event['curvature_global_per_mm']]
                    schedule.append(event)
                count = max(count, len(schedule))
                for event in schedule:
                    rows.append((instance_name, int(element.label), point_number, thickness, event))
            maximum_passes = max(maximum_passes, count)
            if count:
                affected[instance_name] = affected.get(instance_name, 0) + 1
    if not rows:
        raise ValueError(u'整体模型没有匹配到焊接影响区域，请检查坐标、单位及板件对应。')
    if len(mapped_elements) != len(config['element_loads']):
        raise ValueError(u'官方导入网格与 SD 面积积分网格不一致，请重新生成装配预览。')
    for section_name, material_name in sorted(section_materials):
        original = model.materials[material_name]
        if not hasattr(original, 'elastic') or hasattr(original, 'userMaterial') or hasattr(original, 'depvar'):
            mdb.saveAs(pathName=os.path.join(output, name + '.cae'))
            raise ValueError(u'请提供已定义弹性参数且未占用 STATEV 的常规材料：' + material_name)
        new_name = 'SD_' + material_name
        if new_name not in model.materials:
            material = model.Material(name=new_name, objectToCopy=original)
            material.Depvar(n=1 + 5 * maximum_passes)
            material.UserDefinedField()
            material.Expansion(type=ANISOTROPIC, userSubroutine=ON)
        model.sections[section_name].setValues(material=new_name, integrationRule=SIMPSON, numIntPts=5)
    data_path = os.path.join(output, 'SD_LOAD_DATA.txt')
    with open(data_path, 'w') as stream:
        stream.write('%d %d\n' % (len(rows), maximum_passes))
        for instance_name, element, point, thickness, event in rows:
            stream.write(instance_name + '\n')
            values = [event['start_s'], event['end_s'], thickness] + event['normal'] + event['membrane_global'] + event['curvature_global_per_mm']
            stream.write('%d %d %s\n' % (element, point, ' '.join('%.17g' % value for value in values)))
    shortest = min(event['end_s'] - event['start_s'] for _, _, _, _, event in rows)
    final_time = max(event['end_s'] for _, _, _, _, event in rows) + shortest
    model.StaticStep(name='SD_SEQUENCE', previous='Initial', nlgeom=ON, timePeriod=final_time,
                     initialInc=shortest / 2, maxInc=shortest, minInc=shortest * 1e-6, maxNumInc=1000000)
    model.FieldOutputRequest(name='SD_RESULTS', createStepName='SD_SEQUENCE', variables=('S', 'U', 'UR', 'LE', 'SDV'))
    source_fortran = os.path.join(directory, 'local_shell_loading.for')
    shutil.copyfile(source_fortran, os.path.join(output, 'local_shell_loading.for'))
    os.chdir(output)
    job = mdb.Job(name=name, model=model.name, userSubroutine=os.path.join(output, 'local_shell_loading.for'))
    job.writeInput(consistencyChecking=ON)
    mdb.saveAs(pathName=os.path.join(output, name + '.cae'))
    with open(os.path.join(output, 'mapping_report.json'), 'w') as stream:
        json.dump({'affected_elements': affected, 'material_point_events': len(rows),
                   'maximum_passes': maximum_passes, 'target_version': config['target_version'],
                   'solver_run': False}, stream, indent=2)
    print(u'已生成 INP、CAE 和顺序加载数据。请执行 datacheck 并检查材料、板间连接和边界。')


if __name__ == '__main__':
    main()
