import importlib.abc
import importlib.machinery
import importlib.util
import json
from hashlib import sha256
from pathlib import Path, PurePosixPath
import sys


MODULES = ("face_load", "weld_paths", "thermal_cycle", "local_trial", "inherent_strain")
RUNTIME = "sd-web-1"
INTERFACE = 1


class ModuleSource(importlib.machinery.SourceFileLoader):
    def get_code(self, fullname):
        # 覆盖目录后始终读取清单中的源码，忽略旧字节码。
        return self.source_to_code(self.get_data(self.path), self.path)


class ModuleFinder(importlib.abc.MetaPathFinder):
    def __init__(self, files):
        self.files = files

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "sfmarc_workbench.templates":
            return importlib.machinery.ModuleSpec(fullname, None, is_package=True)
        if fullname in self.files:
            source = self.files[fullname]
            loader = ModuleSource(fullname, str(source))
            return importlib.util.spec_from_file_location(fullname, source, loader=loader,
                submodule_search_locations=[str(source.parent)] if source.name == "__init__.py" else None)
        if fullname == "sfmarc_workbench" or fullname.startswith("sfmarc_workbench."):
            raise ModuleNotFoundError("模块清单未声明 Python 文件：" + fullname)
        return None


def checked_path(directory, name):
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
        raise ValueError("模块文件路径无效：" + name)
    result = (directory / name).resolve()
    if not result.is_relative_to(directory.resolve()):
        raise ValueError("模块文件超出目录：" + name)
    return result


def validate_modules(root):
    root = Path(root).resolve()
    files, versions = {}, {}
    for ident in ("shared", *MODULES):
        directory = root / ("shared" if ident == "shared" else "modules/" + ident)
        manifest = json.loads((directory / "module.json").read_text(encoding="utf-8"))
        if (manifest["id"] != ident or manifest["runtime"] != RUNTIME
                or manifest["interface"] != INTERFACE):
            raise ValueError("{} 与当前基础程序不兼容，请使用配套模块目录。".format(ident))
        if not manifest["version"] or not manifest["files"] or not manifest["python"]:
            raise ValueError("模块清单不完整：" + ident)
        for name, digest in manifest["files"].items():
            source = checked_path(directory, name)
            if not source.is_file() or sha256(source.read_bytes()).hexdigest() != digest:
                raise ValueError("{} 文件缺失或不完整：{}；请重新覆盖完整模块目录。".format(ident, name))
        for fullname, name in manifest["python"].items():
            if (not fullname.startswith("sfmarc_workbench.") and fullname != "sfmarc_workbench"
                    or name not in manifest["files"] or not name.endswith(".py") or fullname in files):
                raise ValueError("模块 Python 入口无效或重复：" + fullname)
            files[fullname] = checked_path(directory, name)
        versions[ident] = manifest["version"]
    return files, versions


def install(root):
    if any(name == "sfmarc_workbench" or name.startswith("sfmarc_workbench.") for name in sys.modules):
        raise RuntimeError("外部模块必须在启动业务代码之前加载。")
    files, versions = validate_modules(root)
    sys.meta_path.insert(0, ModuleFinder(files))
    sys._sd_root = Path(root).resolve()
    sys._sd_versions = versions
    sys.dont_write_bytecode = True
