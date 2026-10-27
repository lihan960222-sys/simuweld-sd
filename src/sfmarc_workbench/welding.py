import csv
from io import StringIO
import math
from pathlib import PurePath
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from .weld_script import SCRIPT_BODY


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80, pattern=r"^[^\x00-\x1f\x7f]+$")]
Coordinate = Annotated[float, Field(allow_inf_nan=False)]
Point = tuple[Coordinate, Coordinate, Coordinate]


class WeldPath(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: Name
    points: list[Point] = Field(min_length=2, max_length=100000)
    pause_after: float = Field(ge=0)
    heat_source: Name

    @field_validator("points")
    @classmethod
    def check_points(cls, points):
        path_length(points)
        return points


class HeatParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    power_w: float = Field(gt=0)
    velocity: float = Field(gt=0)
    efficiency: float = Field(ge=0, le=1)
    conical_upper_radius: float = Field(gt=0)
    conical_lower_radius: float = Field(ge=0)
    conical_depth: float = Field(gt=0)
    conical_gaussian_parameter: float = Field(ge=0, le=3)
    volume_heat_fraction: float = Field(ge=0, le=1)
    disc_radius: float = Field(gt=0)
    surface_depth: float = Field(gt=0)
    disc_gaussian_parameter: float = Field(ge=0, le=3)


class HeatProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: Name
    parameters: HeatParameters


class WeldPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    process: Name
    robot: Name
    heats: list[HeatProfile] = Field(min_length=1, max_length=1000)
    orientation: Point = (0, 0, 1)
    paths: list[WeldPath] = Field(min_length=1, max_length=1000)

    @field_validator("orientation")
    @classmethod
    def check_direction(cls, direction):
        length = math.hypot(*direction)
        if not math.isfinite(length) or length == 0:
            raise ValueError("方向向量必须为有限数值且不能为零")
        return tuple(value / length for value in direction)

    @model_validator(mode="after")
    def check_paths(self):
        heat_names = {heat.name for heat in self.heats}
        if len(heat_names) != len(self.heats):
            raise ValueError("热源名称不能重复")
        missing = {path.heat_source for path in self.paths} - heat_names
        if missing:
            raise ValueError("焊路引用的热源不存在：" + "、".join(sorted(missing)))
        if len({path.name for path in self.paths}) != len(self.paths):
            raise ValueError("焊路名称不能重复")
        if sum(len(path.points) for path in self.paths) > 1000000:
            raise ValueError("焊接顺序列表累计不能超过 100 万个点")
        return self


def path_length(points):
    distances = [math.dist(first, second) for first, second in zip(points, points[1:])]
    if not distances or any(not math.isfinite(length) or length <= 0 for length in distances):
        raise ValueError("路径至少包含两个点，相邻点距离必须为有限数值且大于零")
    total = math.fsum(distances)
    if not math.isfinite(total):
        raise ValueError("路径长度必须为有限数值")
    return total


def read_paths(data: bytes, filename: str, unit: str):
    if unit not in ("mm", "m"):
        raise ValueError("CSV 长度单位仅支持 mm 或 m")
    reader = csv.DictReader(StringIO(data.decode("utf-8-sig"), newline=""), strict=True)
    columns = reader.fieldnames or []
    orders = [column for column in ("Order", "Index", "PointOrder", "PointID") if column in columns]
    if len(columns) != len(set(columns)) or not {"X", "Y", "Z"}.issubset(columns) or len(orders) != 1:
        raise ValueError(f"{filename}：需要 X、Y、Z 及 Order（也可使用 Index、PointOrder 或 PointID），列名不能重复")
    group_columns = [column for column in ("PathID", "WeldID") if column in columns]
    if len(group_columns) > 1:
        raise ValueError("PathID 与 WeldID 只能使用其中一列")
    groups = {}
    scale = 1000 if unit == "m" else 1
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"{filename} 第 {reader.line_num} 行：列数不一致")
        group = row[group_columns[0]].strip() if group_columns else ""
        if group_columns and not group:
            raise ValueError(f"{filename} 第 {reader.line_num} 行：PathID 不能为空")
        order = int(row[orders[0]])
        if abs(order) > 9007199254740991:
            raise ValueError("点序超过浏览器可精确表示的整数范围")
        xyz = [float(row[column]) * scale for column in ("X", "Y", "Z")]
        if not all(math.isfinite(value) for value in xyz):
            raise ValueError(f"{filename} 第 {reader.line_num} 行：坐标必须为有限数值")
        groups.setdefault(group, []).append({"order": order, "node_id": row.get("NodeID", row.get("SourceNodeID", "")), "xyz": xyz})
        if reader.line_num > 100001:
            raise ValueError("单份 CSV 不能超过 10 万个点")
    if not groups:
        raise ValueError(f"{filename}：CSV 没有路径点")
    paths = []
    for group, points in groups.items():
        points.sort(key=lambda point: point["order"])
        if len({point["order"] for point in points}) != len(points):
            raise ValueError(f"{filename} / {group}：同一路径的点序不能重复")
        length = path_length([point["xyz"] for point in points])
        name = PurePath(filename).stem + (" / " + group if group else "")
        paths.append({"name": name, "points": points, "length_mm": length})
    return paths


def generate_script(plan: WeldPlan):
    paths = [{"name": path.name, "points": [list(point) for point in path.points],
              "pause_after": path.pause_after, "length_mm": path_length(path.points),
              "heat_source": path.heat_source} for path in plan.paths]
    constants = {"PROCESS_NAME": plan.process, "ROBOT_NAME": plan.robot,
                 "ORIENTATION": plan.orientation,
                 "HEATS": {heat.name: heat.parameters.model_dump() for heat in plan.heats}}
    source = "import math\n\n" + "\n".join(f"{key} = {value!r}" for key, value in constants.items())
    source += "\nPATHS = [\n" + "".join(f"    {path!r},\n" for path in paths) + "]\n"
    return source + SCRIPT_BODY
