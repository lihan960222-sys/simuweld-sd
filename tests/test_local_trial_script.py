import importlib.util
import hashlib
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest


TEMPLATE = Path(__file__).parents[1] / "src/sfmarc_workbench/templates/local_trial_create.py"
spec = importlib.util.spec_from_file_location("local_trial_create", TEMPLATE)
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


def test_cut_surfaces_require_confirmation():
    config = {"groups": [{"name": "Local", "crop": {"components": [
        {"name": "Plate", "keep_whole": False, "element_ids": [10]},
        {"name": "Base", "keep_whole": True, "element_ids": [20]}]},
        "constraints": [{"name": "Fixed", "node_ids": [1], "axes": [True, True, True]}]}]}
    limitations = script.export_limitations(config)
    assert len(limitations) == 1
    assert "Plate" in limitations[0]


def test_preserved_geometry_has_no_cut_surface_limitation():
    config = {"groups": [{"name": "Local", "crop": {"components": [
        {"name": "Plate", "keep_whole": True, "element_ids": [10]}]}, "constraints": []}]}
    assert script.export_limitations(config) == []


def test_schedule_keeps_absolute_times_and_source_lead_follow():
    paths = [{"id": 1, "length_mm": 10.}, {"id": 2, "length_mm": 20.}]
    states = {1: {"robot": "Robot", "robot_start_s": 2., "lead_s": 1., "follow_s": 2.},
              2: {"robot": "Robot", "robot_start_s": 2., "lead_s": 3., "follow_s": 4.}}
    matches = [{"path_id": 1, "label": "First", "start_s": 5., "end_s": 10.,
                "source_start_mm": 0., "source_end_mm": 10.},
               {"path_id": 2, "label": "Second", "start_s": 20., "end_s": 25.,
                "source_start_mm": 5., "source_end_mm": 10.}]
    rows = script.schedule_matches(matches, paths, states)["Robot"]
    assert (rows[0]["pause_s"], rows[0]["lead_s"], rows[0]["follow_s"], rows[0]["end_s"]) == (2., 1., 2., 12.)
    assert (rows[1]["pause_s"], rows[1]["lead_s"], rows[1]["follow_s"], rows[1]["end_s"]) == (8., 0., 0., 25.)


def test_schedule_rejects_overlapping_source_times():
    paths = [{"id": 1, "length_mm": 10.}]
    states = {1: {"robot": "Robot", "robot_start_s": 2., "lead_s": 3., "follow_s": 0.}}
    matches = [{"path_id": 1, "label": "First", "start_s": 4., "end_s": 9.,
                "source_start_mm": 0., "source_end_mm": 10.}]
    with pytest.raises(ValueError, match="重叠"):
        script.schedule_matches(matches, paths, states)


def test_strain_setting_preserves_complete_parameter_document(tmp_path):
    source = Path("C:/Users/60066457/Downloads/source_parameters_before.xml")
    original = ET.parse(source)
    target = tmp_path / "strain_parameters.xml"
    script.enable_strain_output(source, target)
    changed = ET.parse(target)
    selected = "additional_result_output_settings/is_output_strain_tensors"
    assert original.getroot().find(selected).get("value") == "false"
    assert changed.getroot().find(selected).get("value") == "true"
    changed.getroot().find(selected).set("value", "false")
    assert ET.tostring(original.getroot()) == ET.tostring(changed.getroot())


def test_fixed_nodes_reference_and_update_use_real_xml():
    text = Path("C:/Users/60066457/Downloads/properties.xml").read_text(encoding="utf-8")
    script.validate_fixed_nodes_reference(text)
    root = ET.fromstring(text)
    constraint = {"name": "固定节点", "axes": [True, False, True], "nodes": [
        {"component": "PLATE2_20261023", "geometry": "PLATE2_20261023", "node_id": 486243}]}
    script.update_fixed_nodes_tree(root, constraint,
                                  {"PLATE2_20261023": ("PLATE2_20261023", "Geom-2-PLATE2X202610")})
    assert [root.find(axis + "_fixed_dof").text for axis in "xyz"] == ["true", "false", "true"]
    assert len(root.findall("fixed_displacements")) == 1
    assert root.find("fixed_displacements").attrib == {
        "node_Number": "486243", "model_display_name": "PLATE2_20261023", "model_internal_name": "Geom-2-PLATE2X202610"}
    assert root.find("name").get("internal_name") == "BC-1-X"


