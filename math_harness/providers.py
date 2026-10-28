from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class ProviderError(Exception):
    pass


def endpoint(base_url, suffix="chat/completions"):
    base = base_url.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("服务地址必须是有效的 http:// 或 https:// 地址。")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("服务地址不能包含账号、密码、查询参数或片段。")
    if base.endswith("/chat/completions"):
        base = base[:-len("/chat/completions")]
    return base + "/" + suffix


def extract_json(text):
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        value = json.loads(stripped)
    except (ValueError, TypeError) as exc:
        raise ProviderError("模型没有返回有效 JSON。原始输出已保留，可恢复后重试本阶段。") from exc
    if not isinstance(value, dict):
        raise ProviderError("模型应返回 JSON 对象。")
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A credential-bearing request must not silently move to a different endpoint.
        return None


def api_call(profile, key, messages):
    target = endpoint(profile["base_url"])
    body = {
        "model": profile["model"], "messages": messages,
        "stream": False,
        profile.get("token_parameter", "max_tokens"): profile.get("max_output_tokens", 4096),
    }
    if profile.get("json_mode"):
        body["response_format"] = {"type": "json_object"}
    extra = profile.get("extra_body", {})
    for reserved in {"model", "messages", "stream", "tools", "tool_choice"}:
        if reserved in extra:
            raise ProviderError("额外参数不能覆盖 " + reserved)
    body.update(extra)
    headers = {"Content-Type": "application/json", "User-Agent": "MathHarness/0.1.0"}
    if key:
        headers["Authorization"] = "Bearer " + key
    request = urllib.request.Request(target, json.dumps(body).encode("utf-8"), headers, method="POST")
    try:
        with urllib.request.build_opener(NoRedirect()).open(
            request, timeout=profile.get("timeout_seconds", 600)
        ) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ProviderError("模型响应超过 16 MiB，已停止读取。")
        data = json.loads(raw)
    except urllib.error.HTTPError as exc:
        hints = {401: "API Key 无效或已失效", 403: "没有访问权限", 404: "地址或模型名称不正确",
                 402: "余额或额度不足", 429: "请求频率或配额受限"}
        # Never expose provider error bodies: some echo the Authorization header.
        raise ProviderError(f"API 返回 HTTP {exc.code}：{hints.get(exc.code, '服务端请求失败')}。") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProviderError("无法连接模型服务或请求超时。请检查网络、代理和服务地址；不会自动重复计费请求。") from exc
    except ValueError as exc:
        raise ProviderError("API 没有返回有效 JSON。") from exc
    try:
        choice = data["choices"][0]
        content = choice["message"].get("content")
        if choice.get("finish_reason") in {"length", "content_filter"}:
            raise ProviderError("模型输出被截断或过滤；请提高输出上限或缩小问题后重试。")
        if isinstance(content, list):
            content = "\n".join(x.get("text", "") for x in content if isinstance(x, dict))
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("模型返回了空正文；请检查模型和思考/输出参数。")
        return {"text": content, "usage": data.get("usage", {}), "provider": "api"}
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("响应不符合 Chat Completions 格式。第一版支持该协议，请检查服务地址。") from exc


def codex_command():
    custom = os.environ.get("MATH_HARNESS_CODEX_PATH")
    executable = custom or shutil.which("codex") or shutil.which("codex.exe")
    if not executable and os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            bundled_root = Path(local_app_data) / "OpenAI" / "Codex" / "bin"
            candidates = sorted(bundled_root.glob("*/codex.exe"),
                                key=lambda item: item.stat().st_mtime, reverse=True)
            if candidates:
                executable = str(candidates[0])
    if not executable:
        raise ProviderError("未找到 Codex CLI。请先运行 install-codex.bat，再重新打开软件。")
    path = Path(executable)
    if path.suffix.lower() in {".cmd", ".bat", ".ps1"}:
        script = path.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        node = shutil.which("node")
        if not node or not script.is_file():
            raise ProviderError("无法直接启动 npm 的 Codex。请更新 Node.js/Codex，或设置 MATH_HARNESS_CODEX_PATH 指向 codex.exe。")
        return [node, str(script)]
    if path.suffix.lower() == ".js":
        node = shutil.which("node")
        if not node:
            raise ProviderError("未找到 Node.js。")
        return [node, str(path)]
    if path.suffix.lower() == ".py":  # Deterministic test transport, never supplied by model output.
        return [sys.executable, str(path)]
    return [str(path)]


def codex_env():
    env = os.environ.copy()
    for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENAI_BASE_URL",
                 "OPENAI_FEDERATION_RULE_ID", "OPENAI_IDENTITY_TOKEN_FILE", "OPENAI_WORKLOAD_IDENTITY_CONTEXT"):
        env.pop(name, None)
    if os.name == "nt" and not any(name in env for name in ("HTTPS_PROXY", "https_proxy")):
        # Windows browsers use the system proxy, while the CLI may attempt a
        # direct connection unless its child process receives proxy variables.
        proxies = urllib.request.getproxies()
        https_proxy = proxies.get("https") or proxies.get("http")
        if https_proxy:
            env["HTTPS_PROXY"] = https_proxy
            if proxies.get("http") and not any(name in env for name in ("HTTP_PROXY", "http_proxy")):
                env["HTTP_PROXY"] = proxies["http"]
            env.setdefault("NO_PROXY", "localhost,127.0.0.1,::1")
    return env


