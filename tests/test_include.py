from sfmarc_workbench.deck import Deck
from sfmarc_workbench.loads import LoadSpec, export_model
from sfmarc_workbench.geometry import SurfaceModel


def test_include_and_non_utf8_comment_preserve_actual_deck(model):
    raw = model.deck.files[model.deck.root]
    comment = "$ 材料和载荷\r\n".encode("gbk")
    wrapped = Deck({"root.dat": b"INCLUDE parts/model.dat\r\n", "parts/model.dat": comment + raw}, "root.dat")
    assert wrapped.mesh.elements == model.deck.mesh.elements
    imported = SurfaceModel(wrapped)
    files, report = export_model(imported, [LoadSpec(faces=[0], magnitude=1, mode="inward", cases=[1])])
    assert files["parts/model.dat"].startswith(comment)
    assert files[report["root"]] == b"INCLUDE parts/model.dat\r\n"
    reread = Deck(files, report["root"])
    assert reread.mesh.elements == wrapped.mesh.elements
