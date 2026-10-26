# sfMarc Workbench Implementation Plan

> **For agentic workers:** 使用 superpowers:executing-plans 在本次会话逐项实施，保持已有授权。

**Goal:** 本机读取 sfMarc，按角度选择表面，预览并导出压力与指定方向面力。

**Architecture:** Python 保存原文件字节并调用 meshioplusplus 读取网格，NumPy 计算表面和载荷。本机 FastAPI 提供数据，TypeScript 与 Three.js 提供交互，PyInstaller 打包 Windows 程序。

**Tech Stack:** Python 3.12、meshioplusplus 17.11.0、NumPy、SciPy、fortranformat、FastAPI、Three.js、TypeScript、Vite。

**Spec:** ../../design.md

## Global Constraints

- 模型文件仅在本机处理，原文件不覆盖。
- 用户已允许编写新增载荷及 LOADCASE 编辑所需的代码。
- 网格读取使用第三方库，保留原始节点与单元编号、退化单元连接关系。
- 注释与界面使用中文，identifier 使用英文。
- 验证使用实际输入文件与真实浏览器，不使用 mock，不使用截图。

## Review Focus

- 重复节点的 type 7 单元：导出面号必须对应原始连接关系。
- 夹角选择：实体之间的接触表面不能相互扩展。
- LOADCASE 标签：原有热源与初始条件不能丢失。
- 非 UTF-8 内容与 INCLUDE：导出后未编辑的字节必须一致。
- 本地服务：拒绝外部来源、无凭据的修改以及目录越界。

## Task 1: 读取模型和表面几何

Files: `src/sfmarc_workbench/deck.py`, `geometry.py`, `tests/test_model.py`。

Interfaces: `Deck(files: dict[str, bytes], root: str)` 保存文件与阶段；`SurfaceModel(deck)` 提供 points、faces、element_ids、local_faces、areas、normals、body_ids；`select(seed, angle)` 返回面索引。

- [x] 编写真实文件测试：163924 节点、88452 单元、type 7、有效面积与法向、闭合实体表面面积向量和为零。
- [x] 运行测试，确认功能缺失。
- [x] 实现第三方读取、INCLUDE 安全检查、原始面号映射、退化面处理与角度选择。
- [x] 运行 pytest，确认读取与几何检查通过。

## Task 2: 载荷计算与保留原文的导出

Files: `loads.py`, `deck.py`, `tests/test_export.py`。

Interfaces: `LoadSpec` 描述 faces、magnitude、mode、direction、cases 与单位；`preview(model, spec)` 返回面积和合力；`export_model(model, loads)` 返回文件组及修改记录。

- [x] 编写测试：压力正反方向、指定方向合力、未知面拒绝、阶段激活、原文恢复后逐字节一致。
- [x] 运行测试，确认功能缺失。
- [x] 实现 DIST LOADS 卡片生成、FORTRAN 字段输出、LOADCASE 标签维护和导出审计。
- [x] 运行全套测试，重新读取输出检查编号和网格一致。

## Task 3: 本地服务和交互界面

Files: `server.py`, `launcher.py`, `frontend/main.ts`, `frontend/viewer.ts`, `frontend/style.css`, `tests/test_server.py`, `tests/browser.spec.ts`。

Interfaces: `/api/model` 导入；`/api/mesh` 提供绘制数组；`/api/select` 选择；`/api/preview` 计算；`/api/export` 下载；所有 API 使用会话凭据。

- [x] 编写真实请求测试和浏览器导入、选面、载荷编辑、下载测试。
- [x] 运行测试，确认功能缺失。
- [x] 实现监听本机地址的服务、三维交互、实体显示、面选择、多载荷、阶段选择和单位换算。
- [x] 构建网页并在实际浏览器验证，检查无外部网络访问和控制台错误。

## Task 4: 打包与维护

Files: `README.md`, `CONTRIBUTING.md`, `THIRD_PARTY_NOTICES.md`, `.github/workflows/check.yml`, `workbench.spec`, `scripts/build.ps1`。

- [x] 执行完整测试和前端构建，记录实际模型验证数据。
- [x] 打包 Windows 程序并实际启动、执行本机请求。
- [x] 完成独立代码检查，修复影响功能的发现并运行回归检查。
- [x] 提供源码、启动程序、使用说明与明确的求解器验证状态。