def codex_status():
    try:
        command = codex_command()
        result = subprocess.run(command + ["-c", 'forced_login_method="chatgpt"', "login", "status"],
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=15, env=codex_env(), creationflags=hidden_window())
        text = result.stdout + result.stderr
        # Show a normalized status, never authentication files or raw process output.
        chatgpt = result.returncode == 0 and "chatgpt" in text.lower()
        return {"installed": True, "logged_in": chatgpt,
                "message": "已通过 ChatGPT 登录" if chatgpt else "尚未确认 ChatGPT 登录，请点击登录。"}
    except (ProviderError, OSError, subprocess.TimeoutExpired) as exc:
        return {"installed": False, "logged_in": False, "message": str(exc)[:200]}


def hidden_window():
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def start_login():
    # Browser-based sign-in is owned entirely by the official CLI.
    # Keep the CLI visible so the user can open its URL or read a login error
    # when Windows does not launch the browser automatically.
    return subprocess.Popen(codex_command() + ["-c", 'forced_login_method="chatgpt"', "login"],
                            env=codex_env(),
                            creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0)


def logout_codex():
    try:
        result = subprocess.run(codex_command() + ["logout"], capture_output=True,
                                timeout=15, env=codex_env(), creationflags=hidden_window())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProviderError("无法退出当前 Codex 账号，请在终端运行 codex logout 后重试。") from exc
    if result.returncode != 0:
        raise ProviderError("Codex 未能退出当前账号，请在终端运行 codex logout 后重试。")


def codex_call(profile, messages, work_dir, cancel=None):
    command = codex_command()
    prompt = "\n\n".join("[" + x["role"].upper() + "]\n" + x["content"] for x in messages)
    with tempfile.TemporaryDirectory(prefix="codex-", dir=work_dir) as temp:
        root = Path(temp)
        output = root / "answer.txt"
        command += ["-a", "never", "-c", 'forced_login_method="chatgpt"',
                    "-c", 'model_provider="openai"', "exec", "--skip-git-repo-check",
                    "--sandbox", "read-only", "--json", "--color", "never",
                    "--output-last-message", str(output)]
        if profile.get("model", "").strip():
            command += ["--model", profile["model"].strip()]
        command += ["-"]
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                   cwd=root, env=codex_env(), creationflags=hidden_window())
        deadline = time.monotonic() + profile.get("timeout_seconds", 600)
        initial = prompt
        try:
            while True:
                try:
                    stdout, stderr = process.communicate(initial, timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    initial = None
                    if cancel and cancel.is_set():
                        raise ProviderError("运行已请求停止；Codex 当前阶段已终止，可稍后重试本阶段。")
                    if time.monotonic() > deadline:
                        raise ProviderError("Codex 调用超时；已终止当前进程，可稍后恢复。")
            if process.returncode != 0:
                lowered = (stdout + stderr).lower()
                if "unexpected argument" in lowered or "unrecognized" in lowered:
                    raise ProviderError("Codex CLI 版本过旧或参数不兼容。请运行 install-codex.bat 更新。")
                if "request timed out" in lowered or "stream disconnected" in lowered:
                    raise ProviderError("Codex 已启动，但连接模型服务超时。请检查网络或代理，然后重试；这不是账号未登录。")
                if "quota" in lowered or "usage limit" in lowered or "rate limit" in lowered:
                    raise ProviderError("Codex 账号额度或速率受限；已保存进度，稍后可恢复。")
                raise ProviderError("Codex 执行失败。请确认 ChatGPT 登录状态、模型权限和 Windows 沙箱环境。")
            usage, completed = {}, False
            for line in stdout.splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") in {"turn.failed", "error"}:
                    raise ProviderError("Codex 报告执行失败；本阶段不会标记完成。")
                if event.get("type") == "turn.completed":
                    usage = event.get("usage", {})
                    completed = True
            if not completed or not output.is_file():
                raise ProviderError("Codex 未提供完整的成功结果；请检查 CLI 版本后重试。")
            content = output.read_text(encoding="utf-8").strip()
            if not content:
                raise ProviderError("Codex 返回空正文。")
            return {"text": content, "usage": usage, "provider": "codex"}
        finally:
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   capture_output=True, creationflags=hidden_window())
                else:
                    process.kill()
                process.communicate()


def demo_call(stage):
    if stage == "review":
        value = {"coverage_complete": True, "issues": [],
                 "summary": "演示：检查了拉格朗日定理及元素阶的推理。"}
    elif stage == "audit":
        value = {"approved_issue_ids": [], "rejected_issue_ids": [],
                 "needs_more_review": False, "summary": "离线演示结果，未进行模型或人工审查。"}
    else:
        return {"text": "【离线演示：固定示例，不是对输入问题的解答】\n\n"
                "命题：素数阶有限群是循环群。\n\n设 |G|=p 为素数。取 g≠e。由拉格朗日定理，"
                "g 的阶整除 p，且不为 1，因此等于 p。于是 ⟨g⟩=G。证毕。",
                "usage": {}, "provider": "demo"}
    return {"text": json.dumps(value, ensure_ascii=False), "usage": {}, "provider": "demo"}
