import os
from pathlib import Path

import pytest


def reference_path(variable):
    value = os.environ.get(variable)
    if not value:
        pytest.skip(f'需要通过 {variable} 指定真实测试数据', allow_module_level=True)
    path = Path(value).resolve()
    if not path.exists():
        raise FileNotFoundError(f'{variable} 指定的路径不存在：{path}')
    return path
