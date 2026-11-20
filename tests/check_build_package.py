from hashlib import sha256
from pathlib import Path
import sys


baseline = Path(sys.argv[1]).resolve()
package = Path(sys.argv[2]).resolve()
assert not (package / "work").exists(), "构建输出包含运行数据目录"
for folder in ("face_load", "weld_paths", "thermal_cycle", "local_trial"):
    source = baseline / "modules" / folder
    target = package / "modules" / folder
    expected = {path.relative_to(source) for path in source.rglob("*") if path.is_file()}
    actual = {path.relative_to(target) for path in target.rglob("*") if path.is_file()}
    assert actual == expected, folder
    for relative in expected:
        assert sha256((source / relative).read_bytes()).digest() == sha256((target / relative).read_bytes()).digest(), relative
for source in baseline.iterdir():
    if source.is_file() and source.suffix.lower() in (".md", ".txt"):
        assert source.read_bytes() == (package / source.name).read_bytes(), source.name
for source in (baseline / "_internal").rglob("*"):
    if source.is_file():
        assert (package / source.relative_to(baseline)).is_file(), source
assert (package / "SIMUWELD-SD-Web.exe").read_bytes() != (baseline / "SIMUWELD-SD-Web.exe").read_bytes()
assert (package / "_internal/LOCAL_TRIAL.md").is_file()
print("build-package-ok: 原四模块与根目录说明完全一致，旧内部资源齐全，新 EXE 已生成，未复制 work")
