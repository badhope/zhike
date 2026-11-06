"""知客命令行：首次初始化、启动服务、体检、状态查询。

## 为什么要有这个文件

在此之前，想跑起知客得同时知道 `uvicorn core.main:create_app --factory`、
`.env` 要拷、令牌要手填、前端 dist 要先构建、网关要另开一个进程——
新用户第一次接触时这些信息分散在 README 的三四个小节里，任何一个漏掉都会
得到「页面打不开」而不是一条能照着做的提示。

业界自托管项目（Open WebUI 的 `open-webui serve`、AstrBot 的
`astrbot init` + `astrbot run`、LangBot 的 `uvx langbot`）都是同一套做法：
**一条 init 把环境准备好，一条 run 把服务跑起来，一条 doctor 把问题说清楚**。
本模块就是这三条命令，外加 status。

## 设计取舍

- 只用标准库 argparse，不引入 typer/click：命令就五个，多一个依赖意味着
  打包 exe 时多一份风险，而且 argparse 的错误提示已经够用。
- 所有子命令都能在**未初始化**状态下运行：`init` 负责修环境，`doctor`
  负责报问题，其余命令遇到未初始化会提示先跑 init，而不是抛栈。
- 不静默改用户文件：init 只在文件不存在时创建；已存在的 .env 只补缺失键，
  并打印改了哪几行，不覆盖用户已经填好的值。

用法：
    zhike init            # 首次初始化：生成 .env、令牌、建库、检查前端产物
    zhike run             # 启动 core（默认 8010，前台运行）
    zhike run --gateway   # 同时启动 QQ 网关
    zhike doctor          # 体检：环境、依赖、端口、令牌、数据库、前端
    zhike status          # 查看当前配置与服务状态（不启动任何东西）
    zhike version         # 版本信息
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import sys
import textwrap
import time
import urllib.error
import urllib.request
from pathlib import Path

from core import paths

# 可写根：源码模式是仓库根，exe 模式是 %LOCALAPPDATA%\Zhike 或 exe 同级 zhike-data。
# .env 必须落在可写目录——装到 Program Files 时写仓库根会直接 PermissionError。
ROOT = paths.writable_dir()
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = paths.resource(".env.example")
VERSION_FILE = paths.resource("VERSION")

# 与 core/config.py 的默认端口保持一致
DEFAULT_PORT = 8010

# 全链路端口：网关与协议端是 QQ 渠道的可选组件，core 健康不代表消息能通。
EXTRA_PORTS = (
    ("QQ 网关（OneBot WS）", 3001),
    ("NapCat WebUI", 6099),
    ("一键启动器", 8020),
)


def _configure_stdio() -> tuple[bool, bool]:
    """把标准输出切到 UTF-8，并返回 (是否支持 unicode 符号, 是否支持颜色)。

    为什么必须做：Windows 控制台默认代码页是 936(GBK)，`✓` `✗` 这类字符
    直接抛 UnicodeEncodeError。在 exe 里这意味着**初始化第一步就崩**
    （真实复现：双击 exe → init → UnicodeEncodeError → 用户什么都做不了）。
    富文本重定向到文件时同理，只是不报错而是写成乱码。

    降级策略：切不了 UTF-8 就退回纯 ASCII 符号（[OK]/[!]/[X]），
    界面难看一点，但绝不会因为一个装饰字符让程序不可用。
    """
    supports_symbols = True
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - 编码切换失败不能阻断 CLI
        supports_symbols = False

    # 验证真的能写出符号，而不是只看 reconfigure 是否成功
    enc = (getattr(sys.stdout, "encoding", "") or "").lower()
    if "utf" not in enc:
        supports_symbols = False
    # 老 conhost 不认 ANSI 转义；Windows Terminal / VSCode / Git Bash 都认
    color = sys.stdout.isatty() and os.environ.get("NO_COLOR", "") == "" and (
        os.environ.get("WT_SESSION") or os.environ.get("TERM") or os.name != "nt"
    )
    return supports_symbols, bool(color)


_SYMBOLS, _COLOR = _configure_stdio()
_MARK_OK = "✓" if _SYMBOLS else "[OK]"
_MARK_BAD = "✗" if _SYMBOLS else "[X]"
_MARK_WARN = "!" if _SYMBOLS else "[!]"


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def ok(msg: str) -> None:
    print(f"{_c('32', _MARK_OK)} {msg}")


def warn(msg: str) -> None:
    print(f"{_c('33', _MARK_WARN)} {msg}")


def bad(msg: str) -> None:
    print(f"{_c('31', _MARK_BAD)} {msg}")


def info(msg: str) -> None:
    print(f"  {msg}")


def version_string() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        from core import version as version_mod

        return version_mod.VERSION


# ---------------------------------------------------------------- init

def _read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def _random_token() -> str:
    return secrets.token_hex(32)


def cmd_init(args: argparse.Namespace) -> int:
    print(f"知客 Zhike {version_string()} · 初始化\n")

    changed: list[str] = []

    # 1. .env
    if not ENV_FILE.exists():
        if ENV_EXAMPLE.exists():
            shutil.copyfile(ENV_EXAMPLE, ENV_FILE)
            ok(f"已从 .env.example 创建 .env")
        else:
            ENV_FILE.write_text("", encoding="utf-8")
            warn(".env.example 缺失，已创建空 .env")
        changed.append(".env")
    else:
        info(".env 已存在，只补缺失的键，不覆盖已有值")

    env = _read_env(ENV_FILE)
    # 优先「原地填空」而不是在文件末尾追加：.env.example 里本来就有
    # `ADMIN_TOKEN=` 这样的空行，追加会得到同名键两份（dotenv 取后值，
    # 功能上没错，但用户打开文件会看到两个 ADMIN_TOKEN，不知道该改哪个）。
    generated: list[str] = []
    for key in ("ADMIN_TOKEN", "CHANNEL_TOKEN"):
        if not env.get(key):
            generated.append(key)
    if generated:
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
        filled: set[str] = set()
        for i, line in enumerate(lines):
            for key in generated:
                if line.strip() == f"{key}=":
                    lines[i] = f"{key}={_random_token()}"
                    filled.add(key)
        leftover = [k for k in generated if k not in filled]
        if leftover:
            lines.append("")
            lines.append("# 由 zhike init 生成的访问令牌（请勿提交到仓库）")
            for key in leftover:
                lines.append(f"{key}={_random_token()}")
        ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        for key in generated:
            ok(f"已生成 {key}（32 字节随机）")
        changed.append(".env")

    env = _read_env(ENV_FILE)

    # 2. 数据库
    db_raw = env.get("CS_AGENT_DB") or "cs_agent.db"
    db_path = Path(db_raw)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    existed = db_path.exists()
    # 用真实模块建表，保证与运行时同一套迁移逻辑，而不是手写 DDL
    old = os.environ.get("CS_AGENT_DB")
    os.environ["CS_AGENT_DB"] = str(db_path)
    try:
        from core import store

        store.init_db()
    finally:
        if old is None:
            os.environ.pop("CS_AGENT_DB", None)
        else:
            os.environ["CS_AGENT_DB"] = old
    ok(f"数据库{'已存在' if existed else '已创建'}：{db_path}")

    # 3. 前端产物
    dist = paths.resource("frontend", "dist", "index.html")
    if dist.exists():
        ok("前端产物已就绪")
    else:
        warn("前端产物缺失（frontend/dist），Web 控制台不可用")
        info("构建：cd frontend && npm ci && npm run build")

    # 4. 依赖自检
    missing = _missing_deps()
    if missing:
        warn(f"缺少依赖：{', '.join(missing)}")
        info("安装：pip install -r requirements.txt")
    else:
        ok("Python 依赖齐全")

    print()
    if changed:
        print("初始化完成。接下来：")
    else:
        print("环境已就绪。接下来：")
    admin = env.get("ADMIN_TOKEN", "")
    print(f"  1) {_c('36', 'zhike doctor')}   体检一遍")
    print(f"  2) {_c('36', 'zhike run')}      启动服务")
    print(f"  3) 浏览器打开 {_c('36', f'http://127.0.0.1:{DEFAULT_PORT}')}")
    if admin:
        print(f"\n  管理令牌（登录控制台用，已写入 .env）：")
        print(f"  {admin}")
    return 0


# ---------------------------------------------------------------- doctor

def _missing_deps() -> list[str]:
    """返回缺失的关键依赖模块名（只查运行必需的那几个）。"""
    import importlib.util

    required = {
        "fastapi": "fastapi",
        "uvicorn": "uvicorn",
        "httpx": "httpx",
        "pydantic": "pydantic",
        "dotenv": "python-dotenv",
    }
    return [pkg for mod, pkg in required.items() if importlib.util.find_spec(mod) is None]


def _port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) != 0


def _probe(url: str, timeout: float = 2.0) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return True, r.read(200).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001 - 体检要把任何失败都变成一行人话
        return False, type(e).__name__


def cmd_doctor(args: argparse.Namespace) -> int:
    print(f"知客 Zhike {version_string()} · 体检\n")
    problems = 0

    # 环境文件
    if ENV_FILE.exists():
        ok(".env 存在")
    else:
        bad(".env 不存在")
        info("运行 `zhike init` 生成")
        problems += 1

    env = _read_env(ENV_FILE)
    for key in ("ADMIN_TOKEN", "CHANNEL_TOKEN"):
        val = env.get(key, "")
        if not val:
            bad(f"{key} 未设置")
            info("管理接口/渠道接口会返回 503；运行 `zhike init` 可自动生成")
            problems += 1
        elif len(val) < 16:
            warn(f"{key} 只有 {len(val)} 字符，建议 32 字节随机（`zhike init` 会生成）")
        else:
            ok(f"{key} 已设置（{len(val)} 字符）")

    # 依赖
    missing = _missing_deps()
    if missing:
        bad(f"缺少 Python 依赖：{', '.join(missing)}")
        info("pip install -r requirements.txt")
        problems += 1
    else:
        ok("Python 依赖齐全")

    # 数据库
    db_raw = env.get("CS_AGENT_DB") or "cs_agent.db"
    db_path = Path(db_raw) if Path(db_raw).is_absolute() else ROOT / db_raw
    if db_path.exists():
        size_mb = db_path.stat().st_size / 1048576
        ok(f"数据库存在（{size_mb:.1f} MB）：{db_path}")
    else:
        warn(f"数据库尚未创建：{db_path}")
        info("首次 `zhike run` 或 `zhike init` 会自动建库")

    # 前端产物
    dist = paths.resource("frontend", "dist", "index.html")
    if dist.exists():
        ok("前端产物已就绪")
    else:
        warn("前端产物缺失，Web 控制台不可用")
        info("cd frontend && npm ci && npm run build")
        problems += 1

    # 模型配置
    if env.get("USER_LLM_API_KEY") or env.get("OPENAI_API_KEY"):
        ok("已配置模型 API Key（LLM 模式）")
    else:
        warn("未配置模型 API Key，将以规则模式运行")
        info("规则模式仍可检索知识库、执行确定性工具、回复提醒；可在控制台「模型中心」配置")

    # 端口
    port = int(env.get("PORT") or DEFAULT_PORT)
    if _port_free(port):
        ok(f"端口 {port} 可用")
    else:
        warn(f"端口 {port} 已被占用")
        info("换端口：设置环境变量 PORT，或先关闭占用该端口的程序")
        # 端口被占用时尝试探测是否就是本项目在跑，这个信息对用户最有用
        alive, body = _probe(f"http://127.0.0.1:{port}/healthz")
        if alive:
            info(f"该端口上已有服务响应：{body[:80]}")

    # 全链路端口：光看 core 端口不够——QQ 用户真正的故障多半在网关/协议端
    # （消息进来没人回，core 却是健康的）。这几项没有就说明「没接 QQ」，
    # 不是故障，所以只在占用时报告，避免把可选渠道说成错误。
    for label, p in EXTRA_PORTS:
        if _port_free(p):
            info(f"{label}（{p}）未监听 — 未接入属正常")
        else:
            ok(f"{label}（{p}）已监听")

    # 服务状态
    alive, body = _probe(f"http://127.0.0.1:{port}/healthz")
    if alive:
        ok(f"服务正在运行：{body[:100]}")
        ready, rbody = _probe(f"http://127.0.0.1:{port}/readyz")
        if ready:
            _print_ready_checks(rbody)
    else:
        info("服务未运行（`zhike run` 启动）")

    print()
    if problems:
        print(_c("31", f"发现 {problems} 个需要处理的问题"))
        return 1
    print(_c("32", "体检通过"))
    return 0


# ---------------------------------------------------------------- run

def cmd_run(args: argparse.Namespace) -> int:
    env = _read_env(ENV_FILE)
    if not ENV_FILE.exists():
        # 直接跑 run 是绝大多数新用户的第一反应（尤其双击 exe）。
        # 报一句「先跑 init」然后退出，等于让用户先撞一次墙——直接帮它初始化。
        # 交互终端下明确告知正在做什么；非交互（CI/脚本）则保持安静，避免污染输出。
        print("尚未初始化，正在自动初始化（等价于 zhike init）…\n")
        rc = cmd_init(argparse.Namespace())
        if rc != 0:
            bad("自动初始化失败，请手动运行 `zhike init` 查看详情")
            return rc
        print()
        env = _read_env(ENV_FILE)

    # 把 .env 注入进程环境。用 python-dotenv 而不是手写解析：
    # 它处理引号、多行、注释、变量插值，这些手写版本迟早会漏。
    try:
        from dotenv import load_dotenv

        load_dotenv(ENV_FILE, override=False)
    except ImportError:
        warn("python-dotenv 未安装，跳过 .env 加载")

    port = args.port or int(env.get("PORT") or DEFAULT_PORT)
    host = args.host

    if not _port_free(port, host if host != "0.0.0.0" else "127.0.0.1"):
        bad(f"端口 {port} 已被占用，换一个：zhike run --port {port + 1}")
        alive, body = _probe(f"http://127.0.0.1:{port}/healthz")
        if alive:
            info(f"该端口上已有服务在跑：{body[:80]}")
            info("如果你只是想访问它，直接用浏览器打开即可")
        return 2

    print(f"知客 Zhike {version_string()}")
    print(f"  服务地址  http://{host}:{port}")
    admin = env.get("ADMIN_TOKEN", "")
    if admin:
        print(f"  管理令牌  {admin[:8]}…（完整值在 .env）")
    print(f"  模式      {'LLM' if env.get('USER_LLM_API_KEY') else '规则模式（未配置模型 Key）'}")
    print("  停止      Ctrl+C\n")

    if args.gateway:
        return _run_with_gateway(host, port, env)

    try:
        import uvicorn
    except ImportError:
        bad("uvicorn 未安装：pip install -r requirements.txt")
        return 2

    uvicorn.run(
        "core.main:create_app", factory=True, host=host, port=port,
        log_level=args.log_level, access_log=not args.quiet,
    )
    return 0


def _run_with_gateway(host: str, port: int, env: dict[str, str]) -> int:
    """同时起 core 与 QQ 网关。

    用 subprocess 而不是线程：两个进程的日志、退出码、Ctrl+C 语义都该各自独立，
    网关崩了不该把 core 一起带走（网关会自己重连）。
    """
    import subprocess

    env = dict(os.environ)
    env.setdefault("GATEWAY_CORE_URL", f"http://127.0.0.1:{port}")

    core_cmd = [sys.executable, "-m", "uvicorn", "core.main:create_app",
                "--factory", "--host", host, "--port", str(port)]
    gw_cmd = [sys.executable, "-m", "gateway.bot"]

    print("  同时启动 QQ 网关（Ctrl+C 一起停止）\n")
    procs = [subprocess.Popen(core_cmd), subprocess.Popen(gw_cmd)]
    try:
        while all(p.poll() is None for p in procs):
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n正在停止…")
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
    return 0


# ---------------------------------------------------------------- status

def _check_ok(val: object) -> bool:
    """判定 /readyz 里一项检查是否健康。

    各项的字段名不统一：db/kb/metrics 用 ``ok``，llm/search 用 ``configured``
    （未配 Key 不是故障，规则模式是受支持的状态）。只看 ``ok`` 会把两项
    正常的配置判成 ✗，用户看到满屏红叉反而不知道哪个才是真问题。
    """
    if not isinstance(val, dict):
        return bool(val)
    if "ok" in val:
        return bool(val["ok"])
    if "configured" in val:
        return True  # 已配置与否都是健康状态，只是模式不同
    return True


def _print_ready_checks(rbody: str) -> None:
    try:
        checks = json.loads(rbody).get("checks", {})
    except json.JSONDecodeError:
        return
    for name, val in checks.items():
        mark = "✓" if _check_ok(val) else "✗"
        print(f"  {mark} {name}: {json.dumps(val, ensure_ascii=False)[:90]}")


def cmd_status(args: argparse.Namespace) -> int:
    print(f"知客 Zhike {version_string()}\n")
    env = _read_env(ENV_FILE)
    port = int(env.get("PORT") or DEFAULT_PORT)

    rows = [
        ("工作目录", str(ROOT)),
        (".env", "存在" if ENV_FILE.exists() else "缺失"),
        ("数据库", env.get("CS_AGENT_DB") or "cs_agent.db"),
        ("端口", str(port)),
        ("模型 Key", "已配置" if env.get("USER_LLM_API_KEY") else "未配置（规则模式）"),
        ("管理令牌", "已设置" if env.get("ADMIN_TOKEN") else "未设置"),
        ("渠道令牌", "已设置" if env.get("CHANNEL_TOKEN") else "未设置"),
    ]
    width = max(len(k) for k, _ in rows)
    for k, v in rows:
        print(f"  {k.ljust(width)}  {v}")

    print()
    alive, body = _probe(f"http://127.0.0.1:{port}/healthz")
    if alive:
        ok(f"服务运行中：{body[:100]}")
        ready, rbody = _probe(f"http://127.0.0.1:{port}/readyz")
        if ready:
            _print_ready_checks(rbody)
    else:
        warn(f"服务未运行（http://127.0.0.1:{port}）")
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    from core import version as version_mod

    info = version_mod.version_info()
    print(f"zhike {info['version']} ({info['channel']})")
    print(f"python {sys.version.split()[0]}")
    print(f"root   {ROOT}")
    return 0


# ---------------------------------------------------------------- 入口

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="zhike",
        description="知客 —— 自托管的 QQ / 网页私人秘书",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            常用：
              zhike init            首次初始化
              zhike run             启动服务（http://127.0.0.1:8010）
              zhike doctor          排查问题
              zhike status          看当前状态
            """),
    )
    ap.add_argument("--version", action="store_true", help="显示版本后退出")
    sub = ap.add_subparsers(dest="cmd")

    p_init = sub.add_parser("init", help="首次初始化：.env、令牌、数据库")
    p_init.set_defaults(func=cmd_init)

    p_run = sub.add_parser("run", help="启动服务")
    p_run.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    p_run.add_argument("--port", type=int, default=None, help=f"端口（默认 {DEFAULT_PORT}）")
    p_run.add_argument("--gateway", action="store_true", help="同时启动 QQ 网关")
    p_run.add_argument("--log-level", default="info", choices=["critical", "error", "warning", "info", "debug"])
    p_run.add_argument("--quiet", action="store_true", help="关闭访问日志")
    p_run.set_defaults(func=cmd_run)

    p_doc = sub.add_parser("doctor", help="体检：环境、依赖、端口、令牌")
    p_doc.set_defaults(func=cmd_doctor)

    p_status = sub.add_parser("status", help="查看配置与服务状态")
    p_status.set_defaults(func=cmd_status)

    p_ver = sub.add_parser("version", help="版本信息")
    p_ver.set_defaults(func=cmd_version)
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)

    if getattr(args, "version", False) or args.cmd is None:
        if args.cmd is None and not getattr(args, "version", False):
            ap.print_help()
            return 0
        return cmd_version(args)

    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\n已停止")
        return 130
    except Exception as e:  # noqa: BLE001 - CLI 的底线是给出人话，不是抛栈
        bad(f"{type(e).__name__}: {e}")
        info("运行 `zhike doctor` 排查，或加 ZHIKE_DEBUG=1 看完整堆栈")
        if os.environ.get("ZHIKE_DEBUG"):
            raise
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
