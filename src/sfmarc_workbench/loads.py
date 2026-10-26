from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import PurePosixPath
from uuid import uuid4

from fortranformat import FortranRecordWriter
import numpy as np

from .geometry import SurfaceModel


@dataclass(frozen=True)
class LoadSpec:
    faces: list[int]
    magnitude: float
    mode: str
    cases: list[int]
    direction: list[float] = field(default_factory=lambda: [0.0, 0.0, -1.0])
    length_unit: str = "m"
    force_unit: str = "N"
    pressure_unit: str = "MPa"
    name: str = "面载荷"


def validate(model: SurfaceModel, spec: LoadSpec):
    faces = np.array(sorted(set(spec.faces)), dtype=np.int64)
    if not len(faces) or min(faces) < 0 or max(faces) >= len(model.faces):
        raise ValueError("请选择有效的模型表面")
    if not np.isfinite(spec.magnitude) or spec.magnitude <= 0:
        raise ValueError("载荷大小必须是大于零的有限数值")
    if spec.mode not in ("inward", "outward", "manual"):
        raise ValueError("载荷方向模式无效")
    if not spec.cases or any(i < 0 or i >= len(model.deck.cases) for i in spec.cases):
        raise ValueError("请选择有效的计算阶段")
    lengths = {"m": 1.0, "mm": 0.001}
    forces = {"N": 1.0, "kN": 1000.0}
    pressures = {"Pa": 1.0, "kPa": 1000.0, "MPa": 1e6}
    if spec.length_unit not in lengths or spec.force_unit not in forces or spec.pressure_unit not in pressures:
        raise ValueError("单位设置无效")
    pressure = spec.magnitude * pressures[spec.pressure_unit] * lengths[spec.length_unit] ** 2 / forces[spec.force_unit]
    if not np.isfinite(pressure) or pressure == 0:
        raise ValueError("换算后的载荷大小超出可表示范围")
    direction = np.array(spec.direction, dtype=np.float64)
    if direction.shape != (3,) or not np.isfinite(direction).all():
        raise ValueError("方向必须包含三个有限数值")
    if spec.mode == "manual":
        length = np.linalg.norm(direction)
        if not np.isfinite(length) or length == 0:
            raise ValueError("手动方向不能使用零向量或超出范围的向量")
        direction /= length
    return faces, pressure, direction


def preview(model: SurfaceModel, spec: LoadSpec):
    faces, pressure, direction = validate(model, spec)
    area = float(model.areas[faces].sum())
    if spec.mode == "manual":
        force = pressure * area * direction
    else:
        force = pressure * model.area_vectors[faces].sum(axis=0) * (-1 if spec.mode == "inward" else 1)
    if not np.isfinite(force).all():
        raise ValueError("合力超出可表示范围")
    samples = faces[np.linspace(0, len(faces) - 1, min(36, len(faces)), dtype=int)]
    directions = np.tile(direction, (len(samples), 1)) if spec.mode == "manual" else model.normals[samples] * (-1 if spec.mode == "inward" else 1)
    return {"area": area, "face_count": len(faces), "force": force.tolist(), "pressure": pressure,
            "centers": model.centers[samples].tolist(), "directions": directions.tolist(),
            "area_unit": spec.length_unit + "²", "force_unit": spec.force_unit}


