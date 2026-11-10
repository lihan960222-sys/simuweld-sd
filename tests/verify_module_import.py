from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_modules import install

root = Path(sys.argv[1]).resolve()
install(root)

from sfmarc_workbench import loads, local_trial, local_trial_export, thermal, welding
from sfmarc_workbench.server import create_app

for module, name in ((loads, "face_load"), (welding, "weld_paths"),
                     (thermal, "thermal_cycle"), (local_trial, "local_trial"), (local_trial_export, "local_trial")):
    assert Path(module.__file__).is_relative_to(root / "modules" / name)
app = create_app("module-verification")
paths = {route.path for route in app.routes}
assert {"/api/export", "/api/weld-script", "/api/thermal/create", "/api/local-trial/export"}.issubset(paths)
print("external-imports-ok")
