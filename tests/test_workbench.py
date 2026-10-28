import json
import os
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from threading import Thread
from unittest import mock

from math_harness import providers
from math_harness.engine import Manager
from math_harness.server import App, make_server
from math_harness.storage import Store


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = App(Path(self.tmp.name) / "data")

    def wait_for(self, run_id, terminal=None):
        terminal = terminal or {"ready_for_human", "demo_completed", "completed", "error", "paused",
                                "call_limit", "round_limit", "incomplete", "needs_revision"}
        for _ in range(300):
            run = self.app.store.run(run_id)
            if run["status"] in terminal:
                return run
            time.sleep(0.01)
        self.fail("Worker did not reach a terminal state")

    def task(self, **overrides):
        return {"title": "prime", "question": "素数阶群为何循环？", "mode": "prove_review",
                "generator": "demo", "reviewer": "demo", "auditor": "demo",
                "max_rounds": 2, "max_calls": 9, **overrides}

    def test_demo_and_restart_preserve_record_without_certifying_input(self):
        run = self.app.manager.create(self.task())
        self.app.manager.start(run["id"])
        result = self.wait_for(run["id"])
        self.assertEqual(result["status"], "demo_completed")
        self.assertEqual(result["call_count"], 3)
        self.assertEqual(len(self.app.store.steps(run["id"])), 3)
        self.assertIn("离线演示", self.app.manager.export(run["id"]))
        restarted = App(self.app.store.root)
        self.assertEqual(restarted.manager.detail(run["id"])["status"], "demo_completed")

    def test_audited_issues_drive_revision_and_complete(self):
        calls = []
        fake_profile = self.app.store.save_profile({"name": "mock model", "kind": "api",
            "base_url": "http://127.0.0.1:1/v1", "model": "mock"})

        def fake_call(profile, messages, work_dir, stage="draft"):
            calls.append((stage, messages[-1]["content"]))
            if stage == "draft":
                answer = "证明修订版" if len([x for x in calls if x[0] == "draft"]) > 1 else "证明初稿有漏洞"
            elif stage == "review":
                answer = json.dumps({"coverage_complete": True,
                    "issues": [{"id": "E1", "location": "第二步", "feedback": "缺少关键论证"}] if len(
                        [x for x in calls if x[0] == "review"]) == 1 else [],
                    "summary": "全篇核查"}, ensure_ascii=False)
            else:
                review = self.app.store.step(run["id"], self.app.store.run(run["id"])["round"], "review")["parsed"]
                answer = json.dumps({"approved_issue_ids": [x["id"] for x in review["issues"]],
                                     "rejected_issue_ids": [], "needs_more_review": False, "summary": "已核对"})
            return {"text": answer, "usage": {"total_tokens": 10}, "provider": "fake"}

        run = self.app.manager.create(self.task(generator=fake_profile["id"],
            reviewer=fake_profile["id"], auditor=fake_profile["id"]))
        with mock.patch.object(self.app.manager, "call", fake_call):
            self.app.manager.start(run["id"])
            result = self.wait_for(run["id"])
        self.assertEqual(result["status"], "ready_for_human")
        self.assertEqual(result["call_count"], 6)
        self.assertEqual(result["draft"], "证明修订版")
        self.assertIn("缺少关键论证", [x[1] for x in calls if x[0] == "draft"][1])

    def test_failure_preserves_attempt_and_resume_consumes_call(self):
        run = self.app.manager.create(self.task(mode="generate", max_calls=1))
        def fail(*args, **kwargs):
            raise providers.ProviderError("service unavailable")
        with mock.patch.object(self.app.manager, "call", fail):
            self.app.manager.start(run["id"])
            result = self.wait_for(run["id"])
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["call_count"], 1)
        with mock.patch.object(self.app.manager, "call", return_value={"text": "repaired", "usage": {}, "provider": "fake"}):
            self.app.manager.start(run["id"], extra_calls=1)
            result = self.wait_for(run["id"])
        self.assertEqual(result["call_count"], 2)
        self.assertEqual(result["draft"], "repaired")

    def test_incomplete_review_stops_without_human_ready(self):
        run = self.app.manager.create(self.task(max_rounds=1))
        original = self.app.manager.call
        def call(profile, messages, work_dir, stage="draft"):
            if stage == "review":
                return {"text": json.dumps({"coverage_complete": False, "issues": [],
                    "summary": "关键步骤无法核查"}), "usage": {}, "provider": "fake"}
            return original(profile, messages, work_dir, stage)
        with mock.patch.object(self.app.manager, "call", call):
            self.app.manager.start(run["id"])
            result = self.wait_for(run["id"])
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["round"], 2)

    def test_rejects_invalid_review_and_preserves_raw_attempt(self):
        run = self.app.manager.create(self.task(mode="review", candidate="candidate proof"))
        with mock.patch.object(self.app.manager, "call", return_value={"text": "not json",
                "usage": {}, "provider": "fake"}):
            self.app.manager.start(run["id"])
            result = self.wait_for(run["id"])
        self.assertEqual(result["status"], "error")
        self.assertEqual(self.app.store.steps(run["id"]), [])
        self.assertEqual((self.app.store.artifact_dir(run["id"]) / "call-001-review.txt").read_text(), "not json")

    def test_api_request_secret_not_in_records(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        captured = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                captured.append((self.path, self.headers.get("Authorization"),
                    json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                reply = json.dumps({"choices": [{"message": {"content": "connection works"}, "finish_reason": "stop"}],
                                    "usage": {"total_tokens": 2}}).encode()
                self.send_response(200); self.end_headers(); self.wfile.write(reply)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        profile = {"id": "local-test", "kind": "api", "name": "local", "model": "test-model",
            "base_url": f"http://127.0.0.1:{server.server_address[1]}/v1", "max_output_tokens": 256}
        saved = self.app.dispatch("POST", "/api/profiles", {**profile, "api_key": "private-test-key"})
        self.assertTrue(saved["has_key"])
        output = self.app.dispatch("POST", "/api/test", {"profile_id": saved["id"]})
        self.assertIn("连接成功", output["message"])
        self.assertEqual(captured[0][0], "/v1/chat/completions")
        self.assertEqual(captured[0][1], "Bearer private-test-key")
        self.assertEqual(captured[0][2]["model"], "test-model")
        self.assertNotIn("private-test-key", (self.app.store.db).read_bytes().decode("utf-8", "ignore"))

    def test_http_local_token_and_static_assets(self):
        server = make_server(self.app)
        thread = Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(base + "/app.js") as response:
            self.assertIn(b"refreshInfo", response.read())
        with self.assertRaises(urllib.error.HTTPError) as captured:
            urllib.request.urlopen(base + "/api/info")
        self.assertEqual(captured.exception.code, 403)
        req = urllib.request.Request(base + "/api/info", headers={"X-MathHarness-Token": self.app.token})
        with urllib.request.urlopen(req) as response:
            self.assertIn("profiles", json.load(response))

    def test_codex_transport_with_mock_cli_no_live_login(self):
        fake = Path(self.tmp.name) / "codex_fake.py"
        fake.write_text('''import json, sys
from pathlib import Path
args = sys.argv[1:]
if "status" in args:
    print("Logged in using ChatGPT")
elif "exec" in args:
    text = sys.stdin.read()
    assert "[USER]" in text
    Path(args[args.index("--output-last-message")+1]).write_text("mock answer", encoding="utf-8")
    print(json.dumps({"type": "turn.completed", "usage": {"total_tokens": 8}}))
''', encoding="utf-8")
        with mock.patch.dict(os.environ, {"MATH_HARNESS_CODEX_PATH": str(fake)}):
            self.assertTrue(providers.codex_status()["logged_in"])
            response = providers.codex_call({"model": "", "timeout_seconds": 5},
                [{"role": "user", "content": "test"}], Path(self.tmp.name))
        self.assertEqual(response["text"], "mock answer")

    def test_login_opens_visible_codex_process(self):
        with mock.patch.object(providers, "codex_command", return_value=["codex.exe"]), \
             mock.patch.object(providers.subprocess, "Popen") as spawn:
            providers.start_login()
        command, options = spawn.call_args
        self.assertEqual(command[0][-1], "login")
        self.assertNotIn("stdout", options)
        self.assertNotIn("stderr", options)
        if os.name == "nt":
            self.assertEqual(options["creationflags"], subprocess.CREATE_NEW_CONSOLE)

    @unittest.skipUnless(os.name == "nt", "Windows Codex installation path")
    def test_codex_found_without_explorer_path(self):
        cli = Path(self.tmp.name) / "OpenAI" / "Codex" / "bin" / "version" / "codex.exe"
        cli.parent.mkdir(parents=True)
        cli.touch()
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": self.tmp.name}, clear=True), \
             mock.patch.object(providers.shutil, "which", return_value=None):
            self.assertEqual(providers.codex_command(), [str(cli)])

    @unittest.skipUnless(os.name == "nt", "Windows system proxy")
    def test_codex_inherits_windows_system_proxy(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(providers.urllib.request, "getproxies",
                               return_value={"https": "http://127.0.0.1:7897", "http": "http://127.0.0.1:7897"}):
            env = providers.codex_env()
        self.assertEqual(env["HTTPS_PROXY"], "http://127.0.0.1:7897")
        self.assertIn("127.0.0.1", env["NO_PROXY"])

    def test_switch_account_logs_out_before_new_login(self):
        with mock.patch.object(providers, "codex_status", return_value={"logged_in": True}), \
             mock.patch.object(providers, "logout_codex") as logout, \
             mock.patch.object(providers, "start_login", return_value=mock.Mock()) as login:
            result = self.app.dispatch("POST", "/api/codex/switch", {})
        self.assertIn("新账号", result["message"])
        logout.assert_called_once_with()
        login.assert_called_once_with()

    def test_codex_connection_test_has_short_timeout(self):
        with mock.patch.object(self.app.manager, "call", return_value={"usage": {}}) as call:
            self.app.dispatch("POST", "/api/test", {"profile_id": "gpt-account"})
        self.assertEqual(call.call_args.args[0]["timeout_seconds"], 60)


if __name__ == "__main__":
    unittest.main()
