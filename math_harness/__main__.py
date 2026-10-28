from __future__ import annotations

import argparse
import sys
import threading
import webbrowser

from .server import App, make_server
from .storage import default_data_dir


def main():
    parser = argparse.ArgumentParser(description="Math Harness / 数学研究工作台")
    parser.add_argument("--browser", action="store_true", help="使用浏览器界面")
    parser.add_argument("--no-browser", action="store_true", help="仅启动本地服务")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--data-dir", type=str)
    args = parser.parse_args()
    app = App(args.data_dir or default_data_dir())
    server = make_server(app, args.port)
    url = f"http://127.0.0.1:{server.server_address[1]}/#token={app.token}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print("Math Harness v0.1.0 已启动。按 Ctrl+C 关闭。", flush=True)
    print("仅本机访问地址（请勿分享）：" + url, flush=True)
    try:
        if not args.browser and not args.no_browser:
            try:
                import webview
            except ImportError:
                webview = None
            if webview is not None:
                try:
                    webview.create_window("Math Harness · 数学研究工作台", url, width=1320,
                                          height=900, min_size=(960, 680))
                    webview.start()
                    return
                except Exception as exc:
                    print("桌面窗口不可用，将使用本地浏览器界面：" + str(exc), flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        while thread.is_alive():
            thread.join(timeout=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for event in app.manager.pauses.values():
            event.set()
        if app.login_process and app.login_process.poll() is None:
            app.login_process.terminate()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
