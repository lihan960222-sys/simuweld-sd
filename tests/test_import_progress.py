import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from sfmarc_workbench.server import create_app


SOURCE = Path(os.environ.get("SFMARC_TEST_TC_DAT", "work/thermal-sample/C0002.dat"))


@pytest.mark.skipif(not SOURCE.is_file(), reason="需要真实 C0002.dat")
def test_real_import_progress_counts():
    app = create_app("import-progress-test")
    samples = []
    with TestClient(app, base_url="http://127.0.0.1:8765",
                    headers={"X-Session-Token": "import-progress-test"}) as client:
        assert not client.get("/api/import-progress").json()["active"]
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(client.post, "/api/model", data={"root": SOURCE.name},
                                      files={"files": (SOURCE.name, SOURCE.read_bytes())})
            while not pending.done():
                response = client.get("/api/import-progress")
                assert response.status_code == 200
                samples.append(response.json())
                time.sleep(.05)
            imported = pending.result()
        assert imported.status_code == 200, imported.text
        final = client.get("/api/import-progress").json()
        assert not final["active"]
        assert final["completed"] == final["total"] == imported.json()["nodes"]
        assert any(item["active"] and item["total"] is None for item in samples)
        counts = [item for item in samples if item["total"] is not None and item["active"]]
        assert counts
        assert all(0 <= item["completed"] <= item["total"] for item in counts)
        assert any(item["unit"] in ("行", "个单元", "个面") for item in counts)
