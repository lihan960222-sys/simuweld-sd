from pathlib import Path
import math

from fastapi.testclient import TestClient

from sfmarc_workbench.server import create_app


def test_actual_model_local_trial_api():
    source = Path.home() / "Downloads" / "C0002.dat"
    headers = {"X-Session-Token": "local-trial-test"}
    with TestClient(create_app("local-trial-test"), base_url="http://127.0.0.1:8765") as client:
        response = client.post("/api/model", headers=headers, data={"root": source.name},
                               files=[("files", (source.name, source.read_bytes(), "application/octet-stream"))])
        assert response.status_code == 200, response.text[:500]
        headers["X-Model-Id"] = response.json()["id"]
        response = client.post("/api/local-trial", headers=headers, json={"length_unit": "m"})
        assert response.status_code == 200, response.text[:500]
        metadata = response.json()
        path = metadata["paths"][0]
        spec = {"model_id": metadata["model_id"], "path_id": path["id"], "start_mm": 1., "end_mm": 6.,
                "box": {"min": metadata["bounds_mm"][0], "max": metadata["bounds_mm"][1]},
                "keep_whole": [row["name"] for row in metadata["components"] if row["keep_whole"]]}
        result = client.post("/api/local-trial/preview", headers=headers, json=spec)
        assert result.status_code == 200, result.text[:500]
        assert math.isclose(result.json()["selection"]["length_mm"], 5., rel_tol=0, abs_tol=1e-9)
        assert client.post("/api/local-trial/preview", headers=headers,
                           json={**spec, "model_id": "stale"}).status_code == 409
        assert client.post("/api/local-trial/preview", headers=headers,
                           json={**spec, "box": {"min": [0], "max": [1]}}).status_code == 422
        assert client.post("/api/local-trial/export", headers=headers,
                           json={"model_id": metadata["model_id"], "groups": []}).status_code == 422
        assert client.post("/api/model/clear", headers=headers).status_code == 200
        assert client.post("/api/local-trial/preview", headers=headers, json=spec).status_code == 409
