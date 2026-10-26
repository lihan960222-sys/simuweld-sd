from hashlib import sha1
from pathlib import Path
from urllib.request import urlopen

url = "https://raw.githubusercontent.com/damask-multiphysics/DAMASK/7f874ec168cf57b2ced3f8293d0598464131cc48/tests/integration/resources/Marc_element_lib/elemtype7_tension.dat"
target = Path("work/elemtype7_tension.dat")
target.parent.mkdir(parents=True, exist_ok=True)
with urlopen(url, timeout=60) as response:
    data = response.read()
digest = sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
if digest != "ca44ffc9805d9f7e0069b9cd2574fae27297a029":
    raise ValueError("示例文件摘要与固定版本不一致")
target.write_bytes(data)
print(target.resolve())
