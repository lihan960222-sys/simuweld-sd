# SIMUWELD SD

在本机查看 sfMarc 有限元网格，按照表面夹角选择连续区域，定义压力或指定方向面力，导出增加载荷后的 DAT 文件。

模型文件仅在本机处理，原文件保留。浏览器与 Python 服务通过 `127.0.0.1` 通信，页面资源随程序提供。

0.2.0：支持围绕模型中心连续翻转，保留较短的拖动惯性；箭头根据选中区域面积缩放。界面采用浅色视窗、XY/XZ/YZ 平面按钮，实体可以分别设置颜色与半透明显示。

## Windows 使用

1. 解压整个程序目录，双击 `SIMUWELD-SD.exe`。保留同目录的 `_internal` 文件夹。
2. 浏览器会自动打开。选择 `.dat`；文件引用 INCLUDE 时选择包含相关文件的模型目录，并指定主 DAT。
3. 点击模型表面，调整相邻面最大夹角。选择方式支持替换、增加、移除；实体可以分别显示、选择颜色和启用半透明。
4. 输入载荷大小和单位，选择沿法向向内、向外，或者输入全局坐标中的方向分量。手动方向会自动归一化。
5. 确认原模型的长度与力单位，选择载荷生效阶段，预览箭头、面积与合力，保存载荷。
6. 可以继续添加载荷，也可以编辑或删除已有载荷。重叠区域在相同阶段按载荷求和。
7. 导出 ZIP，解压后使用名称带 `_loaded.dat` 的主文件。ZIP 同时包含导入的关联文件及 `sfmarc-loads-report.json`。

程序窗口关闭后，本机服务停止。浏览器页面关闭不会自动终止服务。

左键拖动旋转，右键拖动平移，滚轮缩放。旋转支持连续翻转；平移后仍围绕模型中心旋转。XY、XZ、YZ 按钮切换到相应坐标平面，适合窗口恢复完整模型视图。实体颜色和透明度仅影响当前显示。

## 输入与载荷定义

- 网格读取：meshioplusplus 17.11.0。使用其原始连接关系接口，保留节点、单元编号和退化单元的面号。
- 已实现的实体单元类型：7、134、136。type 7 的重复节点退化单元包含在处理范围内。
- 通过共享节点关系识别连续实体，坐标重合的不同节点不合并。只有外表面可以被选择。
- 沿法向的压力：TABLE 形式 `DIST LOADS`，normal pressure，正值向内、负值向外。
- 手动方向：general traction，分量为输入大小乘以单位方向向量，计量为力除以面积。
- 根据 Marc Volume C 的 FACE IDS 表确定局部面号，按局部面号组织原始单元列表。
- 在选定 LOADCASE 中增加新载荷标签，阶段内恒定；默认选择全部历史计算阶段。
- 法向随动行为保持原文件的 `FOLLOW FOR` 设置。预览表示初始几何上的方向与合力。
- 包含 `FOLLOW FOR` 时，手动方向面力的组合尚未验证，当前停止该组合的导出。
- 原始数据按字节保留，仅新增载荷定义及修改指定 LOADCASE 的数量和标签。修改记录能够恢复原文，用于核对。
- 中文阶段名称支持 UTF-8 与 GB18030/GBK，导出保留原始编码。
- 长度与力单位作用于整个模型；修改后，全部已保存载荷会重新换算，需要再次确认单位。
- INCLUDE 限于用户选定的文件组，不允许目录越界、循环引用、超过 10 层或同一文件重复引用。
- 单次导入文件组上限 512 MB、512 个文件。程序不读取未由用户选择的外部文件。

## 验证状态

程序测试与求解器验证分别记录。测试使用用户实际 sfMarc 文件及真实 Mentat 示例，检查网格、面积、合力、载荷阶段、原文保留以及实际浏览器操作。

2026-10-26 的检查结果：

- 实际 sfMarc 模型：163,924 个节点、88,452 个单元、7 个连续实体、153 个 LOADCASE；11 项测试通过。
- 公开 Mentat 示例：125 个节点、64 个单元；11 项测试通过。
- Windows 程序：已独立启动，并通过真实 Edge 浏览器的模型导入、点击选面、手动方向、载荷保存和下载检查；检查中没有外部网络请求。
- 原文件 SHA256 在检查前后保持一致。导出内容可重新读取，未编辑内容通过逐字节恢复核对。
- GitHub Actions 配置已提供，尚未在远程仓库执行。

2026-10-27 的 0.2.0 检查：6 项实际模型三维检查通过，覆盖连续翻转、平移后的旋转中心、调整窗口后的点击选面、箭头比例、颜色与透明度和显示深度精度；11 项公开 Mentat 示例检查通过。新版 Windows 程序通过实际模型导入、界面设置、选面、载荷保存、修改单位和下载检查，过程中没有外部网络请求。原文件 SHA256 保持一致。

实际文件验证覆盖 type 7。type 134、136 的面映射已实现，尚未使用对应的实际模型验证。

当前环境没有可用的 Marc/sfMarc 求解器，尚未验证求解器读取、新增载荷产生的反力或变形结果。材料文件与求解许可需要由模型原运行环境提供。原文件的外部材料引用会保留，并记录在导出清单中。

## 从源码运行

需要 Python 3.12 或更新版本、Node.js 22.12 或更新版本。开发环境首次安装需要网络；程序运行不访问外部服务。

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-lock.txt
.venv/Scripts/python.exe -m pip install -e ".[dev]"
npm ci
npm run build
.venv/Scripts/python.exe -m sfmarc_workbench.launcher
```

```powershell
$env:SFMARC_TEST_DAT = "D:/models/model.dat"
.venv/Scripts/python.exe -m pytest
```

项目的实际用户模型专项测试仅在对应模型输入下执行。公共测试样本通过固定 commit 下载到被忽略的 `work/`，文件摘要会被核对。

```powershell
.venv/Scripts/python.exe scripts/fetch_sample.py
$env:SFMARC_TEST_DAT = "$PWD/work/elemtype7_tension.dat"
.venv/Scripts/python.exe -m pytest
```

Windows 打包：

```powershell
./scripts/build.ps1 -Python .venv/Scripts/python.exe
```

三维显示检查使用实际模型和本机 Edge，验证惯性衰减、连续翻转、旋转中心、箭头比例、实体颜色与透明度，以及坐标平面、斜视和放大视图的渲染数值：

```powershell
$env:SFMARC_TEST_DAT = "D:/models/C0002-3.dat"
$env:SFMARC_TEST_PYTHON = "$PWD/.venv/Scripts/python.exe"
npm run test:viewer
```

## 维护与扩展

- `deck.py`：输入文件与 INCLUDE、LOADCASE 位置、原始数据。
- `geometry.py`：表面提取、Marc 面号、法向、积分与角度选择。
- `loads.py`：载荷数据、单位换算、合力与导出。
- `server.py`：本机 API、请求来源与会话校验。
- `frontend/viewer.ts`：三维显示、点击与高亮。
- `frontend/main.ts`：模型、选择与载荷操作界面。

新增二开功能应复用原始编号和表面数据。新增单元类型需要先核对 Marc 官方连接关系和面号，再使用真实输入验证。源码库不包含用户模型与计算结果。

本项目源码使用 MIT License。依赖许可与分发文件见 `THIRD_PARTY_NOTICES.md`。
