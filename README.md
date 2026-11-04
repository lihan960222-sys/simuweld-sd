# SIMUWELD SD

本机运行的 Simufact Welding 辅助工具，提供模型查看、面载荷编辑和焊路脚本生成。模型文件在本机处理，原文件保留。

[下载 Windows 版本](https://github.com/lihan960222-sys/simuweld-sd/releases/latest) · [反馈问题](https://github.com/lihan960222-sys/simuweld-sd/issues) · [参与开发](CONTRIBUTING.md)

## 功能

- **模型查看**：支持 DAT、INP、BDF、NAS、STP、STEP，提供实体树、颜色与透明度、网格边线、正交与透视视图、爆炸图。
- **面载荷**：在 Marc/sfMarc DAT 模型上按角度选择表面，设置压力或指定方向的面力，导出新的 DAT 文件组。
- **生成焊路**：导入 CSV 路径点，创建热源、分配焊路、调整焊接顺序与停止时间，预览焊接动画，导出 Simufact Python 脚本。
- **转换至热循环模式**：设置焊路采样并预览真实节点，导出温度提取与热循环创建脚本，支持最大 10 GB 温度 CSV。

## 快速开始

1. 从 [Releases](https://github.com/lihan960222-sys/simuweld-sd/releases/latest) 下载 Windows 压缩包。
2. 完整解压到本机磁盘，选择启动方式：`SIMUWELD-SD.exe` 打开桌面窗口，`SIMUWELD-SD-Web.exe` 打开默认浏览器。请保留同目录的 `_internal` 文件夹。
3. 导入模型并选择文件单位制，通过顶部入口使用各项功能。

CSV 与模型需使用相同坐标系。焊接动画用于检查路径与热源位置，实际计算在 Simufact/Marc 中完成。导出文件与脚本尚未在求解环境中验证。

离线完整包自带 WebView2，包含两种启动方式；Web UI 包使用电脑已有浏览器。Web UI 运行时保留启动窗口，关闭该窗口即可退出服务。

## 从源码运行

需要 Windows、Python 3.12+ 和 Node.js 22.12+。
桌面运行及打包前执行 `powershell -File scripts/fetch_webview2.ps1`，下载固定运行环境。Web UI 开发可直接使用 `--web` 参数启动。

```powershell
git clone https://github.com/lihan960222-sys/simuweld-sd.git
cd simuweld-sd
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-lock.txt
.venv/Scripts/python.exe -m pip install -e ".[dev]"
npm ci
npm run build
.venv/Scripts/python.exe -m sfmarc_workbench.launcher
```

开发与验证要求见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可

[MIT License](LICENSE) · [第三方组件说明](THIRD_PARTY_NOTICES.md)

Copyright © LH
