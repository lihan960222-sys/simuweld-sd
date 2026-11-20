import numpy as np
from fortranformat import FortranRecordReader


def read_contact_bodies(deck):
    headers = [index for index, line in enumerate(deck.lines[:deck.end_option])
               if line.text.strip().upper().split() == ['CONTACT', '2']]
    if not headers:
        return []
    if len(headers) != 1:
        raise ValueError('DAT 要求一个初始 CONTACT 定义')
    cursor = headers[0]
    integers = FortranRecordReader(f'16I{deck.iw}')
    body_reader = FortranRecordReader(f'8I{deck.iw},A')
    point_reader = FortranRecordReader(f'I{deck.iw},3E{deck.kw}.0')

    def record(reader):
        nonlocal cursor
        cursor = deck.next_data(cursor + 1)
        return reader.read(deck.lines[cursor].text)

    count = record(integers)[0]
    if not count or count < 1:
        raise ValueError('CONTACT 刚体数量无效')
    for _ in range(3):
        cursor = deck.next_data(cursor + 1)
    bodies = []
    for _ in range(count):
        body = record(body_reader)
        name = body[-1].strip()
        for _ in range(4):
            cursor = deck.next_data(cursor + 1)
        thermal = record(FortranRecordReader(f'8E{deck.kw}.0'))
        tables = record(FortranRecordReader(f'8I{deck.iw}'))
        extra = record(FortranRecordReader(f'7E{deck.kw}.0'))
        extra_tables = record(FortranRecordReader(f'7I{deck.iw}'))
        if any(tables) or any(extra) or any(extra_tables):
            raise ValueError(f'CONTACT 实体 {name} 使用了当前二维计算尚未支持的换热表或自然对流')
        if not np.isfinite(thermal).all() or thermal[0] < 0 or not 0 <= thermal[7] <= 1:
            raise ValueError(f'CONTACT 实体 {name} 的热边界无效')
        if any(thermal[4:7]):
            raise ValueError(f'CONTACT 实体 {name} 使用了当前二维计算尚未支持的近接触热边界')
        if thermal[2] != 0:
            raise ValueError(f'CONTACT 实体 {name} 的实体级接触换热尚未支持，请使用 CONTACT TABLE')
        entry = dict(id=body[0], kind=body[1], name=name, convection_h=thermal[0],
                     ambient_c=thermal[1] - 273.15, emissivity=thermal[7],
                     temperature_c=thermal[3] - 273.15, points=None, faces=None)
        bodies.append(entry)
        if body[1] == 0:
            cursor = deck.next_data(cursor + 1)
            continue
        if body[1] != 1:
            raise ValueError(f'CONTACT 实体 {name} 的类型尚未支持')
        kind, patch_count, point_count, unit, *_ = record(integers)
        if kind != 7 or unit == -1 or not patch_count or not point_count or patch_count < 0 or point_count < 0:
            raise ValueError(f'CONTACT 刚体 {name} 要求显式三维 patches 和 points')
        patches = [record(integers)[:6] for _ in range(patch_count)]
        nodes = [record(point_reader) for _ in range(point_count)]
        identifiers = [row[0] for row in nodes]
        if len(set(identifiers)) != point_count:
            raise ValueError(f'CONTACT 刚体 {name} 存在重复 point 编号')
        points = np.asarray([row[1:] for row in nodes], dtype=float)
        if not np.isfinite(points).all():
            raise ValueError(f'CONTACT 刚体 {name} 坐标无效')
        lookup = {ident: index for index, ident in enumerate(identifiers)}
        faces = []
        for patch in patches:
            members = list(dict.fromkeys(patch[2:]))
            if len(members) < 3 or any(ident not in lookup for ident in members):
                raise ValueError(f'CONTACT 刚体 {name} 的 patch 节点无效')
            faces.append([lookup[ident] for ident in members])
        entry.update(points=points, faces=faces)
    return bodies


def read_rigid_surfaces(deck):
    return [(body['name'], body['points'], body['faces']) for body in read_contact_bodies(deck) if body['kind'] == 1]


def read_thermal_conditions(deck):
    bodies = read_contact_bodies(deck)
    lookup = {body['id']: body for body in bodies}
    headers = [index for index, line in enumerate(deck.lines)
               if line.text.strip().upper().split() == ['CONTACT', 'TABLE']]
    stages = []
    for header in headers:
        cursor = header

        def record(fmt):
            nonlocal cursor
            cursor = deck.next_data(cursor + 1)
            return FortranRecordReader(fmt).read(deck.lines[cursor].text)

        count, version = record(f'2I{deck.iw}')
        if version != 2 or count < 0:
            raise ValueError('当前二维计算要求 CONTACT TABLE 使用版本 2')
        pairs = []
        for _ in range(count):
            first = record(f'I{deck.iw},6E{deck.kw}.0,3I{deck.iw}')[0]
            for _ in range(7):
                cursor = deck.next_data(cursor + 1)
            thermal = record(f'8E{deck.kw}.0')
            equivalent = thermal[0] < 0
            if equivalent:
                # shortcut: Simufact 特殊接触定义使用等效系数，支持对应扩展后替换。
                record(f'I{deck.iw},7E{deck.kw}.0')
                record(f'6I{deck.iw}')
            tables = record(f'8I{deck.iw}')
            second = record(f'I{deck.iw}')[0]
            if first not in lookup or second not in lookup or not np.isfinite(thermal).all():
                raise ValueError('CONTACT TABLE 引用了无效实体或换热数值')
            if any(tables) or any(thermal[2:4]) or any(thermal[5:]):
                raise ValueError('CONTACT TABLE 使用了当前二维计算尚未支持的换热表或自然对流')
            if thermal[1] < 0 or not 0 <= thermal[4] <= 1:
                raise ValueError('CONTACT TABLE 近接触对流或辐射数值无效')
            if thermal[1] or thermal[4]:
                raise ValueError('CONTACT TABLE 使用了当前二维计算尚未支持的近接触对流或辐射')
            pairs.append(dict(first=first, second=second, h=1000. if equivalent else thermal[0],
                              equivalent=equivalent, raw_h=thermal[0],
                              near_convection_h=thermal[1], near_emissivity=thermal[4]))
        stages.append(sorted(pairs, key=lambda pair: (pair['first'], pair['second'])))
    if any(stage != stages[0] for stage in stages[1:]):
        raise ValueError('DAT 各阶段的换热条件发生变化，当前二维计算要求恒定热边界')
    return dict(bodies=bodies, pairs=stages[0] if stages else [], stage_count=len(stages))
