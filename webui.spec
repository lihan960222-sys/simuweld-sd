from importlib.metadata import distributions
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata


root = Path(SPECPATH)
datas = [(str(root / 'node_modules/three/LICENSE'), 'licenses/three'),
         (str(root / 'node_modules/lucide/LICENSE'), 'licenses/lucide')]
datas += collect_data_files('meshioplusplus')
datas += copy_metadata('fortranformat')
datas += copy_metadata('shapely')
for distribution in distributions():
    for file in distribution.files or []:
        if 'license' in str(file).lower() or 'notice' in str(file).lower():
            source = Path(distribution.locate_file(file))
            if source.is_file():
                datas.append((str(source), 'licenses/' + distribution.metadata['Name']))

analysis = Analysis(
    [str(root / 'run_app.py')],
    pathex=[str(root / 'src'), str(root / '.work/dependencies')],
    binaries=[], datas=datas,
    hiddenimports=['meshioplusplus._core', 'meshioplusplus._skin', 'meshioplusplus._merge',
                   'sfmarc_workbench.routes_inherent_strain'],
    excludes=['pytest', 'numpy.tests', 'scipy.tests', 'pip'], noarchive=False)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='SIMUWELD-SD-Web',
          icon=str(root / 'public/icon.ico'), console=True, debug=False, strip=False, upx=False)
collect = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name='SIMUWELD-SD')
