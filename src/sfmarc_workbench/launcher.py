import argparse
from pathlib import Path
import secrets
import socket
import sys
import tempfile
import threading
import webbrowser

import uvicorn

from .server import create_app


def main():
    parser = argparse.ArgumentParser(description="SIMUWELD SD 本机面载荷编辑")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
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
    server = uvicorn.Server(uvicorn.Config(create_app(token), log_level="warning", host="127.0.0.1", port=port))

    def open_browser():
        import time
        while not server.started and not server.should_exit:
            time.sleep(0.1)
        if server.started:
            webbrowser.open(url)

    if not args.no_browser:
        threading.Thread(target=open_browser, daemon=True).start()
    print("SIMUWELD SD\n" + url + "\n关闭此窗口将停止本机服务。", flush=True)
    server.run(sockets=[listener])


if __name__ == "__main__":
    main()
