# 第三方组件

程序使用 meshioplusplus、NumPy、SciPy、fortranformat、FastAPI、Starlette、Uvicorn、Pydantic、python-multipart、Three.js 及它们的依赖。安装版本由依赖文件记录。

meshioplusplus Python 源码采用 MIT。其发布的二进制包含 OpenCASCADE，许可为 `MIT AND (LGPL-2.1-only WITH OCCT-exception-1.0)`。Windows 程序附带组件的原始许可与说明，位于 `_internal/licenses/`。保留这些文件；组件原始说明包含源码构建信息。

测试样本 `elemtype7_tension.dat` 来自 DAMASK 的实际 Mentat 测试资源，固定 commit 为 `7f874ec168cf57b2ced3f8293d0598464131cc48`。DAMASK 使用 AGPL 许可。下载脚本在开发者本机获取该文件，样本没有加入本项目源码或程序分发。
