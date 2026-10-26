import os
import re
import json
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from fastapi.testclient import TestClient
import pytest

from sfmarc_workbench.server import create_app


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app("integration-token"), base_url="http://127.0.0.1:8765") as value:
        yield value


def test_rejects_untrusted_requests(client):
    assert client.get("/api/model").status_code == 401
    assert client.get("/api/model", headers={"X-Session-Token": "integration-token", "Origin": "https://example.com"}).status_code == 403
    assert client.get("/api/model", headers={"X-Session-Token": "integration-token", "Host": "other.example"}).status_code == 403


def test_real_upload_selection_preview_and_export(client):
    path = Path(os.environ["SFMARC_TEST_DAT"])
    headers = {"X-Session-Token": "integration-token"}
    raw = path.read_bytes()
    case = list(re.finditer(rb"(?im)^loadcase[^\r\n]*", raw))[1]
    raw = raw[:case.start()] + b"loadcase            " + "加载".encode("gbk") + raw[case.end():]
    result = client.post("/api/model", headers=headers, data={"root": path.name}, files=[("files", (path.name, raw, "application/octet-stream"))])
    assert result.status_code == 200, result.text[:500]
    info = result.json()
    assert info["nodes"] == (125 if path.name == "elemtype7_tension.dat" else 163924)
    assert info["cases"][1]["name"] == "加载"
    headers["X-Model-Id"] = info["id"]
    selection = client.post("/api/select", headers=headers, json={"seed": 0, "angle": 20}).json()
    assert 0 in selection["faces"]
    load = {"faces": selection["faces"], "magnitude": 2, "mode": "inward", "cases": [1]}
    assert client.post("/api/preview", headers=headers, json=load).json()["area"] > 0
    exported = client.post("/api/export", headers=headers, json={"loads": [load]})
    assert exported.status_code == 200
    assert exported.content[:2] == b"PK"
    with ZipFile(BytesIO(exported.content)) as archive:
        report = json.loads(archive.read("sfmarc-loads-report.json"))
        assert report["loads"][0]["cases"] == ["加载"]
        assert "加载".encode("gbk") in archive.read(report["root"])
    assert client.get("/api/mesh", headers={**headers, "X-Model-Id": "stale"}).status_code == 409
