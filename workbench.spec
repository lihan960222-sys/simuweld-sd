from pathlib import Path
from importlib.metadata import distributions

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

datas = [("dist", "web"), ("README.md", "."), ("THIRD_PARTY_NOTICES.md", "."), ("LICENSE", ".")]
datas.append(("node_modules/three/LICENSE", "licenses/three"))
datas += collect_data_files("meshioplusplus")
datas += copy_metadata("fortranformat")
for distribution in distributions():
    for file in distribution.files or []:
        if "license" in str(file).lower() or "notice" in str(file).lower():
            source = Path(distribution.locate_file(file))
            if source.is_file():
                datas.append((str(source), "licenses/" + distribution.metadata["Name"]))

analysis = Analysis(["run_app.py"], pathex=["src"], binaries=[], datas=datas,
                    hiddenimports=["meshioplusplus._core", "meshioplusplus._skin", "meshioplusplus._merge"],
                    excludes=["pytest", "numpy.tests", "scipy.tests", "pip"], noarchive=False)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name="SIMUWELD-SD",
          icon="public/icon.ico",
          console=True, debug=False, strip=False, upx=False)
collect = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name="SIMUWELD-SD")
