import json
import math
from hashlib import sha256
from pathlib import Path


def source_trajectories(process, paths):
    candidates = []
    for robot in process.all_robots:
        cursor = float(robot.start_time.value('s'))
        for trajectory in sorted(robot.all_trajectories, key=lambda item: item.index):
            start = cursor + float(trajectory.pause_time_start.value('s')) + float(trajectory.lead_time.value('s'))
            end = start + float(trajectory.welding_time.value('s'))
            candidates.append((robot, trajectory, start, end))
            cursor = end + float(trajectory.follow_up_time.value('s')) + float(trajectory.pause_time_end.value('s'))
    matched = {}
    for path in paths:
        found = []
        for robot, trajectory, start, end in candidates:
            parameter = trajectory.heat_source_parameters
            if parameter is None or parameter.heat_source is None:
                continue
            if (abs(float(trajectory.original_length.value('mm')) - path['length_mm']) <= .02
                    and abs(float(parameter.velocity.value('mm/s')) - path['speed_mm_s']) <= .03
                    and abs(start - path['start_s']) <= .001
                    and abs(end - path['end_s']) <= .001):
                found.append((robot, trajectory))
        if len(found) != 1:
            raise ValueError('焊路 {} 在来源 Process 中匹配到 {} 条轨迹；请核对来源名称和时间。'.format(path['id'], len(found)))
        if tuple(found[0][1].all_geometries):
            raise ValueError('轨迹 {} 含填充几何，需要独立确认局部模型。'.format(found[0][1].name))
        matched[path['id']] = found[0]
    return matched


def bodies(process):
    return [*process.all_components, *process.all_bearings, *process.all_clampings, *process.all_fixed_geometries]


def bounds_mm(body):
    corners = [body.bounds.cornerMMM, body.bounds.cornerPPP]
    output = []
    for point in corners:
        if point.unit not in ('m', 'mm'):
            raise ValueError('组件范围单位不受支持：' + str(point.unit))
        scale = 1000 if point.unit == 'm' else 1
        output.append([value * scale for value in (point.x, point.y, point.z)])
    return output


def source_state(process):
    return {'name': process.name,
            'bodies': [(body.name, None if body.geometry is None else body.geometry.name) for body in bodies(process)],
            'robots': [(robot.name, float(robot.start_time.value('s')),
                        [(trajectory.name, trajectory.heat_source_parameters.name,
                          float(trajectory.welding_time.value('s'))) for trajectory in robot.all_trajectories])
                       for robot in process.all_robots]}


