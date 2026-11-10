from pathlib import Path
from importlib.metadata import distributions

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

datas = [("README.md", "."), ("THIRD_PARTY_NOTICES.md", "."), ("LICENSE", "."),
         ("LOCAL_TRIAL.md", "."), ("THERMAL_CYCLE.md", "."),
         ("node_modules/three/LICENSE", "licenses/three"), ("node_modules/lucide/LICENSE", "licenses/lucide")]
datas += collect_data_files("meshioplusplus")
datas += copy_metadata("fortranformat")
for distribution in distributions():
    for file in distribution.files or []:
        if "license" in str(file).lower() or "notice" in str(file).lower():
            source = Path(distribution.locate_file(file))
            if source.is_file():
                datas.append((str(source), "licenses/" + distribution.metadata["Name"]))

business_modules = ["launcher", "server", "deck", "geometry", "background", "thermal_deck",
                    "loads", "welding", "weld_script", "thermal", "sampling", "local_trial", "local_trial_export",
                    "routes_face_load", "routes_weld_paths", "routes_thermal_cycle", "routes_local_trial"]
analysis = Analysis(["run_app.py"], pathex=["src"], binaries=[], datas=datas,
                    hiddenimports=["meshioplusplus._core", "meshioplusplus._skin", "meshioplusplus._merge"]
                                  + ["sfmarc_workbench." + name for name in business_modules],
                    excludes=["pytest", "numpy.tests", "scipy.tests", "pip"], noarchive=False)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name="SIMUWELD-SD-Web",
          icon="public/icon.ico", console=True, debug=False, strip=False, upx=False)
collect = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name="SIMUWELD-SD")
