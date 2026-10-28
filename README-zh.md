# Math Harness Windows 第一版

这是一个本机运行的数学证明工作台，支持两种真实模型入口：

- **GPT 账号**：调用本机官方 Codex CLI。软件不接触你的账号密码；在官方登录流程完成授权。
- **自定义 API**：填写 OpenAI 兼容的 Chat Completions 服务地址、模型名称和 API Key，可给证明生成、错误检查、反馈审计分别选连接。

还有一个**离线演示**连接，仅返回固定的素数阶群例子，用于检查界面；它不会回答输入的问题。

## Windows 上启动

1. 解压项目到你希望的位置，包括 F 盘。安装 Python 3.11 或更新版本，并在安装时选中 Add Python to PATH。GPT 账号模式需要可运行的 Codex CLI；只有通过 `install-codex.bat` 安装时才需要 Node.js 和 npm。
2. 双击 `start-windows.bat`。第一次会建立项目内的 `.venv` 并安装 `pywebview`；网络安装完成后打开桌面窗口。安装失败时可在终端查看输出。可选用 `python -m math_harness --browser` 打开本机浏览器。
3. 使用 GPT 账号：若尚未安装 Codex CLI，先双击 `install-codex.bat`；若已安装则无需重复安装。重启程序，在「模型与账号」先点「检查状态」。显示“已通过 ChatGPT 登录”时无需再次点击登录；可点“测试账号连接”确认模型请求是否成功。若需更换账号，点“切换账号”，确认后会退出本机 Codex CLI 当前登录，再打开官方授权流程。若显示未登录，点“登录 GPT 账号”，在浏览器完成授权，再次检查状态。登录时会打开 Codex 窗口；若浏览器未自动打开，可复制窗口中的授权地址。也可在 Windows 终端运行 `codex login`。若本机回调被拦截，可根据[官方登录说明](https://learn.chatgpt.com/docs/auth)尝试 `codex login --device-auth`（需先在账号或工作区设置中启用）。**GPT 账号模式能否运行取决于本机 Codex 版本、账号权限和额度。**
4. 使用其他模型：在「自定义 API」新建连接，填写服务商提供的准确地址、模型名称与密钥。保存后点击「测试连接」。测试发送一次真实请求，可能计费。将连接分配给任务里的生成、检查、审计角色。

数据默认保存在 `%LOCALAPPDATA%\MathHarness`。你也可以在启动前设置 `MATH_HARNESS_DATA_DIR=F:\MathHarnessData`。界面使用 `127.0.0.1` 本机端口，运行时不要分享启动地址。

若“检查状态”显示已登录，但“测试账号连接”超时，请先确认网络代理正在运行。Windows 下程序会把系统代理设置传给 Codex CLI；修改代理后需要重启 Math Harness。

## 分享给其他 Windows 电脑

分享源码压缩包时不要包含 `.venv`、`math_harness_desktop.egg-info`、`__pycache__` 或个人数据目录。接收者解压后安装 Python 3.11+，双击 `start-windows.bat`，程序会在其电脑上建立新的虚拟环境并安装桌面依赖。首次安装需要网络。若使用 GPT 账号模式，接收者还需在自己的电脑安装 Codex CLI，并用自己的 ChatGPT 账号完成授权；也可改用自己的兼容 API 连接。每台电脑的研究记录默认保存在该用户的 `%LOCALAPPDATA%\MathHarness`，不会随源码压缩包转移。此版本仅绑定本机 `127.0.0.1`，不是供多人同时访问的服务器。

## 使用流程

输入精确问题与假设，可补充 `.md`、`.tex`、`.txt` 文本和候选证明。选择「生成证明」「证明与审查」或「审查已有证明」，设置轮数与模型调用上限，然后开始。

证明与审查模式依次生成候选证明、独立检查数学问题、由另一上下文审计检查意见，再按经过审计的反馈修改。每个阶段写入 SQLite 和文本记录。关闭软件后，正在运行的任务会转为暂停；重新打开可在左侧打开记录并继续。费用由相应服务商或账号规则决定；调用次数上限**不是金额上限**，单次请求可能超时或失败。审查结果必须人工核实。

API Key 默认只在当前进程内存中使用。Windows 上可以选中「使用 Windows 系统加密保存密钥」，用当前 Windows 用户的 DPAPI 加密后保存在本地数据库；换电脑或换 Windows 用户可能无法解密。导出的 Markdown 不含密钥。

## 开发和构建

安装开发环境：`python -m venv .venv`，然后在项目根目录运行 `.venv\Scripts\python -m pip install -e ".[desktop]"`。测试运行 `.venv\Scripts\python -m unittest discover -s tests -v`。

如需单个 Windows 可执行文件，在 **Windows 上**先运行 `start-windows.bat`，再运行 `build-windows.bat`，输出为 `dist\MathHarness.exe`。首次发布前在目标 Windows 机器上实测 WebView2、Codex 登录和实际 API。此压缩包是源代码与 Windows 启动脚本，**不包含预编译 exe**。

## 第一版边界

公式以 LaTeX 源码显示；尚未加入 PDF 解析、联网文献检索、工具调用和形式化证明。自定义 API 仅支持非流式 Chat Completions 协议；兼容服务的额外参数可能需要调整。GPT 账号调用采用 Codex CLI 的 `exec`，CLI 与模型可用性会随安装版本变化。状态「待人工复核」只表示模型审查阶段结束。