def export_model(model: SurfaceModel, loads: list[LoadSpec]):
    deck = model.deck
    if not deck.table:
        raise ValueError("载荷导出需要 TABLE 形式的输入文件")
    if not loads:
        raise ValueError("请添加至少一个载荷")
    if len({(spec.length_unit, spec.force_unit) for spec in loads}) != 1:
        raise ValueError("所有载荷必须使用相同的模型单位")
    if deck.follow and any(spec.mode == "manual" for spec in loads):
        raise ValueError("手动方向面力与 FOLLOW FOR 的组合尚未验证，当前不能导出")
    iw, rw = deck.iw, deck.iw * 2

    def integers(*values):
        return FortranRecordWriter(f"{len(values)}I{iw}").write(values)

    def reals(values):
        result = FortranRecordWriter(f"{len(values)}E{rw}.{13 if deck.extended else 3}").write(values)
        if "*" in result:
            raise ValueError("载荷数值超过 Marc 字段宽度")
        return result

    blocks, summaries = [], []
    active = [[] for _ in deck.cases]
    for spec in loads:
        faces, pressure, direction = validate(model, spec)
        labels = []
        for local in np.unique(model.local_faces[faces]):
            group = faces[model.local_faces[faces] == local]
            label = "sfw_" + uuid4().hex[:20]
            labels.append(label)
            magnitude = (pressure * direction).tolist() if spec.mode == "manual" else [pressure * (1 if spec.mode == "inward" else -1)]
            blocks += [integers(1, 0, 0, 0, 0, 0) + label, reals(magnitude), integers(*([0] * len(magnitude))),
                       integers(0, 21 if spec.mode == "manual" else 1, int(local)), integers(1)]
            ids = model.element_ids[group].tolist()
            for start in range(0, len(ids), 6):
                blocks.append(integers(*ids[start:start+6]) + (" C" if start + 6 < len(ids) else ""))
        for case in set(spec.cases):
            active[case].extend(labels)
        summaries.append({"name": spec.name, "labels": labels, "magnitude": spec.magnitude,
                          "pressure_unit": spec.pressure_unit, "length_unit": spec.length_unit, "force_unit": spec.force_unit,
                          "mode": spec.mode, "direction": direction.tolist(), "cases": [deck.cases[i].name for i in sorted(set(spec.cases))],
                          "faces": [[int(model.element_ids[i]), int(model.local_faces[i])] for i in faces],
                          "preview": preview(model, spec)})
    patches = {}

    def patch(path, start, stop, after):
        patches.setdefault(path, []).append((start, stop, after))

    source = deck.lines[deck.insertion]
    newline = b"\r\n" if source.raw.endswith(b"\r\n") else b"\n"
    count = sum(len(item["labels"]) for item in summaries)
    block = newline.join(line.encode("ascii") for line in ["$ sfMarc Workbench: surface loads", "DIST LOADS", integers(count, 0), *blocks]) + newline
    patch(source.path, source.number, source.number, block)
    for case, labels in zip(deck.cases, active):
        if not labels:
            continue
        source = deck.lines[case.count_line]
        newline = b"\r\n" if source.raw.endswith(b"\r\n") else b"\n"
        value = source.text
        tail = ("," + value.split(",", 1)[1]).encode("ascii") if "," in value else value[iw:].encode("ascii")
        first = str(len(case.label_lines) + len(labels)).encode("ascii") if "," in value else integers(len(case.label_lines) + len(labels)).encode("ascii")
        patch(source.path, source.number, source.number + 1, first + tail + newline)
        last = deck.lines[case.label_lines[-1]] if case.label_lines else source
        if not last.raw.endswith((b"\n", b"\r")):
            raise ValueError("LOADCASE 的最后一行缺少换行符")
        patch(last.path, last.number + 1, last.number + 1, newline.join(label.encode("ascii") for label in labels) + newline)
    result = dict(deck.files)
    edit_report = {}
    for path, edits in patches.items():
        lines = deck.source_lines[path]
        offsets = np.cumsum([0] + [len(line) for line in lines]).tolist()
        parts, cursor, output_size = [], 0, 0
        records = []
        for start, stop, after in sorted(edits, key=lambda item: (item[0], item[1])):
            a, b = offsets[start], offsets[stop]
            if a < cursor:
                raise ValueError("导出编辑范围发生重叠")
            original = deck.files[path]
            parts.append(original[cursor:a])
            output_size += a - cursor
            records.append({"output_offset": output_size, "before": original[a:b].decode("ascii"), "after": after.decode("ascii")})
            parts.append(after)
            output_size += len(after)
            cursor = b
        parts.append(deck.files[path][cursor:])
        result[path] = b"".join(parts)
        edit_report[path] = records
    root_path = PurePosixPath(deck.root)
    new_root = str(root_path.with_name(root_path.stem + "_loaded.dat"))
    if new_root in result:
        raise ValueError("输出名称与已导入的文件重复")
    result[new_root] = result.pop(deck.root)
    report = {"root": new_root, "source_root": deck.root, "source_sha256": deck.digest,
              "output_sha256": {p: sha256(b).hexdigest() for p, b in result.items()},
              "loads": summaries, "edits": edit_report,
              "solver_validation": "尚未使用 Marc/sfMarc 求解器验证", "external_materials": deck.materials,
              "missing_materials": deck.missing_materials}
    return result, report
