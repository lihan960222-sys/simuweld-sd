from io import BytesIO
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from zipfile import ZipFile

import httpx
import numpy as np


root = Path(sys.argv[1]).resolve()
replacement = Path(sys.argv[2]).resolve()
exe = root / "SIMUWELD-SD-Web.exe"
before = sha256(exe.read_bytes()).hexdigest()
work = root / "work" / "module-acceptance"
work.mkdir(parents=True, exist_ok=True)
reports = []


def wait_calculation(client, job):
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        response = client.get('/api/inherent-strain/status', params={'job_id': job})
        response.raise_for_status()
        state = response.json()
        if state['status'] not in ('running', 'cancelling'):
            return state
        time.sleep(.1)
    raise TimeoutError('固有应变法后台任务未在规定时间完成')


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
                assert len(versions.json()["versions"]) == 6
                assert "/shared/index.js" in client.get("/").text
                for ident in ("face_load", "weld_paths", "thermal_cycle", "local_trial", "inherent_strain"):
                    client.get(f"/modules/{ident}/{ident}.js").raise_for_status()
                    assert client.get(f"/modules/{ident}/../backend/module.json").status_code == 404
                source = Path(os.environ['SFMARC_TEST_C0002_DAT'])
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
                report = {"cycle": cycle, "versions": versions.json()["versions"], "nodes": model["nodes"], "export_bytes": len(response.content)}
                if cycle == 0:
                    spec = {'model_id': model['id'], 'revision': 1, 'length_unit': 'm', 'spacing_mm': .5}
                    response = client.post('/api/inherent-strain/prepare', json=spec)
                    response.raise_for_status()
                    job = response.json()['job_id']
                    state = wait_calculation(client, job)
                    assert state['status'] == 'ready', state
                    response = client.get('/api/inherent-strain/grid', params={'job_id': job})
                    response.raise_for_status()
                    grid = response.json()
                    assert grid['cell_count'] > 100 and len(grid['paths']) == 4 and len(grid['layers']) == 2
                    material = Path(os.environ['SFMARC_TEST_MATERIAL'])
                    response = client.post('/api/inherent-strain/material', data={'model_id': model['id'], 'revision': 1},
                                           files={'material': (material.name, material.read_bytes())})
                    response.raise_for_status()
                    response = client.post('/api/inherent-strain/start', json={
                        'model_id': model['id'], 'revision': 1, 'job_id': job,
                        'end_s': .003, 'dt_s': .001, 'record_interval_s': .001})
                    response.raise_for_status()
                    state = wait_calculation(client, job)
                    assert state['status'] == 'complete' and state['steps'] == 3, state
                    frames = []
                    for index in range(state['frames']):
                        response = client.get('/api/inherent-strain/frame', params={'job_id': job, 'index': index})
                        response.raise_for_status()
                        frames.append(response.json())
                    response = client.get('/api/inherent-strain/export', params={'job_id': job})
                    response.raise_for_status()
                    with ZipFile(BytesIO(response.content)) as archive:
                        metadata = json.loads(archive.read('parameters.json'))
                        assert not metadata['full_weld_completed'] and metadata['status'] == 'complete'
                        with np.load(BytesIO(archive.read('temperature.npz')), allow_pickle=False) as data:
                            np.testing.assert_array_equal(data['temperature_c'], [frame['temperature_c'] for frame in frames])
                            np.testing.assert_array_equal(data['times_s'], [frame['time_s'] for frame in frames])
                            assert np.isfinite(data['temperature_c']).all()
                    report['thermal'] = {'cells': grid['cell_count'], 'layers': len(grid['layers']),
                                         'paths': len(grid['paths']), 'time_s': state['time_s'],
                                         'steps': state['steps'], 'frames': state['frames'], 'export_matches_frames': True}
                reports.append(report)
        finally:
            process.terminate()
            process.wait(timeout=30)
assert sha256(exe.read_bytes()).hexdigest() == before
print(json.dumps({"same_exe": True, "runs": reports}, ensure_ascii=False))
