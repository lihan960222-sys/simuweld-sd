# -*- coding: utf-8 -*-
import json
import os
from hashlib import sha256

from abaqus import mdb


def main():
    directory = os.path.dirname(os.path.abspath(__file__))
    filename = os.path.join(directory, SETTINGS['filename'])
    model = mdb.ModelFromInputFile(name='SD_Preview', inputFileName=filename)
    scale = 1000. if SETTINGS['length_unit'] == 'm' else 1.
    points, faces = [], []
    for name, instance in model.rootAssembly.instances.items():
        indices = {}
        for node in instance.nodes:
            indices[node.label] = len(points)
            points.append([float(value) * scale for value in node.coordinates])
        for element in instance.elements:
            if element.type not in ('S3', 'S4', 'S4R', 'R3D3', 'R3D4'):
                raise ValueError(u'预览支持 S3、S4、S4R 和刚性面单元：' + element.type)
            faces.append([indices[node.label] for node in element.getNodes()])
    with open(filename, 'rb') as stream:
        digest = sha256(stream.read()).hexdigest()
    output = {'schema': 'simuweld-shell-preview-1', 'source_sha256': digest,
              'points_mm': points, 'faces': faces}
    with open(os.path.join(directory, 'shell_preview.json'), 'w') as stream:
        json.dump(output, stream)
    print(u'已生成 shell_preview.json，请与原 INP 一起导入 SD。')


if __name__ == '__main__':
    main()
