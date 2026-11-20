import ast
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from fastapi.testclient import TestClient
import pytest

from sfmarc_workbench.server import create_app


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "src/sfmarc_workbench/templates/local_results_export.py"
spec = importlib.util.spec_from_file_location("local_results_export", TEMPLATE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_script_download_is_standalone_and_preserves_process_name():
    name = "局部算例 'A'"
    with TestClient(create_app("results-test"), base_url="http://127.0.0.1:8765") as client:
        assert client.post("/api/local-trial/results-script", json={}).status_code == 401
        response = client.post("/api/local-trial/results-script", json={"process_name": name},
                               headers={"X-Session-Token": "results-test"})
        assert response.status_code == 200
        tree = ast.parse(response.text)
        assert ast.literal_eval(tree.body[0].value) == name
        compile(response.text, "export_local_results.py", "exec")
        assert TEMPLATE.read_text(encoding="utf-8") in response.text
        assert client.post("/api/local-trial/results-script", json={"process_name": "A\nB"},
                           headers={"X-Session-Token": "results-test"}).status_code == 400


def test_time_mapping_rejects_duplicate_and_invalid_values():
    assert module.increment_times([0, 2, 9], [0, .2, .8]) == {0: 0., 2: .2, 9: .8}
    for numbers, times in [([], []), ([1, 1], [0, 1]), ([.5], [1]), ([1], [float("nan")])]:
        with pytest.raises(ValueError):
            module.increment_times(numbers, times)


def test_archive_preserves_real_files_and_refuses_overwrite():
    with TemporaryDirectory(dir=ROOT / "work", prefix="results-export-") as work:
        directory = Path(work) / "result"
        directory.mkdir()
        source = directory / TEMPLATE.name
        source.write_bytes(TEMPLATE.read_bytes())
        archive = module.pack_results(directory)
        with ZipFile(archive) as result:
            assert result.testzip() is None
            assert result.read(TEMPLATE.name) == TEMPLATE.read_bytes()
        with pytest.raises(FileExistsError):
            module.pack_results(directory)
        with pytest.raises(ValueError):
            module.checked_files(directory, ".unv")
