from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__, providers
from .engine import Manager, validate_profile
from .storage import Store


class App:
    def __init__(self, data_dir):
        self.store = Store(Path(data_dir))
        self.manager = Manager(self.store)
        self.token = secrets.token_urlsafe(32)
        self.login_process = None
        self.login_lock = threading.Lock()
        if not self.store.profile("demo"):
            self.store.save_profile({"id": "demo", "name": "离线演示 · 无模型调用", "kind": "demo"})
        if not self.store.profile("gpt-account"):
            self.store.save_profile({"id": "gpt-account", "name": "GPT 账号 · Codex", "kind": "codex",
                                     "model": "", "timeout_seconds": 900})

    def dispatch(self, method, path, body):
        parts = path.strip("/").split("/")
        if path == "/api/info" and method == "GET":
            return {"version": __version__, "windows": os.name == "nt",
                    "data_dir": str(self.store.root), "profiles": self.store.profiles()}
        if path == "/api/profiles" and method == "POST":
            profile = validate_profile(body)
            if profile["id"] in {"demo", "gpt-account"} and profile["kind"] != ("demo" if profile["id"] == "demo" else "codex"):
                raise ValueError("内置连接类型不能修改。")
            if profile["kind"] in {"demo", "codex"} and profile["id"] not in {"demo", "gpt-account"}:
                raise ValueError("内置连接不能创建副本。")
            return self.store.save_profile(profile, body.get("api_key"), bool(body.get("persist_key")))
        if path == "/api/codex/status" and method == "GET":
            result = providers.codex_status()
            result["login_running"] = bool(self.login_process and self.login_process.poll() is None)
            result["login_failed"] = bool(self.login_process and self.login_process.poll() not in (None, 0))
            return result
        if path == "/api/codex/login" and method == "POST":
            with self.login_lock:
                if providers.codex_status()["logged_in"]:
                    return {"message": "已通过 ChatGPT 登录，无需重复授权。"}
                if self.login_process and self.login_process.poll() is None:
                    return {"message": "Codex 登录窗口已打开，请在浏览器中完成授权。"}
                self.login_process = providers.start_login()
            return {"message": "已打开 Codex 登录窗口。若浏览器未弹出，请复制窗口中的授权地址；完成后点击检查状态。"}
        if path == "/api/codex/switch" and method == "POST":
            with self.login_lock:
                if any(worker.is_alive() for worker in self.manager.workers.values()):
                    raise ValueError("请先暂停正在运行的研究任务，再切换 GPT 账号。")
                if self.login_process and self.login_process.poll() is None:
                    raise ValueError("当前登录尚在进行，请完成或关闭登录窗口后重试。")
                had_login = providers.codex_status()["logged_in"]
                if had_login:
                    providers.logout_codex()
                self.login_process = providers.start_login()
            return {"message": "已退出旧账号并打开 Codex 登录窗口，请在浏览器中选择新账号。" if had_login
                    else "已打开 Codex 登录窗口，请在浏览器中选择账号。"}
        if path == "/api/test" and method == "POST":
            profile = self.store.profile(body.get("profile_id"))
            if not profile:
                raise ValueError("请先保存连接。")
            # A test is an explicit, bounded inference request; status checks never infer.
            if profile["kind"] == "api":
                profile = {**profile, "max_output_tokens": 128, "json_mode": False}
            elif profile["kind"] == "codex":
                profile = {**profile, "timeout_seconds": 60}
            import tempfile
            with tempfile.TemporaryDirectory(prefix="connection-test-", dir=self.store.root) as temp:
                result = self.manager.call(profile, [{"role": "user", "content": "请只回答：连接成功"}], Path(temp))
            return {"message": "离线演示连接可用" if profile["kind"] == "demo" else "已收到模型回复，连接成功。",
                    "usage": result.get("usage", {})}
        if path == "/api/runs" and method == "GET":
            return [{k: run.get(k) for k in ("id", "title", "status", "message", "updated_at", "demo")}
                    for run in self.store.runs()]
        if path == "/api/runs" and method == "POST":
            with self.manager.lock:
                if any(t.is_alive() for t in self.manager.workers.values()):
                    raise ValueError("请先暂停正在运行的任务，第一版一次运行一个任务。")
                run = self.manager.create(body)
                self.manager.start(run["id"])
            return run
        if len(parts) >= 3 and parts[:2] == ["api", "runs"]:
            run_id = parts[2]
            if len(parts) == 3 and method == "GET":
                return self.manager.detail(run_id)
            if len(parts) == 4 and method == "POST":
                if parts[3] == "pause":
                    return self.manager.pause(run_id)
                if parts[3] == "resume":
                    return self.manager.start(run_id, int(body.get("extra_calls", 0)), int(body.get("extra_rounds", 0)))
            if len(parts) == 4 and parts[3] == "export" and method == "GET":
                return {"filename": "math-research-" + run_id[:8] + ".md", "text": self.manager.export(run_id)}
        raise ValueError("未找到请求的操作。")


def make_server(app, port=0):
    static = Path(__file__).parent / "static"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # No prompt, token or API key is written to access logs.

        def respond(self, status, data, content_type="application/json; charset=utf-8"):
            payload = json.dumps(data, ensure_ascii=False).encode("utf-8") if isinstance(data, (dict, list)) else data
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; "
                             "connect-src 'self'; img-src 'self' data:; font-src 'self'; "
                             "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(payload)

        def handle_request(self):
            try:
                actual_port = self.server.server_address[1]
                host = self.headers.get("Host", "")
                allowed_hosts = {f"127.0.0.1:{actual_port}", f"localhost:{actual_port}"}
                if host not in allowed_hosts:
                    self.respond(403, {"error": "无效的本地访问地址。"})
                    return
                path = urllib.parse.urlsplit(self.path).path
                if path.startswith("/api/"):
                    if not hmac.compare_digest(self.headers.get("X-MathHarness-Token", ""), app.token):
                        self.respond(403, {"error": "访问令牌已失效，请从启动窗口重新打开界面。"})
                        return
                    origin = self.headers.get("Origin")
                    if origin and origin not in {"http://" + x for x in allowed_hosts}:
                        self.respond(403, {"error": "不允许跨站访问。"})
                        return
                    body = {}
                    if self.command == "POST":
                        length = int(self.headers.get("Content-Length", 0))
                        if not 0 <= length <= 2 * 1024 * 1024:
                            raise ValueError("请求过大。")
                        body = json.loads(self.rfile.read(length) or b"{}")
                        if not isinstance(body, dict):
                            raise ValueError("请求正文必须是对象。")
                    result = app.dispatch(self.command, path, body)
                    self.respond(200, result)
                elif self.command == "GET":
                    names = {"/": ("index.html", "text/html; charset=utf-8"),
                             "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                             "/styles.css": ("styles.css", "text/css; charset=utf-8")}
                    if path not in names:
                        self.respond(404, b"Not found", "text/plain")
                        return
                    name, mime = names[path]
                    self.respond(200, (static / name).read_bytes(), mime)
                else:
                    self.respond(405, {"error": "不支持此方法。"})
            except (ValueError, providers.ProviderError, OSError) as exc:
                self.respond(400, {"error": app.store.secrets.redact(str(exc))[:1000]})
            except Exception:
                self.respond(500, {"error": "内部错误。请重启后查看已保存任务；未完成阶段不会标记成功。"})

        do_GET = handle_request
        do_POST = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server
