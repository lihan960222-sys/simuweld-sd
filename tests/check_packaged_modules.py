from io import BytesIO
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from zipfile import ZipFile

import httpx


root = Path(sys.argv[1]).resolve()
replacement = Path(sys.argv[2]).resolve()
exe = root / "SIMUWELD-SD-Web.exe"
before = sha256(exe.read_bytes()).hexdigest()
work = root / "work" / "module-acceptance"
work.mkdir(parents=True, exist_ok=True)
reports = []
for cycle in range(2):
    if cycle:
        shutil.copytree(replacement, root / "modules/local_trial", dirs_exist_ok=True)
    stdout, stderr = work / f"stdout-{cycle}.log", work / f"stderr-{cycle}.log"
    with stdout.open("w", encoding="utf-8") as out, stderr.open("w", encoding="utf-8") as err:
        process = subprocess.Popen([str(exe), "--no-browser"], cwd=root, stdout=out, stderr=err,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            endpoint = None
            for attempt in range(120):
                match = re.search(r"(http://127\.0\.0\.1:\d+)/#token=([^\s]+)", stdout.read_text(encoding="utf-8", errors="replace"))
                if match:
                    endpoint, token = match.groups()
                    break
                if process.poll() is not None:
                    raise RuntimeError(stderr.read_text(encoding="utf-8", errors="replace"))
                time.sleep(.25)
            assert endpoint, "程序未提供本机服务地址"
            with httpx.Client(base_url=endpoint, headers={"X-Session-Token": token}, timeout=180) as client:
                versions = client.get("/api/modules");versions.raise_for_status()
                assert len(versions.json()["versions"]) == 5
                assert "/shared/index.js" in client.get("/").text
                for ident in ("face_load", "weld_paths", "thermal_cycle", "local_trial"):
                    client.get(f"/modules/{ident}/{ident}.js").raise_for_status()
                    assert client.get(f"/modules/{ident}/../backend/module.json").status_code == 404
                source = Path.home() / "Downloads/C0002.dat"
                response = client.post("/api/model", data={"root": source.name},
                                       files=[("files", (source.name, source.read_bytes()))])
                response.raise_for_status();model = response.json()
                client.headers["X-Model-Id"] = model["id"]
                pressure = client.post("/api/preview", json={"faces": [0], "magnitude": .08,
                    "mode": "inward", "cases": [0], "length_unit": "m", "force_unit": "N", "pressure_unit": "MPa"})
                pressure.raise_for_status();assert pressure.json()["pressure"] == 80000
                response = client.post("/api/local-trial", json={"length_unit": "m"})
                response.raise_for_status();meta = response.json()
                path = meta["paths"][0]
                spec = {"model_id": meta["model_id"], "path_id": path["id"], "name": "模块覆盖验证",
                        "start_mm": 1, "end_mm": 6, "box": {"min": meta["bounds_mm"][0], "max": meta["bounds_mm"][1]},
                        "keep_whole": [item["name"] for item in meta["components"] if item["keep_whole"]]}
                response = client.post("/api/local-trial/export", json={"model_id": meta["model_id"], "groups": [spec]})
                response.raise_for_status()
                with ZipFile(BytesIO(response.content)) as archive:
                    assert "create_local_weld_paths.py" in archive.namelist()
                    assert any(name.endswith(".bdf") for name in archive.namelist())
                    config = json.loads(archive.read("local_trial.json"))
                    length = config["groups"][0]["selection"]["length_mm"]
                    assert math.isclose(length, 5, rel_tol=0, abs_tol=1e-9), length
                reports.append({"cycle": cycle, "versions": versions.json()["versions"], "nodes": model["nodes"], "export_bytes": len(response.content)})
        finally:
            process.terminate()
            process.wait(timeout=30)
assert sha256(exe.read_bytes()).hexdigest() == before
print(json.dumps({"same_exe": True, "runs": reports}, ensure_ascii=False))
