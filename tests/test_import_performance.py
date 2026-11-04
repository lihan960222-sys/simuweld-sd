import json
import os
from pathlib import Path

import numpy as np
import pytest

from sfmarc_workbench.deck import Deck
from sfmarc_workbench.geometry import SurfaceModel, integrate_face


DIRECTORY = Path(__file__).resolve().parents[1] / "work" / "import-performance"
SOURCE_DIRECTORY = Path(os.environ.get("SFMARC_TEST_IMPORT_DIRECTORY", "work/thermal-sample"))


@pytest.fixture(scope="module", params=["C0002.dat", "C0002-3.dat"])
def imported(request):
    source = SOURCE_DIRECTORY / request.param
    if not source.is_file():
        pytest.skip("需要实际 C0002.dat 和 C0002-3.dat")
    return source.stem, SurfaceModel(Deck({source.name: source.read_bytes()}, source.name))


def test_import_matches_saved_original_geometry(imported):
    stem, model = imported
    baseline = DIRECTORY / f"baseline-{stem}.npz"
    if not baseline.is_file():
        pytest.skip("需要优化前保存的实际模型几何数组")
    with np.load(baseline) as original:
        for name in original.files:
            current = getattr(model, name)
            assert current.dtype == original[name].dtype, name
            if name in ("areas", "area_vectors", "normals"):
                np.testing.assert_allclose(current, original[name], rtol=1e-13, atol=1e-15, err_msg=name)
            else:
                np.testing.assert_array_equal(current, original[name], err_msg=name)
    metadata = json.loads((DIRECTORY / f"baseline-{stem}.json").read_text(encoding="utf-8"))
    for name, original in metadata.items():
        assert json.loads(json.dumps(getattr(model, name))) == original, name


def test_import_matches_scalar_face_integration(imported):
    _, model = imported
    for index, face in enumerate(model.faces):
        area, vector = integrate_face(model.points[face])
        np.testing.assert_allclose(model.areas[index], area, rtol=1e-13, atol=1e-15)
        np.testing.assert_allclose(model.area_vectors[index], vector, rtol=1e-13, atol=1e-15)
