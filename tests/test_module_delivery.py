import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("runtime_modules", ROOT / "runtime_modules.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


@pytest.fixture
def delivery():
    source = Path(os.environ["SFMARC_MODULE_ROOT"])
    work = ROOT / "work"
    work.mkdir(exist_ok=True)
    with TemporaryDirectory(dir=work, prefix="module-check-") as directory:
        target = Path(directory)
        shutil.copytree(source / "shared", target / "shared")
        shutil.copytree(source / "modules", target / "modules")
        yield target


def test_real_external_modules(delivery):
    files, versions = runtime.validate_modules(delivery)
    assert set(versions) == {"shared", *runtime.MODULES}
    assert "modules/local_trial/backend/local_trial_export.py" in files["sfmarc_workbench.local_trial_export"].as_posix()
    result = subprocess.run([sys.executable, str(ROOT / "tests" / "verify_module_import.py"), str(delivery)],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    assert "external-imports-ok" in result.stdout


def test_folder_overlay_and_old_files(delivery):
    folder = delivery / "modules" / "local_trial"
    original = Path(os.environ["SFMARC_MODULE_ROOT"]) / "modules" / "local_trial"
    shutil.copyfile(folder / "backend/local_trial.py", folder / "backend/obsolete.py")
    manifest_path = folder / "module.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["interface"] = 999
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="不兼容"):
        runtime.validate_modules(delivery)
    shutil.copytree(original, folder, dirs_exist_ok=True)
    assert (folder / "backend/obsolete.py").is_file()
    files, _ = runtime.validate_modules(delivery)
    with pytest.raises(ModuleNotFoundError, match="未声明"):
        runtime.ModuleFinder(files).find_spec("sfmarc_workbench.obsolete")
    subprocess.run([sys.executable, str(ROOT / "tests" / "verify_module_import.py"), str(delivery)],
                   cwd=ROOT, capture_output=True, text=True, check=True)


def test_incomplete_folder_stops(delivery):
    path = delivery / "modules/local_trial/web"
    source = next(path.glob("*.js"))
    source.unlink()
    with pytest.raises(ValueError, match="文件缺失"):
        runtime.validate_modules(delivery)
