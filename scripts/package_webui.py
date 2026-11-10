import argparse
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED


parser = argparse.ArgumentParser()
parser.add_argument("source", type=Path)
parser.add_argument("destination", type=Path)
args = parser.parse_args()
source = args.source.resolve()
files = [source / "SIMUWELD-SD-Web.exe", source / "使用说明.md", source / "模块更新说明.md"]
files.extend(path for path in (source / "_internal").rglob("*") if path.is_file())
for folder in (source / "shared", *(source / "modules" / name for name in
                                   ("face_load", "weld_paths", "thermal_cycle", "local_trial"))):
    manifest = json.loads((folder / "module.json").read_text(encoding="utf-8"))
    files.append(folder / "module.json")
    files.extend(folder / name for name in manifest["files"])
if not all(path.is_file() for path in files) or not (source / "_internal" / "LOCAL_TRIAL.md").is_file():
    raise ValueError("交付程序或使用说明不完整")
temporary = args.destination.with_suffix(".zip.building")
with ZipFile(temporary, "w", ZIP_DEFLATED, compresslevel=6) as archive:
    for path in files:
        archive.write(path, "SIMUWELD-SD/" + path.relative_to(source).as_posix())
with ZipFile(temporary) as archive:
    failure = archive.testzip()
    if failure is not None:
        raise ValueError("压缩文件校验失败：" + failure)
temporary.replace(args.destination)
print(f"已打包并校验 {len(files)} 个文件：{args.destination}")