def test_fixed_nodes_reject_wrong_saved_geometry():
    root = ET.parse("C:/Users/60066457/Downloads/properties.xml").getroot()
    constraint = {"name": "固定节点", "axes": [True, True, True], "nodes": [
        {"component": "PLATE2_20261023", "geometry": "PLATE2_20261023", "node_id": 486243}]}
    with pytest.raises(ValueError, match="Geometry"):
        script.update_fixed_nodes_tree(root, constraint, {"PLATE2_20261023": ("Other", "Geom-Other")})


def test_coordinate_matching_uses_actual_mesh_ids_and_rejects_ambiguity():
    nodes = [(17, (1., 2., 3.)), (29, (4., 5., 6.))]
    samples = [{"node_id": 486243, "xyz_mm": (4., 5., 6.)}]
    assert script.match_node_positions(nodes, samples) == [(29, (4., 5., 6.))]
    with pytest.raises(ValueError, match="2 个"):
        script.match_node_positions(nodes + [(30, (4., 5., 6.))], samples)
    assert script.match_node_positions(nodes, [{"node_id": 1, "xyz_mm": (9., 9., 9.)}], allow_missing=True) == []
    with pytest.raises(ValueError, match="0 个"):
        script.match_node_positions(nodes, [{"node_id": 1, "xyz_mm": (9., 9., 9.)}])


@pytest.fixture
def saved_roof_project(tmp_path):
    source = Path("C:/Users/60066457/Downloads/RoofBrazing-Demo.zip")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    with zipfile.ZipFile(source) as archive:
        for member in archive.infolist():
            if member.filename.endswith((".xml", ".swproj")):
                destination = (tmp_path / member.filename).resolve()
                assert destination.is_relative_to(tmp_path.resolve())
                archive.extract(member, tmp_path)
    yield tmp_path / "RoofBrazing-Demo/RoofBrazing"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest


def test_saved_real_project_rejects_old_fixed_nodes_format(saved_roof_project):
    original = {path: path.read_bytes() for path in saved_roof_project.rglob("*.xml")}
    with pytest.raises(ValueError, match="FixedNodes XML"):
        script.patch_saved_constraints(saved_roof_project, {
            "Roof-Brazing-Half": [{"name": "Fixing-Right", "axes": [True, True, True], "nodes": []}]})
    assert all(path.read_bytes() == content for path, content in original.items())


def test_saved_real_project_resolves_process_component_and_geometry(saved_roof_project):
    selected = saved_roof_project / "Roof-Brazing-Half/Fixing-Right/properties.xml"
    previous = ET.parse(selected).getroot()
    node = previous.find("fixed_displacements")
    name = previous.find("name")
    current = ET.parse("C:/Users/60066457/Downloads/properties.xml")
    # 目录与节点引用采用真实示例项目，对象结构采用用户的实际 2026.2 文件。
    current.getroot().find("name").attrib = dict(name.attrib)
    current.write(selected, encoding="utf-8", xml_declaration=True)
    original = {path: path.read_bytes() for path in saved_roof_project.rglob("*.xml") if path != selected}
    constraint = {"name": "Fixing-Right", "axes": [True, False, True], "nodes": [{
        "component": "Side-outer-Right", "geometry": node.get("model_display_name"),
        "node_id": int(node.get("node_Number"))}]}
    script.patch_saved_constraints(saved_roof_project, {"Roof-Brazing-Half": [constraint]})
    changed = ET.parse(selected).getroot()
    assert len(changed.findall("fixed_displacements")) == 1
    assert changed.find("fixed_displacements").attrib == node.attrib
    assert changed.find("y_fixed_dof").text == "false"
    assert all(path.read_bytes() == content for path, content in original.items())


def test_saved_real_project_rejects_unregistered_fixed_nodes(saved_roof_project):
    selected = saved_roof_project / "Roof-Brazing-Half/Fixing-Right/properties.xml"
    selected.write_bytes(Path("C:/Users/60066457/Downloads/properties.xml").read_bytes())
    before = selected.read_bytes()
    with pytest.raises(ValueError, match="未唯一引用"):
        script.patch_saved_constraints(saved_roof_project, {
            "Roof-Brazing-Half": [{"name": "固定节点", "axes": [True, True, True], "nodes": []}]})
    assert selected.read_bytes() == before
