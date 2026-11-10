import math


def weld_lines(config):
    lines = []
    names = set()
    for group in config["groups"]:
        for index, match in enumerate(sorted(group["matches"], key=lambda row: row["start_s"]), 1):
            name = "{}_Weld_{:03d}".format(group["name"], index)
            points, normals = match["points_mm"], match["normals"]
            if name in names or not group["name"].strip():
                raise ValueError("焊路名称重复或为空。")
            if (len(points) < 2 or len(points) != len(normals)
                    or any(len(row) != 3 or any(not math.isfinite(value) for value in row)
                           for row in points + normals)
                    or any(not math.isclose(math.hypot(*normal), 1., abs_tol=1e-6) for normal in normals)):
                raise ValueError("{} 的坐标或方向无效。".format(name))
            if (not math.isfinite(match["start_s"]) or not math.isfinite(match["end_s"])
                    or match["start_s"] < 0 or match["end_s"] <= match["start_s"]
                    or math.fsum(math.dist(a, b) for a, b in zip(points, points[1:])) <= 0):
                raise ValueError("{} 的长度或时间无效。".format(name))
            names.add(name)
            lines.append({"name": name, **match})
    if not lines:
        raise ValueError("没有需要创建的焊路。")
    return lines


def main():
    from simufact import Vector3D
    from simufact.welding import current_project

    project = current_project()
    if project is None:
        raise RuntimeError("请在 Simufact Welding 中打开目标项目。")
    lines = weld_lines(CONFIG)
    existing = {line.name for line in project.all_weld_lines}
    duplicates = [line["name"] for line in lines if line["name"] in existing]
    if duplicates:
        raise ValueError("项目中已有同名焊路：{}".format("、".join(duplicates)))
    for item in lines:
        line = project.new_weld_line(item["name"], "local-vector")
        for point, normal in zip(item["points_mm"], item["normals"]):
            line.add_point(True, Vector3D(point, "mm"), normal)
        print("已创建 {}，原始焊接时间 {}～{} s".format(item["name"], item["start_s"], item["end_s"]))
    print("焊路创建完成。请添加到 Robot，设置热源、时间及模型后保存项目。")


if __name__ == "__main__":
    main()
