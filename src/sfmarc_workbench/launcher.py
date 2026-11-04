import argparse
import logging
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser

import uvicorn
import webview

from .server import create_app


def main():
    parser = argparse.ArgumentParser(description="SIMUWELD SD 本机模型工具")
    parser.add_argument("--port", type=int, default=0)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--no-browser", action="store_true")
    mode.add_argument("--web", action="store_true", help="使用默认浏览器打开 Web UI")
    args = parser.parse_args()
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
    scratch = base / "work" / "session"
    scratch.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(scratch)
    listener = socket.socket()
    listener.bind(("127.0.0.1", args.port))
    listener.listen(128)
    port = listener.getsockname()[1]
    token = secrets.token_urlsafe(32)
    url = f"http://127.0.0.1:{port}/#token={token}"
    logging.basicConfig(filename=scratch / "application.log", encoding="utf-8", level=logging.WARNING)
    server = uvicorn.Server(uvicorn.Config(create_app(token), log_level="warning", log_config=None,
                                         host="127.0.0.1", port=port, timeout_graceful_shutdown=3))
    browser_mode = args.web or Path(sys.executable).stem == "SIMUWELD-SD-Web"
    if args.no_browser or browser_mode:
        def open_browser():
            while not server.started and not server.should_exit:
                time.sleep(0.05)
            if server.started:
                webbrowser.open(url)

        if sys.stdout is not None:
            print("SIMUWELD SD\n" + url, flush=True)
            if browser_mode:
                print("关闭此窗口可退出 Web UI 本机服务。", flush=True)
        if browser_mode and not args.no_browser:
            threading.Thread(target=open_browser, daemon=True).start()
        server.run(sockets=[listener])
        return

    runtime = (Path(sys._MEIPASS) if getattr(sys, "frozen", False) else base / "work") / "webview2"
    if not (runtime / "msedgewebview2.exe").is_file():
        raise FileNotFoundError("缺少随包 WebView2 运行环境，请完整解压软件包")
    if sys.getwindowsversion().build < 22000:
        subprocess.run(["icacls", str(runtime), "/grant", "*S-1-15-2-2:(OI)(CI)(RX)",
                        "*S-1-15-2-1:(OI)(CI)(RX)"], check=True, capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    webview.settings["WEBVIEW2_RUNTIME_PATH"] = str(runtime)
    service = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    service.start()
    try:
        deadline = time.monotonic() + 30
        while not server.started:
            if not service.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError("SIMUWELD SD 本机服务启动失败，请查看 work/session/application.log")
            time.sleep(0.05)
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.create_window("SIMUWELD SD", url, width=1440, height=960, min_size=(960, 640),
                              confirm_close=True, background_color="#F7F8FA", text_select=True)
        webview.start(gui="edgechromium", localization={
            "global.quitConfirmation": "关闭 SIMUWELD SD？请确认需要保留的内容已经导出。",
            "global.saveFile": "保存文件", "global.ok": "确定", "global.cancel": "取消",
        })
    finally:
        server.should_exit = True
        service.join(timeout=5)
        listener.close()


if __name__ == "__main__":
    main()
