# 生成焊路 Implementation Plan

> 使用 superpowers:executing-plans 在当前会话执行，按照用户已确认的交互完成代码与验证。

**Goal:** 导入网格或 STEP 几何背景和 CSV 路径点，编辑完整激光热源参数，实时显示按比例尺寸图，按用户排列的焊路及焊后停止时间播放焊枪动画，并导出独立 Python 脚本。

**Architecture:** Python 标准库 csv 读取坐标，沿原有本机 API 返回路径。生成脚本内置坐标并使用用户参考文件中的 Simufact API。独立功能面板保存编辑状态，Viewer 共用模型坐标与相机。

**Technologies:** Python、csv、Pydantic、FastAPI、TypeScript、Three.js、Playwright。

**Spec:** 用户本会话确认：按顺序选择焊路，每条包含路径点和焊后停止时间；只导出 Python，暂不修改 DAT。

## Global Constraints

- 本机处理，保留原文件和历史版本，不上传。
- CSV 坐标量化，鼠标只选择 CSV 原有点；CSV 与模型单位分别指定。
- 按点序选择整条或起终点之间所有点，支持反向；同一路径可多次添加。
- Process 必须存在，热源使用新名称，Robot 必须为空或使用新名称。热源全部参数由网页设置，并在 Simufact 中读取核对。
- 代码编辑使用 apply_patch；测试使用实际输入与真实浏览器，不使用 mock。

## Review Focus

- 路径交叉时依据 CSV 路径编号和点序识别，不通过距离猜测连接。
- 空值、重复点序、相邻重合点、非有限数值和负停止时间在入口拒绝。
- 修改模型长度单位只改变焊路显示换算，导出坐标保持所选物理长度。
- 导入 CSV 失败或取消时保留已添加焊路；功能切换保留编辑状态。
- 导出 Python 包含正确顺序和停止时间，名称安全转义，原坐标完整保留。

### Task 1: CSV 与脚本

**Files:** 新增 `src/sfmarc_workbench/welding.py`、`weld_script.py`、`tests/test_welding.py`；修改 `server.py`。

**Interfaces:** `/api/weld-paths` 接收多份 CSV 和单位，返回带 `name/points` 的路径；点包含 `order/node_id/xyz`，xyz 为 mm。`/api/weld-script` 接收 `process/robot/heat_source/orientation/paths`，每条包含 `name/points/pause_after`，返回 Python。

- [x] 编写真实参考 CSV 读取、非法 CSV、单位换算、反向点与时间导出的测试。
- [x] 执行测试，确认新 API 缺失导致失败。
- [x] 使用 csv.DictReader 与 Pydantic 实现入口验证，使用参考 API 生成内置数据脚本。
- [x] 编译并执行生成脚本的纯数据验证函数；本地没有 Simufact 时明确记录 API 执行限制。

### Task 2: 页面与三维交互

**Files:** 新增 `frontend/welding.ts`；修改 `main.ts`、`viewer.ts`、`style.css`、`tests/browser.spec.ts`、`tests/viewer.spec.ts`。

**Interfaces:** Viewer 接收 mm 换算后的路径点，支持显示、点击已有点、路径居中。功能切换控制路径可见状态，模型导入或单位变化刷新路径坐标。

- [x] 编写实际浏览器 CSV 导入、整条添加、范围选择、顺序调整、停止时间、编辑与脚本下载测试。
- [x] 确认功能入口缺失导致失败。
- [x] 实现模块面板、顺序列表、原有点选择及单位换算，保留面载荷操作。
- [x] 执行构建、完整后端测试、浏览器与三维检查。

### Task 3: 本机发布文件

**Files:** 版本文件、README、Windows 打包输出。

- [x] 更新版本与使用说明，说明 CSV 格式、PauseEnd 和 Simufact 验证状态。
- [x] 构建 Windows 程序，并在打包程序上运行浏览器流程。
- [ ] 保留历史文件，新建当前版本 Windows 与源码 ZIP，核对原文件摘要。

### Task 4: 几何背景、热源与动画

- 使用已安装的 meshioplusplus 读取 INP/BDF，使用其 OpenCASCADE 后端读取 STEP，复用实体颜色、透明度和显示设置。
- `HeatParameters` 与网页表单包含功率、速度、效率、全部圆锥与表面参数；尺寸示意图使用统一比例。
- `WeldTimeline` 按累计路径长度计算位置，按速度计算焊接时间，并包括每条焊路的停止时间。
- Three.js 焊枪、圆锥和 PointLight 使用同一组参数与坐标，预览倍速不改变导出数据。
- API 接口依据本机 Simufact Python 文档及用户脚本核对。使用真实库生成并读取标准网格和 STEP 样本，执行 API、浏览器与渲染数值检查。
- 激光焊接 SVG 标识用于网页与 Windows 图标；实体开关保持相机状态，回到中心按钮重新取景。

验证记录：焊路与几何背景 20 项后端检查、原模型 12 项后端检查、16 项三维检查通过。源码服务和 Windows 程序分别通过 4 项完整浏览器流程。生成脚本完成编译和参数检查；Simufact 内创建对象与计算尚未执行。
