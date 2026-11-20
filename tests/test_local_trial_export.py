from io import BytesIO, StringIO
import json
from pathlib import Path
from zipfile import ZipFile
import ast

import numpy as np
import pytest
from meshioplusplus.marc import _marc
from meshioplusplus.nastran import read

from sfmarc_workbench.deck import Deck
from sfmarc_workbench.geometry import SurfaceModel
from sfmarc_workbench.local_trial import LocalTrial, _arcs, _slice
from sfmarc_workbench.local_trial_export import export_local_trial
from reference_data import reference_path


@pytest.fixture(scope="module")
def trial():
    source = reference_path('SFMARC_TEST_C0002_DAT')
    return LocalTrial(SurfaceModel(Deck({source.name: source.read_bytes()}, source.name)), "m")


def configuration(trial):
    path = trial.plan["paths"][0]
    points = np.asarray(path["points_mm"])
    length = _arcs(points)[-1]
    coords, _, _ = _slice(points, np.asarray(path["normals"]), length * .3, length * .6)
    spec = {"name": "真实模型局部试算", "model_id": trial.model_id,
            "path_id": path["id"], "start_mm": length * .3, "end_mm": length * .6,
            "box": {"min": (coords.min(axis=0) - 2).tolist(), "max": (coords.max(axis=0) + 2).tolist()},
            "constraints": [], "trim_start_mm": .1, "trim_end_mm": .1,
            "keep_whole": [name for name in trial.components if name.lower() == "base"]}
    return {"model_id": trial.model_id, "groups": [spec], "cooling_limit_c": 100., "max_cooling_s": 100.}


def test_actual_zip_bdf_roundtrip_and_config(trial):
    data = configuration(trial)
    preview = trial.preview(data["groups"][0])
    with ZipFile(BytesIO(export_local_trial(trial, data))) as archive:
        config = json.loads(archive.read("local_trial.json"))
        group = config["groups"][0]
        assert config["source_sha256"] == trial.model.deck.digest
        assert config["source_paths"] == trial.plan["paths"]
        assert "fixed_nodes_reference_xml" not in config
        assert "create_local_case.py" in archive.namelist()
        assert "case_manifest.json" in archive.namelist()
        compile(archive.read('create_local_case.py'), 'create_local_case.py', 'exec')
        assert group["matches"] == [row for row in preview["matches"] if row["matched"]]
        assert group["selection"] == preview["selection"]
        for field in ("node_ids", "element_ids", "points_mm", "faces", "cut_faces"):
            assert group["crop"][field] == preview["crop"][field]
        files = []
        for component in group["crop"]["components"]:
            if "mesh_file" not in component:
                assert component["keep_whole"] or not component["element_ids"]
                continue
            files.append(component["mesh_file"])
            mesh = read(StringIO(archive.read(component["mesh_file"]).decode("utf-8")))
            ids = component["bdf_node_original_ids"]
            expected_points = trial.points[[trial.node_index[node] for node in ids]]
            assert len(mesh.points) == len(component["node_ids"])
            assert np.allclose(mesh.points, expected_points, atol=1e-9, rtol=0)
            actual = [(block.type, tuple(ids[int(index)] for index in row))
                      for block in mesh.cells for row in block.data]
            expected = []
            for ident in component["bdf_element_original_ids"]:
                kind, nodes, _ = trial.elements[ident]
                if kind == 7:
                    cell_type, nodes = _marc._collapse_solid(list(nodes))
                else:
                    cell_type = {134: "tetra", 136: "wedge"}[kind]
                expected.append((cell_type, tuple(nodes)))
            assert actual == expected
            assert len(actual) == len(component["element_ids"])
        assert files
        namespace = {"__name__": "local_trial_export_verification"}
        script = archive.read("create_local_weld_paths.py")
        exec(compile(script, "create_local_weld_paths.py", "exec"), namespace)
        lines = namespace["weld_lines"](namespace["CONFIG"])
        assert len(lines) == len(group["matches"])
        assert [line["points_mm"] for line in lines] == [row["points_mm"] for row in sorted(group["matches"], key=lambda row: row["start_s"])]
        for forbidden in (b"all_components", b"save_as", b"new_trajectory", b"process_parameters", b"import_geometry"):
            assert forbidden not in script


def test_actual_export_rejects_invalid_model(trial):
    data = configuration(trial)
    data["model_id"] = "outdated"
    with pytest.raises(ValueError, match="模型已更新"):
        export_local_trial(trial, data)
    data = configuration(trial)
    data["groups"][0]["model_id"] = "outdated-group"
    with pytest.raises(ValueError, match="试算组的来源模型已更新"):
        export_local_trial(trial, data)


def test_actual_export_kept_components_have_mesh(trial):
    data = configuration(trial)
    data["groups"][0]["keep_whole"] = list(trial.components)
    with ZipFile(BytesIO(export_local_trial(trial, data))) as archive:
        config = json.loads(archive.read("local_trial.json"))
        for component in config["groups"][0]["crop"]["components"]:
            assert component["mesh_file"] in archive.namelist()
            assert component["element_ids"] == trial.component_elements[component["name"]]


def test_actual_export_validates_script_config_before_download(trial):
    data = configuration(trial)
    data["groups"][0]["name"] = " "
    with pytest.raises(ValueError, match="试算名称"):
        export_local_trial(trial, data)


def test_actual_case_manifest_keeps_local_welding_intervals(trial):
    data = configuration(trial)
    with ZipFile(BytesIO(export_local_trial(trial, data))) as archive:
        config = json.loads(archive.read('case_manifest.json'))
        group = config['groups'][0]
        assert group['events']
        origin = min(row['start_s'] for row in group['matches'])
        for event, match in zip(group['events'], sorted(group['matches'], key=lambda row: row['start_s'])):
            assert event['start_s'] == pytest.approx(match['start_s'] - origin)
            assert event['end_s'] == pytest.approx(match['end_s'] - origin)
        assert config['cooling_limit_c'] == data['cooling_limit_c']


def test_exported_case_script_clears_geometry_before_replacement(trial):
    with ZipFile(BytesIO(export_local_trial(trial, configuration(trial)))) as archive:
        tree = ast.parse(archive.read('create_local_case.py'))
    assignments = sorted((node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Attribute) and target.attr == 'geometry'
                                  and isinstance(target.value, ast.Name) and target.value.id == 'body'
                                  for target in node.targets)), key=lambda node: node.lineno)
    assert len(assignments) == 2, '组件几何替换需要明确解除原关联，再指定新几何。'
    assert isinstance(assignments[0].value, ast.Constant) and assignments[0].value.value is None
    assert isinstance(assignments[1].value, ast.Name) and assignments[1].value.id == 'geometry'
    removals = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute) and node.func.attr == 'delete_geometry']
    assert len(removals) == 1
    assert isinstance(removals[0].args[0], ast.Name) and removals[0].args[0].id == 'imported_geometry'
