from pathlib import Path
from importlib.metadata import distributions

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

datas = [("dist", "web"), ("README.md", "."), ("THIRD_PARTY_NOTICES.md", "."), ("LICENSE", ".")]
if not Path("work/webview2/msedgewebview2.exe").is_file():
    raise FileNotFoundError("请将微软 x64 Fixed Version Runtime 解压至 work/webview2")
datas.append(("work/webview2", "webview2"))
datas.append(("THERMAL_CYCLE.md", "."))
datas.append(("IMPORT_PERFORMANCE.md", "."))
datas.append(("node_modules/three/LICENSE", "licenses/three"))
datas.append(("node_modules/lucide/LICENSE", "licenses/lucide"))
datas += collect_data_files("meshioplusplus")
datas.append(("src/sfmarc_workbench/templates", "sfmarc_workbench/templates"))
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
          console=False, debug=False, strip=False, upx=False)
web_exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name="SIMUWELD-SD-Web",
              icon="public/icon.ico", console=True, debug=False, strip=False, upx=False)
collect = COLLECT(exe, web_exe, analysis.binaries, analysis.datas, strip=False, upx=False, name="SIMUWELD-SD")
