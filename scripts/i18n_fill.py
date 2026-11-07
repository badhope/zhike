"""把 scripts/i18n_gap.py 报出的缺失 msgid 补进 en.ts。

翻译原则（沿用项目既有风格）：
- 简洁、面向用户、不像机器翻译；
- 保留占位符原样（{{n}} / {{ms}}），插值由 i18next 处理；
- 专有名词不译（QQ、NapCat、WebUI、SQLite、API、Key、OpenAI）；
- 不确定的宁可意译，不要留空（留空会让英文界面回退成中文）。

用法：python scripts/i18n_fill.py [--write]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EN = ROOT / "frontend/src/locales/en.ts"

TRANSLATIONS: dict[str, str] = {
    "0 = 未知": "0 = unknown",
    "API 地址": "API base URL",
    "Key 只存在本机数据库，加密保存，不会回传到浏览器以外的任何地方。":
        "The key is stored only in your local database, encrypted. It never leaves your browser session.",
    "Language / 语言": "Language",
    "OpenAI 兼容": "OpenAI-compatible",
    "QQ 扫码登录": "QQ sign-in",
    "QQ 是可选渠道。接上之后，群里 @ 它就能用；不接也不影响网页端。":
        "QQ is optional. Once connected, mention it in a group chat; leaving it unconnected does not affect the web console.",
    "QQ 渠道已就绪。回到控制台即可在群里 @ 它或私聊使用。":
        "QQ channel is ready. Go back to the console and mention it in a group, or message it directly.",
    "QQ 渠道是可选的：不接入不影响网页对话；接入则从这里一键部署+扫码":
        "QQ is optional: skip it and web chat still works; connect it here with one-click deploy plus QR sign-in",
    "SQLite 可写": "SQLite writable",
    "WebUI 令牌需与协议端一致（.env 的 NAPCAT_WEBUI_TOKEN）。":
        "The WebUI token must match the protocol side (NAPCAT_WEBUI_TOKEN in .env).",
    "{{n}} 秒后自动重试（协议端刚启动时通常等十几秒即可）":
        "Retrying in {{n}}s (after a fresh protocol start this usually resolves within seconds)",
    "一键部署协议端": "One-click protocol deployment",
    "三分钟配好，然后就能用了": "Set up in three minutes, then you are ready",
    "上一步": "Back",
    "不联网也能用": "Works offline",
    "不配也能用，只是回答会比较朴素": "Works without it, answers are just plainer",
    "不需要账号": "No account needed",
    "个人秘书本身不依赖 QQ；这里提供的是可选的机器人接入，全部流程在本页完成，不需要去克隆部署任何别的东西。":
        "The assistant itself does not depend on QQ; this is optional bot access, completed entirely on this page with nothing else to clone or deploy.",
    "之后随时可以在「设置」和「日志中心」复查这些状态。":
        "You can re-check these at any time under Settings and Logs.",
    "也可以先跳过：规则模式下知识库检索、计算、时间、提醒都能正常用。":
        "You can skip this: in rule mode, knowledge search, calculations, time and reminders all still work.",
    "二维码不可用": "QR code unavailable",
    "二维码保持有效，临近过期才换新；用手机 QQ 扫一次即可，登录成功后本页会自动更新":
        "The QR code stays valid and refreshes only near expiry; scan once with mobile QQ and this page updates automatically",
    "二维码有效 · {{n}} 秒后自动换新": "QR valid · refreshes in {{n}}s",
    "二维码每 10 秒自动换新；用手机 QQ 扫一次即可，登录成功后本页会自动更新":
        "The QR code refreshes every 10 seconds; scan once with mobile QQ and this page updates automatically",
    "正在发一次真实请求…": "Sending a real request…",
    "正在拉取模型列表…": "Fetching model list…",
    "正在获取二维码…": "Fetching QR code…",
    "立即换新码": "Refresh code now",
    "上一步 ": "Back",
    "启动协议端": "Start protocol side",
    "协议端未就绪：": "Protocol side not ready: ",
    "服务存活": "Service alive",
    "数据库": "Database",
    "知识库": "Knowledge base",
    "模型": "Model",
    "已配置": "Configured",
    "未配置，规则模式": "Not configured, rule mode",
    "进程在跑": "Process running",
    "内置示例条目": "Built-in sample entries",
    "跳过全部设置": "Skip all setup",
    "首次设置": "First-time setup",
    "欢迎使用知客": "Welcome to Zhike",
    "连接模型": "Connect a model",
    "接入 QQ": "Connect QQ",
    "自检": "Self-check",
    "开始使用": "Get started",
    "保存中…": "Saving…",
    "继续": "Continue",
    "跳过": "Skip",
    "拉取模型列表": "Fetch models",
    "测试连接": "Test connection",
    "找到 {{n}} 个模型，选一个继续": "Found {{n}} models, pick one to continue",
    "端点可用，但没返回模型列表": "Endpoint reachable, but it returned no model list",
    "连接正常（{{ms}}ms）": "Connected ({{ms}}ms)",
    "连接失败": "Connection failed",
    "API Key": "API key",
    "知客是一个跑在你自己机器上的私人秘书：QQ 群里回话、网页里聊天、真的去执行工具——设提醒、算数、查资料、读写文件。":
        "Zhike is a personal assistant running on your own machine: it answers in QQ groups, chats on the web, and actually runs tools — reminders, calculations, research, file access.",
    "每一步可审计": "Every step auditable",
    "哈希链防篡改": "Hash-chained and tamper-evident",
    "数据库就是你磁盘上的一个文件": "Your database is a single file on disk",
    "没配模型时走规则模式": "Falls back to rule mode without a model",
    "接入路径：控制台 → 设置 → 渠道连接 → QQ → 一键部署协议端 → 扫码登录。":
        "Path: Console → Settings → Channels → QQ → deploy protocol side → scan to sign in.",
    "协议端（NapCat）对 QQ 版本敏感，若登录失败，先看该页面给出的版本提示。":
        "The protocol side (NapCat) is sensitive to the QQ version; if sign-in fails, check the version hint on that page.",
    "大部分用户建议先跳过，把网页端用顺了再接 QQ。":
        "Most users should skip this first and connect QQ after getting comfortable with the web console.",
    "手机 QQ → 右上角「+」→ 扫一扫。扫码后手机上点确认，本页会自动跳到「已登录」。":
        "Mobile QQ → “+” (top right) → Scan. Confirm on your phone and this page switches to signed-in automatically.",
    "刷新状态": "Refresh status",
    "已登录": "Signed in",
    "上次登录：": "Last signed in: ",
    " · 扫码后将以新账号为准": " · the scanned account will replace it",
    "账号资料来自这次实际登录的结果；换号后这里会自动更新，不需要手动改配置。":
        "Account details come from the login that actually happened; switching accounts updates this automatically.",
    "请先填写要登录的 QQ 号（页面「QQ 号」输入框）——账号跟随实际扫码/登录结果，不设默认值":
        "Enter the QQ number to sign in with first — the account follows the actual login, there is no default.",
    "刷新二维码": "Refresh QR code",
    "协议端": "Protocol side",
    "可选：不接 QQ，网页端照样可用": "Optional: skip QQ and the web console still works",
    "复制关联 ID": "Copy correlation ID",
    "已安装": "Installed",
    "接入 QQ 渠道（可选）": "Connect QQ channel (optional)",
    "提示：还没配置 NAPCAT_WEBUI_TOKEN 时拿不到二维码；协议端启动后可在其 webui.json 里查看令牌。":
        "Note: without NAPCAT_WEBUI_TOKEN no QR code can be fetched; once the protocol side is running, find the token in its webui.json.",
    "未安装": "Not installed",
    "检查部署状态…": "Checking deployment status…",
    "正在换新码…": "Refreshing code…",
    "用户上传图": "User upload",
    "知识库已内置 14 条示例，可以直接问「物流发货要多久」试试。":
        "The knowledge base ships with 14 sample entries — try asking “how long does shipping take”.",
    "确认每一项都真的能用": "Confirm every item actually works",
    "第 1 步 · 部署协议端（自动下载最新版，约 36 MB，支持断点续传）":
        "Step 1 · Deploy the protocol side (downloads the latest build, ~36 MB, resumable)",
    "第 2 步 · 启动协议端（已登录过的号自动登录）":
        "Step 2 · Start the protocol side (previously signed-in accounts log in automatically)",
    "第 3 步 · 扫码登录": "Step 3 · Scan to sign in",
    "网关会自动连上，无需重启；回到「渠道连接」看心跳。":
        "The gateway reconnects automatically, no restart needed; check the heartbeat under Channels.",
    "选择动作": "Choose an action",
    "部署中": "Deploying",
    "预览": "Preview",
    "QQ 渠道已就绪。回到控制台即可在群里 @ 它或私聊使用。":
        "QQ channel ready. Head back to the console to mention it in a group or message it directly.",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    gap = subprocess.run([sys.executable, str(ROOT / "scripts/i18n_gap.py")],
                         capture_output=True, text=True, encoding="utf-8")
    missing = [ln.split("\t", 1)[1] for ln in gap.stdout.splitlines() if ln.startswith("MISS\t")]
    todo = [m for m in missing if m in TRANSLATIONS]
    unknown = [m for m in missing if m not in TRANSLATIONS]

    print(f"缺失 {len(missing)}，可补 {len(todo)}，无翻译 {len(unknown)}")
    for u in unknown[:20]:
        print("  NO-TRANS:", u[:70])

    if not a.write or not todo:
        return 0

    text = EN.read_text(encoding="utf-8")
    # 插到 export default 之前的对象末尾
    idx = text.rstrip().rfind("\n}")
    block = "\n  // ---- i18n 补齐（向导 / 扫码页） ----\n"
    for m in todo:
        val = TRANSLATIONS[m].replace("\\", "\\\\").replace("'", "\\'")
        key = m.replace("\\", "\\\\").replace("'", "\\'")
        block += f"  '{key}': '{val}',\n"
    text = text[:idx] + "\n" + block + text[idx:]
    EN.write_text(text, encoding="utf-8")
    print(f"已写入 {len(todo)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
