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
    "local_trial": ["local_trial", "local_trial_export", "routes_local_trial", "local_shell_results",
                    "local_shell_equivalent", "local_result_archive", "local_abaqus_export", "local_conservative"],
    "inherent_strain": ["plate_material", "plate_geometry", "plate_thermal", "plate_shell", "contact_geometry", "plate_structure", "plate_inherent",
                        "plate_mechanics", "inherent_strain", "routes_inherent_strain"],
}
TEMPLATES = {"thermal_cycle": ["tc_create.py", "tc_export.py", "thermal_cycle_source.xml"],
             "local_trial": ["local_trial_paths.py", "local_trial_case.py", "local_results_export.py",
                             "shell_mapping.py", "prepare_abaqus_model.py", "local_shell_loading.for", "export_abaqus_preview.py"]}


def build(destination, version, archives=None, only=None):
    destination = Path(destination).resolve()
    selected = set(only or GROUPS)
    if selected - GROUPS.keys():
        raise ValueError("未知模块：" + ", ".join(sorted(selected - GROUPS.keys())))
    for ident, modules in GROUPS.items():
        if ident not in selected:
            continue
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
        if ident == 'local_trial':
            copy(ROOT / 'LOCAL_SHELL_GUIDE.txt', '使用说明.txt')
            copy(ROOT / 'THIRD_PARTY_LOCAL_SHELL.txt', '第三方组件.txt')
            dependencies = SOURCE / 'dependencies'
            for source in sorted(dependencies.rglob('*')):
                if source.is_file() and '__pycache__' not in source.parts and source.suffix != '.pyc':
                    copy(source, 'backend/dependencies/' + source.relative_to(dependencies).as_posix())
        if ident == 'inherent_strain':
            copy(ROOT / 'docs' / 'INHERENT_PREVIEW.md', '使用说明.md')
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
    parser.add_argument("--only", nargs="+", choices=GROUPS)
    args = parser.parse_args()
    build(args.destination, args.version, args.archives, args.only)
