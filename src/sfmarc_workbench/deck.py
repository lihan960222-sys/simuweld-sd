from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from hashlib import sha256
from io import StringIO
from pathlib import PurePosixPath
import re

from fortranformat import FortranRecordReader
from meshioplusplus.marc import _marc


def safe_path(name: str) -> str:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or ":" in name or not path.name:
        raise ValueError(f"文件路径不允许：{name}")
    return path.as_posix()


@dataclass(frozen=True)
class SourceLine:
    path: str
    number: int
    raw: bytes

    @property
    def text(self):
        value = self.raw.decode("utf-8", errors="surrogateescape")
        if any("\udc80" <= char <= "\udcff" for char in value):
            value = self.raw.decode("gb18030")
        return value.rstrip("\r\n").removeprefix("\ufeff")


@dataclass(frozen=True)
class LoadCase:
    name: str
    header: int
    count_line: int
    label_lines: tuple[int, ...]
    initial: bool


class Deck:
    def __init__(self, files: dict[str, bytes], root: str):
        self.files = {safe_path(k): v for k, v in files.items()}
        self.root = safe_path(root)
        if self.root not in self.files:
            raise ValueError("没有找到主 DAT 文件")
        self.source_lines = {k: v.splitlines(keepends=True) for k, v in self.files.items()}
        self.used_files = set()
        self.lines = []
        self._expand(self.root, ())
        text = [line.text for line in self.lines]
        self.extended = any(re.match(r"^extended\s*$", s, re.I) for s in text)
        self.iw = 10 if self.extended else 5
        self.kw = 20 if self.extended else 10
        if not _marc.is_marc_deck("\n".join(text[:200])):
            raise ValueError("文件开头不符合 Marc 输入格式")
        end_options = [i for i, s in enumerate(text) if re.match(r"^end\s+option\s*$", s, re.I)]
        if len(end_options) != 1:
            raise ValueError("需要且只能包含一个 END OPTION")
        self.end_option = end_options[0]
        self.table = any(re.match(r"^table(?:\s|,|$)", s, re.I) for s in text[:self.end_option])
        self.follow = [s.strip() for s in text if re.match(r"^follow\s+for(?:\s|,|$)", s, re.I)]
        messages = StringIO()
        # 固定依赖版本的原始连接关系接口保留退化单元的 Marc 面号。
        with redirect_stdout(messages), redirect_stderr(messages):
            self.mesh = _marc._parse_deck(text)
        if not self.mesh.nodes or not self.mesh.elements:
            raise ValueError("文件没有可读取的节点或单元")
        ids = [item[0] for item in self.mesh.elements]
        if len(ids) != len(set(ids)):
            raise ValueError("存在重复单元编号")
        self.cases = []
        for i, line in enumerate(text):
            if not re.match(r"^loadcase(?:\s|,|$)", line, re.I):
                continue
            name = line[8:].lstrip(" ,").strip()
            count_index = self.next_data(i + 1)
            value = text[count_index]
            count = int(value.split(",")[0]) if "," in value else FortranRecordReader(f"I{self.iw}").read(value)[0]
            if count is None or count < 0 or count > 100000:
                raise ValueError(f"阶段 {name} 的标签数量无效")
            labels = []
            cursor = count_index + 1
            for _ in range(count):
                cursor = self.next_data(cursor)
                labels.append(cursor)
                cursor += 1
            self.cases.append(LoadCase(name, i, count_index, tuple(labels), i < self.end_option))
        if not self.cases:
            raise ValueError("文件没有 LOADCASE，无法指定载荷作用阶段")
        initial = [c.header for c in self.cases if c.initial]
        self.insertion = min(initial) if initial else self.end_option
        self.digest = sha256(self.files[self.root]).hexdigest()
        self.materials = []
        self.missing_materials = []
        for i, line in enumerate(text[:self.end_option]):
            if re.match(r"^read\s+material(?:\s|,|$)", line, re.I):
                header = self.next_data(i + 1)
                entry = self.lines[self.next_data(header + 1)]
                filename = entry.text.strip()
                self.materials.append(filename)
                relative = PurePosixPath(entry.path).parent / filename.replace("\\", "/")
                if relative.as_posix() not in self.files:
                    self.missing_materials.append(filename)

    def next_data(self, index):
        while index < len(self.lines) and (not self.lines[index].text.strip() or self.lines[index].text.lstrip().startswith("$")):
            index += 1
        if index == len(self.lines):
            raise ValueError("输入文件在数据段中提前结束")
        return index

    def _expand(self, path, ancestors):
        if path in ancestors or len(ancestors) >= 10:
            raise ValueError(f"INCLUDE 循环引用或超过 10 层：{path}")
        if path not in self.files:
            raise ValueError(f"缺少 INCLUDE 文件：{path}；请使用选择模型目录")
        if path in self.used_files:
            raise ValueError(f"同一个 INCLUDE 文件被多次引用：{path}")
        self.used_files.add(path)
        for number, raw in enumerate(self.source_lines[path]):
            line = SourceLine(path, number, raw)
            target = _marc._include_target(line.text)
            if target is None:
                self.lines.append(line)
            else:
                target = safe_path(target)
                child = safe_path((PurePosixPath(path).parent / target).as_posix())
                self._expand(child, (*ancestors, path))
