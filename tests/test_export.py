from dataclasses import replace

import numpy as np
import pytest

from sfmarc_workbench.deck import Deck, safe_path
from sfmarc_workbench.loads import LoadSpec, export_model, preview
from sfmarc_workbench.geometry import SurfaceModel


def test_pressure_and_manual_resultant(model):
    faces = model.select(int(np.argmax(model.areas)), 1)
    load = LoadSpec(faces=faces, magnitude=2, mode="inward", cases=[1], length_unit="m", force_unit="N", pressure_unit="MPa")
    inward = preview(model, load)
    outward = preview(model, replace(load, mode="outward"))
    assert np.allclose(inward["force"], -np.array(outward["force"]))
    manual = preview(model, replace(load, mode="manual", direction=[3, 0, -4]))
    assert np.allclose(manual["force"], np.array([0.6, 0, -0.8]) * inward["area"] * 2e6)
    with pytest.raises(ValueError):
        preview(model, replace(load, mode="manual", direction=[0, 0, 0]))
    with pytest.raises(ValueError):
        preview(model, replace(load, faces=[-1]))


def test_export_preserves_original_and_activates_only_requested_cases(model):
    original = dict(model.deck.files)
    cases = [1, len(model.deck.cases) - 1]
    load = LoadSpec(faces=[0, 1, 2], magnitude=1, mode="inward", cases=cases)
    files, report = export_model(model, [load])
    updated = Deck(files, report["root"])
    assert updated.mesh.elements == model.deck.mesh.elements
    assert updated.mesh.nodes == model.deck.mesh.nodes
    added = report["loads"][0]["labels"]
    for i, (before, after) in enumerate(zip(model.deck.cases, updated.cases)):
        assert len(after.label_lines) == len(before.label_lines) + (len(added) if i in cases else 0)
        old_labels = [model.deck.lines[j].text for j in before.label_lines]
        new_labels = [updated.lines[j].text for j in after.label_lines]
        assert new_labels[:len(old_labels)] == old_labels
    assert model.deck.files == original
    for path, edits in report["edits"].items():
        changed = files[report["root"] if path == model.deck.root else path]
        for edit in reversed(edits):
            start = edit["output_offset"]
            assert changed[start:start + len(edit["after"].encode("ascii"))] == edit["after"].encode("ascii")
            changed = changed[:start] + edit["before"].encode("ascii") + changed[start + len(edit["after"].encode("ascii")):]
        assert changed == original[path]


def test_path_traversal_rejected():
    for path in ["../outside.dat", "C:/outside.dat", "/etc/passwd", "a/../../outside.dat"]:
        with pytest.raises(ValueError):
            safe_path(path)


def test_model_units_are_consistent_across_saved_loads(model):
    load = LoadSpec(faces=[0], magnitude=2, mode="inward", cases=[1])
    result = preview(model, load)
    assert result["pressure"] == 2
    assert result["area_unit"] == "mm²"
    assert result["force_unit"] == "N"
    assert preview(model, replace(load, length_unit="m"))["pressure"] == 2e6
    with pytest.raises(ValueError, match="模型单位"):
        export_model(model, [load, replace(load, length_unit="m")])


def test_manual_traction_requires_verified_follow_setting(model):
    deck = model.deck
    marker = next(line.raw for line in deck.lines if line.text.strip().lower() == "extended")
    option = f"{'FOLLOW FOR':20}{1:10}{0:10}{0:10}\n".encode("ascii")
    raw = deck.files[deck.root].replace(marker, marker + option, 1)
    following = SurfaceModel(Deck({deck.root: raw}, deck.root))
    with pytest.raises(ValueError, match="FOLLOW FOR"):
        export_model(following, [LoadSpec(faces=[0], magnitude=1, mode="manual", cases=[1])])
