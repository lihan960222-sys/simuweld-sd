import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
from zipfile import ZipFile, ZIP_DEFLATED


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "sfmarc_workbench"
GROUPS = {
    "shared": ["__init__", "launcher", "server", "deck", "geometry", "background", "thermal_deck"],
    "face_load": ["loads", "routes_face_load"],
    "weld_paths": ["welding", "weld_script", "routes_weld_paths"],
    "thermal_cycle": ["thermal", "sampling", "routes_thermal_cycle"],
    "local_trial": ["local_trial", "local_trial_export", "routes_local_trial"],
}
TEMPLATES = {"thermal_cycle": ["tc_create.py", "tc_export.py", "thermal_cycle_source.xml"],
             "local_trial": ["local_trial_paths.py"]}


def build(destination, version, archives=None):
    destination = Path(destination).resolve()
    for ident, modules in GROUPS.items():
        folder = Path("shared" if ident == "shared" else "modules/" + ident)
        target = destination / folder
        files, python = {}, {}

        def copy(source, relative):
            output = target / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, output)
            files[relative] = sha256(output.read_bytes()).hexdigest()

        for name in modules:
            relative = "backend/" + name + ".py"
            copy(SOURCE / (name + ".py"), relative)
            python["sfmarc_workbench" + ("" if name == "__init__" else "." + name)] = relative
        for name in TEMPLATES.get(ident, []):
            relative = "backend/templates/" + name
            copy(SOURCE / "templates" / name, relative)
            if name.endswith(".py"):
                python["sfmarc_workbench.templates." + name[:-3]] = relative
        web = ROOT / "dist" / folder
        if not web.is_dir():
            raise ValueError("缺少网页模块构建结果：" + str(web))
        for source in sorted(web.rglob("*")):
            if source.is_file():
                copy(source, "web/" + source.relative_to(web).as_posix())
        if ident == "shared":
            for name in ("index.html", "icon.svg", "icon.ico"):
                copy(ROOT / "dist" / name, "web/" + name)
        manifest = {"id": ident, "version": version, "runtime": "sd-web-1", "interface": 1,
                    "python": python, "files": files}
        (target / "module.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        if archives is not None:
            archives = Path(archives)
            archives.mkdir(parents=True, exist_ok=True)
            with ZipFile(archives / (ident + ".zip"), "w", ZIP_DEFLATED) as archive:
                for relative in ("module.json", *files):
                    archive.write(target / relative, (folder / relative).as_posix())
        print("{}：{} 个文件，{} bytes".format(ident, len(files), sum((target / name).stat().st_size for name in files)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    parser.add_argument("--version", default="0.9.1")
    parser.add_argument("--archives", type=Path)
    args = parser.parse_args()
    build(args.destination, args.version, args.archives)
