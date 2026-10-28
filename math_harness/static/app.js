"use strict";

const $ = (id) => document.getElementById(id);
const state = { token: location.hash.startsWith("#token=") ? decodeURIComponent(location.hash.slice(7)) : "",
  profiles: [], runs: [], run: null, mode: "prove_review", resultTab: "draft", timer: null, importTarget: null };
if (state.token) history.replaceState(null, "", location.pathname);

function toast(message, error = false) {
  const box = $("toast");
  box.textContent = String(message); box.classList.toggle("error", error); box.hidden = false;
  clearTimeout(box._timeout); box._timeout = setTimeout(() => { box.hidden = true; }, 5500);
}

async function request(path, method = "GET", body) {
  if (!state.token) throw new Error("缺少本机访问令牌，请关闭窗口并重新启动程序。");
  let response;
  try {
    response = await fetch(path, {method, headers: {"X-MathHarness-Token": state.token,
      ...(body === undefined ? {} : {"Content-Type": "application/json"})},
      body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store"});
  } catch (_) { throw new Error("无法连接本机服务。请确认程序仍在运行。"); }
  let result;
  try { result = await response.json(); } catch (_) { throw new Error("本机服务返回了无效响应。"); }
  if (!response.ok) throw new Error(result.error || `请求失败 (${response.status})`);
  return result;
}

function safe(action) {
  return async (event) => {
    if (event) event.preventDefault();
    try { await action(event); } catch (error) { toast(error.message || String(error), true); }
  };
}

function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll("[data-mode]").forEach(el => el.classList.toggle("selected", el.dataset.mode === mode));
  $("candidate-group").hidden = mode !== "review";
  $("candidate").required = mode === "review";
  $("generator").disabled = mode === "review";
  $("reviewer").disabled = mode === "generate";
  $("auditor").disabled = mode === "generate";
}

function populateProfiles() {
  for (const role of ["generator", "reviewer", "auditor"]) {
    const select = $(role), previous = select.value;
    select.replaceChildren();
    for (const profile of state.profiles) {
      const option = new Option(profile.name + (profile.kind === "api" && !profile.has_key ? " · 需密钥" : ""), profile.id);
      select.add(option);
    }
    select.value = state.profiles.some(p => p.id === previous) ? previous :
      (state.profiles.some(p => p.id === "gpt-account") ? "gpt-account" : state.profiles[0]?.id || "");
  }
  const edit = $("edit-profile"), previous = edit.value;
  edit.replaceChildren(new Option("新建连接", ""));
  for (const p of state.profiles.filter(p => p.kind === "api")) edit.add(new Option(p.name, p.id));
  edit.value = state.profiles.some(p => p.id === previous && p.kind === "api") ? previous : "";
  const codex = state.profiles.find(p => p.id === "gpt-account");
  if (codex) $("codex-model").value = codex.model || "";
  $("demo-note").textContent = ["generator", "reviewer", "auditor"]
    .some(id => state.profiles.find(p => p.id === $(id).value)?.kind === "demo") ?
    "◌ 选中了离线演示模型，结果不会验证输入问题。" :
    "◌ 模型输出和审查结论需自行核实；API 调用可能计费。";
}

function populateRuns() {
  const list = $("run-list"); list.replaceChildren(); $("run-count").textContent = String(state.runs.length);
  if (!state.runs.length) { const empty = document.createElement("p"); empty.className = "empty-side";
    empty.textContent = "你的研究，从一个问题开始。"; list.append(empty); }
  for (const run of state.runs) {
    const button = document.createElement("button"); button.className = "run-item";
    button.classList.toggle("active", state.run?.id === run.id);
    const strong = document.createElement("strong"), small = document.createElement("small");
    strong.textContent = run.title; small.textContent = `${run.demo ? "演示 · " : ""}${statusLabel(run.status)}`;
    button.append(strong, small); button.addEventListener("click", safe(() => openRun(run.id)));
    list.append(button);
  }
}

const statusLabels = {created: "待开始", running: "运行中", paused: "已暂停", completed: "生成完成",
  demo_completed: "演示结束", ready_for_human: "待人工复核", needs_revision: "发现修改意见",
  incomplete: "审查未完成", round_limit: "轮数用尽", call_limit: "调用上限", error: "执行出错"};
function statusLabel(status) { return statusLabels[status] || status; }

async function refreshInfo() {
  const info = await request("/api/info");
  state.profiles = info.profiles;
  $("data-dir").textContent = info.data_dir;
  $("persist-key").disabled = !info.windows;
  $("key-help").textContent = info.windows ? "未勾选时，密钥仅在本次运行中可用。" :
    "当前平台只支持本次会话使用密钥；Windows 上可选择系统加密持久保存。";
  populateProfiles();
}
async function refreshRuns() { state.runs = await request("/api/runs"); populateRuns(); }

function showPage(page) {
  $("composer").hidden = page !== "composer"; $("result").hidden = page !== "result";
  $("page-name").textContent = page === "composer" ? "新建任务" : "研究记录";
  populateRuns();
}

function outputFor(run) {
  switch (state.resultTab) {
    case "feedback":
      return ["已审核反馈 · 仅显示审计批准的原文", run.feedback?.length ?
        run.feedback.map(x => `${x.id} · ${x.location}\n${x.feedback}`).join("\n\n") : "暂无已批准反馈。完整审查和审计输出见“完整过程”。"];
    case "history":
      return ["完整过程 · 各阶段原始输出", run.steps?.length ? run.steps.map(x =>
        `第 ${x.round} 轮 · ${ {draft:"候选证明",review:"错误检查",audit:"反馈审计"}[x.stage] || x.stage}\n\n${x.text}`)
        .join("\n\n━━━━━━━━━━━━━━━━━━\n\n") : "暂无已保存阶段。"];
    case "input": return ["原始输入", `问题：\n${run.question}\n\n参考材料：\n${run.references || "（无）"}`];
    default: return ["候选证明 · 文本与 LaTeX 源码", run.draft || "正在等待候选证明。"];
  }
}

function renderRun(run) {
  state.run = run;
  const summary = {id: run.id, title: run.title, status: run.status,
    message: run.message, updated_at: run.updated_at, demo: run.demo};
  const index = state.runs.findIndex(item => item.id === run.id);
  if (index >= 0) state.runs[index] = summary;
  else state.runs.unshift(summary);
  $("result-title").textContent = run.title;
  $("result-status").textContent = statusLabel(run.status);
  $("result-message").textContent = run.message || "";
  $("status-icon").textContent = run.status === "running" ? "◌" : "✓";
  $("metric-round").textContent = String(Math.min(run.round || 1, run.max_rounds || 1));
  $("metric-calls").textContent = `${run.call_count || 0} / ${run.max_calls}`;
  $("metric-steps").textContent = String(run.steps?.length || 0);
  const usage = run.usage || {};
  const totalTokens = usage.total_tokens ?? ((usage.prompt_tokens || 0) + (usage.completion_tokens || 0));
  $("metric-tokens").textContent = totalTokens ? totalTokens.toLocaleString() : "—";
  $("result-demo").hidden = !run.demo;
  $("pause-run").hidden = run.status !== "running";
  $("resume-run").hidden = !["paused", "error", "call_limit", "round_limit", "incomplete"].includes(run.status);
  document.querySelectorAll("[data-result-tab]").forEach(el => el.classList.toggle("active", el.dataset.resultTab === state.resultTab));
  const [label, content] = outputFor(run);
  $("output-label").textContent = label; $("output-text").textContent = content;
  showPage("result");
  clearTimeout(state.timer);
  if (run.status === "running") state.timer = setTimeout(() => openRun(run.id).catch(error => toast(error.message, true)), 1500);
}

async function openRun(id) { renderRun(await request(`/api/runs/${encodeURIComponent(id)}`)); }

function openSettings(tab = "account") {
  document.querySelectorAll("[data-settings]").forEach(el => el.classList.toggle("active", el.dataset.settings === tab));
  for (const value of ["account", "api", "about"]) $("settings-" + value).hidden = value !== tab;
  $("settings-dialog").showModal();
  if (tab === "account") refreshStatus().catch(error => { $("codex-status").textContent = error.message; });
}

function selectProfile() {
  const profile = state.profiles.find(p => p.id === $("edit-profile").value);
  $("profile-id").value = profile?.id || "";
  $("profile-name").value = profile?.name || "";
  $("base-url").value = profile?.base_url || "";
  $("api-model").value = profile?.model || "";
  $("api-key").value = "";
  $("persist-key").checked = profile?.key_persisted || false;
  $("timeout").value = profile?.timeout_seconds || 600;
  $("max-output").value = profile?.max_output_tokens || 4096;
  $("token-param").value = profile?.token_parameter || "max_tokens";
  $("extra-body").value = JSON.stringify(profile?.extra_body || {}, null, 2);
  $("preset").value = "custom";
  $("api-key").placeholder = profile?.has_key ? "留空保留已有密钥" : "输入服务商提供的密钥";
}

async function refreshStatus() {
  const status = await request("/api/codex/status");
  $("codex-status").textContent = status.message + (status.login_running ? " · 登录过程进行中" :
    status.login_failed && !status.logged_in ? " · 登录进程已失败，请查看 Codex 窗口或在终端运行 codex login" : "");
  $("codex-login").disabled = status.logged_in;
  $("codex-login").textContent = status.logged_in ? "已登录，无需重复授权" : "登录 GPT 账号 ↗";
  $("codex-switch").disabled = !status.installed || status.login_running;
  $("codex-test").disabled = !status.logged_in;
}

async function readLocalFile() {
  const file = $("file-input").files[0]; if (!file) return;
  if (file.size > 1024 * 1024) throw new Error("单个文本文件最多 1 MiB；请提取需要的内容。");
  const content = await file.text(), target = $(state.importTarget);
  if (target.value.length + content.length > Number(target.maxLength)) throw new Error("导入后超出输入字符上限。");
  target.value += (target.value ? "\n\n" : "") + content;
  $("file-input").value = ""; toast("已导入文本；请检查编码及内容。");
}

document.querySelectorAll("[data-mode]").forEach(el => el.addEventListener("click", () => setMode(el.dataset.mode)));
document.querySelectorAll("[data-result-tab]").forEach(el => el.addEventListener("click", () => {
  state.resultTab = el.dataset.resultTab; if (state.run) renderRun(state.run);
}));
document.querySelectorAll("[data-settings]").forEach(el => el.addEventListener("click", () => {
  const tab = el.dataset.settings;
  document.querySelectorAll("[data-settings]").forEach(item => item.classList.toggle("active", item.dataset.settings === tab));
  for (const value of ["account", "api", "about"]) $("settings-" + value).hidden = value !== tab;
}));
for (const id of ["open-settings", "configure-models"]) $(id).addEventListener("click", () => openSettings());
$("close-settings").addEventListener("click", () => $("settings-dialog").close());
$("new-run").addEventListener("click", () => { clearTimeout(state.timer); state.run = null; showPage("composer"); });
$("load-example").addEventListener("click", () => {
  $("title").value = "素数阶群是循环群"; $("question").value = "设 G 是阶为素数 p 的有限群。证明 G 是循环群。";
  $("candidate").value = "取非单位元 g。由拉格朗日定理，g 的阶整除 p，故阶为 p，因而 G=⟨g⟩。";
});
for (const [button, target] of [["import-question", "question"], ["import-reference", "references"]])
  $(button).addEventListener("click", () => { state.importTarget = target; $("file-input").click(); });
$("file-input").addEventListener("change", safe(readLocalFile));
$("generator").addEventListener("change", populateProfilesNote);
$("reviewer").addEventListener("change", populateProfilesNote);
$("auditor").addEventListener("change", populateProfilesNote);
function populateProfilesNote() {
  $("demo-note").textContent = ["generator", "reviewer", "auditor"]
    .some(id => state.profiles.find(p => p.id === $(id).value)?.kind === "demo") ?
    "◌ 选中了离线演示模型，结果不会验证输入问题。" : "◌ 模型输出和审查结论需自行核实；API 调用可能计费。";
}

$("run-form").addEventListener("submit", safe(async () => {
  const button = $("start-button"); button.disabled = true;
  try {
    const run = await request("/api/runs", "POST", {title: $("title").value, question: $("question").value,
      references: $("references").value, candidate: $("candidate").value, mode: state.mode,
      generator: $("generator").value, reviewer: $("reviewer").value, auditor: $("auditor").value,
      max_rounds: Number($("max-rounds").value), max_calls: Number($("max-calls").value)});
    await refreshRuns(); await openRun(run.id);
  } finally { button.disabled = false; }
}));
$("pause-run").addEventListener("click", safe(async () => {
  await request(`/api/runs/${state.run.id}/pause`, "POST", {}); toast("已请求暂停；当前调用完成后生效。");
}));
$("resume-run").addEventListener("click", safe(async () => {
  const run = state.run;
  const extra_calls = run.status === "call_limit" ? 3 : 0;
  const extra_rounds = run.status === "round_limit" || run.round > run.max_rounds ? 1 : 0;
  if (extra_calls || extra_rounds) {
    const text = `恢复将增加 ${extra_calls} 次模型调用和 ${extra_rounds} 轮上限，可能产生费用。继续吗？`;
    if (!confirm(text)) return;
  }
  await request(`/api/runs/${run.id}/resume`, "POST", {extra_calls, extra_rounds}); await openRun(run.id);
}));
$("export-run").addEventListener("click", safe(async () => {
  const {filename, text} = await request(`/api/runs/${state.run.id}/export`);
  const link = document.createElement("a"); link.href = URL.createObjectURL(new Blob([text], {type: "text/markdown;charset=utf-8"}));
  link.download = filename; link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 5000);
}));
$("copy-output").addEventListener("click", safe(async () => { await navigator.clipboard.writeText($("output-text").textContent); toast("已复制。"); }));
$("codex-check").addEventListener("click", safe(refreshStatus));
$("codex-login").addEventListener("click", safe(async () => {
  try {
    const result = await request("/api/codex/login", "POST", {});
    $("codex-status").textContent = result.message;
    await refreshStatus();
  } catch (error) { $("codex-status").textContent = error.message; throw error; }
}));
$("codex-switch").addEventListener("click", safe(async () => {
  if (!confirm("切换账号会退出本机 Codex CLI 当前登录，也可能影响其他使用该登录的 Codex 窗口。确认后将在浏览器中选择新账号。继续吗？")) return;
  $("codex-switch").disabled = true;
  try {
    const result = await request("/api/codex/switch", "POST", {});
    $("codex-status").textContent = result.message;
    await refreshStatus();
  } catch (error) {
    $("codex-status").textContent = error.message;
    throw error;
  } finally { $("codex-switch").disabled = false; }
}));
$("codex-test").addEventListener("click", safe(async () => {
  const button = $("codex-test"), status = $("codex-test-status");
  button.disabled = true;
  status.textContent = "正在请求模型，最多等待 60 秒…";
  try {
    const result = await request("/api/test", "POST", {profile_id: "gpt-account"});
    status.textContent = result.message;
  } catch (error) {
    status.textContent = "连接测试失败：" + (error.message || String(error));
    throw error;
  } finally { button.disabled = false; }
}));
$("codex-form").addEventListener("submit", safe(async () => {
  const result = await request("/api/profiles", "POST", {id: "gpt-account", name: "GPT 账号 · Codex",
    kind: "codex", model: $("codex-model").value, timeout_seconds: 900});
  await refreshInfo(); toast(`${result.name} 已保存。`);
}));
$("new-profile").addEventListener("click", () => { $("edit-profile").value = ""; selectProfile(); $("profile-name").focus(); });
$("edit-profile").addEventListener("change", selectProfile);
$("preset").addEventListener("change", () => {
  if ($("preset").value === "deepseek") $("base-url").value = "https://api.deepseek.com";
  if ($("preset").value === "local") $("base-url").value = "http://127.0.0.1:1234/v1";
});
$("profile-form").addEventListener("submit", safe(async () => {
  let extra;
  try { extra = JSON.parse($("extra-body").value || "{}"); } catch (_) { throw new Error("额外参数必须是有效 JSON 对象。"); }
  const payload = {id: $("profile-id").value || null, name: $("profile-name").value, kind: "api",
    model: $("api-model").value, base_url: $("base-url").value, timeout_seconds: Number($("timeout").value),
    max_output_tokens: Number($("max-output").value), token_parameter: $("token-param").value,
    extra_body: extra, persist_key: $("persist-key").checked};
  if ($("api-key").value) payload.api_key = $("api-key").value;
  const profile = await request("/api/profiles", "POST", payload);
  $("api-key").value = ""; await refreshInfo(); $("edit-profile").value = profile.id; selectProfile(); toast("连接已保存。");
}));
$("test-profile").addEventListener("click", safe(async () => {
  if (!$("profile-id").value) throw new Error("请先保存连接，然后测试。");
  $("test-profile").disabled = true;
  try { const result = await request("/api/test", "POST", {profile_id: $("profile-id").value}); toast(result.message); }
  finally { $("test-profile").disabled = false; }
}));

safe(async () => { await refreshInfo(); await refreshRuns(); setMode(state.mode); selectProfile(); })();