def main():
    from simufact import Matrix4x4, UnitValueTemperature, UnitValueTime, Vector3D
    from simufact.welding import current_project

    project = current_project()
    if project is None:
        raise RuntimeError('请打开来源瞬态计算的 Simufact 项目。')
    directory = Path(__file__).resolve().parent
    config = json.loads((directory / 'case_manifest.json').read_text(encoding='utf-8'))
    source = project.process(config['source_process'])
    if source is None:
        raise ValueError('来源 Process 不存在：' + config['source_process'])
    initial = source_state(source)
    target_names = [group['target_process'] for group in config['groups']]
    if len(set(target_names)) != len(target_names) or any(process.name in target_names for process in project.all_processes):
        raise ValueError('目标 Process 名称重复或已经存在。')
    source_bodies = {body.name: body for body in bodies(source)}
    component_names = config.get('component_names', {})
    source_paths = source_trajectories(source, config['source_paths'])
    existing_names = {parameter.name for parameter in project.all_heat_source_parameters} | {line.name for line in project.all_weld_lines}
    for group in config['groups']:
        for component in group['crop']['components']:
            name = component_names.get(component['name'], component['name'])
            if name not in source_bodies and name == component['name']:
                matches = [body.name for body in source_bodies.values()
                           if all(abs(value - expected) < .02
                                  for actual, target in zip(bounds_mm(body), component['source_bounds_mm'])
                                  for value, expected in zip(actual, target))]
                if len(matches) == 1:
                    name = matches[0]
                    component_names[component['name']] = name
            if name not in source_bodies:
                raise ValueError('DAT 组件 {} 对应的 Simufact 名称 {} 不存在；请在 SD 设置组件对应。实际名称：{}'.format(
                    component['name'], name, '、'.join(source_bodies)))
            if 'mesh_file' in component:
                path = directory / component['mesh_file']
                if not path.is_file() or sha256(path.read_bytes()).hexdigest() != component['mesh_sha256']:
                    raise ValueError('局部网格文件缺失或校验失败：' + str(path))
        for event in group['events']:
            if event['name'] in existing_names:
                raise ValueError('项目已存在同名焊路或热源：' + event['name'])
    for group in config['groups']:
        target = source.copy()
        target.name = group['target_process']
        if target.name != group['target_process']:
            raise ValueError('Process 名称设置失败。')
        target_bodies = {body.name: body for body in bodies(target)}
        for component in group['crop']['components']:
            name = component_names.get(component['name'], component['name'])
            body = target_bodies[name]
            if component['name'].lower() == 'base' or component['keep_whole']:
                continue
            if 'mesh_file' not in component:
                # 空组件从副本中移除，来源组件保留。
                if name in {item.name for item in target.all_components}:
                    target.delete_component(body)
                elif name in {item.name for item in target.all_clampings}:
                    target.delete_clamping(body)
                elif name in {item.name for item in target.all_fixed_geometries}:
                    target.delete_fixed_geometry(body)
                else:
                    target.delete_bearing(body)
                continue
            imported_geometry = project.import_geometry(str(directory / component['mesh_file']), unit='mm', as_single_body=True)
            if imported_geometry is None:
                raise ValueError('网格导入没有返回几何对象：' + name)
            is_component = name in {item.name for item in target.all_components}
            geometry = imported_geometry.make_solid_shell() if is_component else imported_geometry
            if geometry is None:
                raise ValueError('网格转换没有返回几何对象：' + name)
            mesh = geometry.volume_mesh()
            if (mesh is None or mesh.number_nodes != len(component['node_ids'])
                    or mesh.number_elements != len(component['element_ids'])):
                raise ValueError('导入后的节点数或单元数与裁剪网格不一致：' + name)
            geometry.name = target.name + '_' + name
            print('替换组件 {}：{} → {}；{} 个节点，{} 个单元。'.format(
                name, component['mesh_file'], geometry.name, mesh.number_nodes, mesh.number_elements), flush=True)
            # 组件指定新几何前，需要先解除副本中的原几何关联。
            body.geometry = None
            body.geometry = geometry
            body.transformation = Matrix4x4()
            if body.geometry is None or body.geometry.name != geometry.name:
                raise ValueError('组件的新几何关联检查失败：' + name)
            if is_component:
                project.delete_geometry(imported_geometry)
        for robot in tuple(target.all_robots):
            target.delete_robot(robot)
        final_time = max(event['end_s'] for event in group['events']) + config['max_cooling_s']
        for event, match in zip(group['events'], sorted(group['matches'], key=lambda row: row['start_s'])):
            source_robot, source_trajectory = source_paths[match['path_id']]
            parameter = source_trajectory.heat_source_parameters.copy()
            parameter.name = event['name']
            line = project.new_weld_line(event['name'], 'local-vector')
            for point, normal in zip(match['points_mm'], match['normals']):
                line.add_point(True, Vector3D(point, 'mm'), normal)
            robot = target.new_robot(event['name'])
            robot.start_time = UnitValueTime(event['start_s'], 's')
            robot.material = source_robot.material
            robot.temperature = source_robot.temperature
            trajectory = robot.new_trajectory(line, *(UnitValueTime(0., 's') for _ in range(4)))
            trajectory.heat_source_parameters = parameter
            if not math.isclose(float(trajectory.welding_time.value('s')), event['end_s'] - event['start_s'], abs_tol=1e-5, rel_tol=1e-4):
                raise ValueError('局部焊接持续时间与原工艺不一致：' + event['name'])
        # 局部应变提取期间保留夹具，释放阶段可在完成试算后独立建立。
        for clamping in target.all_clampings:
            clamping.start_time = UnitValueTime(0., 's')
            clamping.end_time = None
            if clamping.definition in ('stiffness', 'stiffness+force') and clamping.fixation_start_time is not None:
                clamping.fixation_start_time = UnitValueTime(0., 's')
                clamping.fixation_end_time = None
        time_control = target.process_parameters.time_control
        time_control.analysis_end_time = UnitValueTime(final_time, 's')
        time_control.set_result_output_frequency(welding=1, cooling=1, coupled_increments_only=True)
        time_control.set_mechanical_solution_frequency(welding=1, cooling=1)
        target.termination_criteria.new_body_criterion(
            targets=tuple(component.name for component in target.all_components),
            loadcases=('Cooling',), value=UnitValueTemperature(config['cooling_limit_c'], 'C'),
            type='MaximumTemperature', only_last_loadcase=True, termination_mode='Analysis')
        local = dict(config, groups=[group], source_state=initial)
        (directory / (target.name + '_case.json')).write_text(json.dumps(local, ensure_ascii=False, indent=2), encoding='utf-8')
        print('已创建 {}：{} 次焊接；最终最高温度停止值 {}℃，最长冷却 {} s。'.format(
            target.name, len(group['events']), config['cooling_limit_c'], config['max_cooling_s']), flush=True)
    if source_state(source) != initial:
        raise ValueError('来源 Process 状态发生变化，请停止后续操作并检查对象共享。')
    print('请确认切割面热边界、夹具接触和局部连接，再生成 DAT 并计算。')


if __name__ == '__main__':
    main()
