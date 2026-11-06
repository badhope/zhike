"""构建 Windows exe：准备产物 → 打包 → 冒烟验证。

## 为什么需要脚本而不是一行 pyinstaller

打包本身一行命令就够，但「打出来的 exe 能用」需要三件事都成立：

1. 前端 `dist` 存在（否则打开是空白页，用户以为坏了）；
2. PyInstaller 装好了（不在 requirements.txt 里，属于构建期依赖）；
3. 打完之后**真的跑一遍**——静默失败（缺 hiddenimport、缺数据文件）
   只有启动时才暴露，而用户拿到手才发现问题就太晚了。

本脚本把这三步串起来，并在最后做一次真实冒烟：起 exe、等 /healthz、查知识库条数、
关掉。任何一步失败都返回非零退出码，可直接进 CI。

用法：
    python scripts/build_exe.py              # 完整构建 + 冒烟
    python scripts/build_exe.py --no-verify  # 只构建
    python scripts/build_exe.py --clean      # 先清掉旧产物
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "packaging" / "zhike.spec"
DIST = ROOT / "dist-exe"
EXE = DIST / "zhike.exe"
FRONTEND = ROOT / "frontend" / "dist" / "index.html"


def step(msg: str) -> None:
    print(f"\n== {msg}")


def fail(msg: str) -> int:
    print(f"FAIL {msg}", file=sys.stderr)
    return 1


def check_prereqs() -> int:
    if not FRONTEND.exists():
        print("前端产物缺失，先构建：cd frontend && npm ci && npm run build", file=sys.stderr)
        print("（没有前端产物打出来的 exe 打开是空白页）", file=sys.stderr)
        return 1
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller 未安装：pip install -e .[build]", file=sys.stderr)
        return 1
    return 0


def build(clean: bool) -> int:
    if clean:
        for p in (DIST, ROOT / "build" / "zhike", ROOT / ".tmp" / "pyi-build"):
            if p.exists():
                shutil.rmtree(p, ignore_errors=True)
    cmd = [sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm",
           "--distpath", str(DIST), "--workpath", str(ROOT / ".tmp" / "pyi-build")]
    proc = subprocess.run(cmd, cwd=ROOT)
    if proc.returncode != 0:
        return fail("PyInstaller 打包失败")
    if not EXE.exists():
        return fail(f"没有产出 {EXE}")
    return 0


def _free_port(start: int = 8071) -> int:
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


def _get(url: str, timeout: float = 3.0) -> tuple[int, bytes]:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.read()


def verify() -> int:
    """真实起一次 exe，验证健康检查与知识库条数。"""
    step("冒烟验证：启动 exe")
    port = _free_port()
    env = dict(os.environ)
    # 隔离数据目录，避免污染开发者本机配置
    tmp = ROOT / ".tmp" / "exe-smoke"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    shutil.copy2(EXE, tmp / "zhike.exe")
    env["ZHIKE_HOME"] = str(tmp / "home")

    proc = subprocess.Popen(
        [str(tmp / "zhike.exe"), "run", "--port", str(port), "--quiet"],
        cwd=str(tmp), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + 90
        ok = False
        while time.time() < deadline:
            if proc.poll() is not None:
                out = proc.stdout.read() if proc.stdout else ""
                return fail(f"exe 启动即退出：\n{out[-1500:]}")
            try:
                status, body = _get(f"{base}/healthz", timeout=2)
                if status == 200 and b'"ok":true' in body:
                    ok = True
                    break
            except Exception:  # noqa: BLE001 - 启动期连不上是正常现象
                time.sleep(1)
        if not ok:
            return fail("90 秒内 /healthz 没有就绪")

        _, body = _get(f"{base}/readyz")
        ready = json.loads(body)
        checks = ready.get("checks", {})
        if not checks.get("db", {}).get("ok"):
            return fail("/readyz 报告数据库不正常")
        kb_entries = checks.get("kb", {}).get("entries", 0)
        if not kb_entries:
            return fail("知识库为空——数据文件可能没打进包")

        status, page = _get(f"{base}/")
        if b'<div id="root">' not in page:
            return fail("首页没有前端挂载点——前端产物可能没打进包")

        # 真实走一次问答，验证工具链在打包环境里也能用
        env_file = Path(env["ZHIKE_HOME"]) / ".env"
        tokens: dict[str, str] = {}
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, _, v = line.partition("=")
                    tokens[k.strip()] = v.strip()
        channel = tokens.get("CHANNEL_TOKEN", "")
        if channel:
            payload = json.dumps({
                "channel_type": "web", "channel_user_id": "smoke",
                "text": "帮我算 1234*5678", "display_name": "smoke",
            }).encode()
            req = urllib.request.Request(
                f"{base}/v1/ingest", data=payload, method="POST",
                headers={"Content-Type": "application/json", "X-Channel-Token": channel})
            with urllib.request.urlopen(req, timeout=60) as r:
                reply = json.loads(r.read().decode()).get("reply") or {}
            if reply.get("tool_used") != "calc":
                return fail(f"打包环境下 calc 没有执行：{reply}")
            if "7006652" not in str(reply.get("text", "")):
                return fail(f"计算结果不对：{reply.get('text')}")

        print(f"OK  exe 可用：healthz + readyz + 首页 + 工具调用（KB {kb_entries} 条）")
        print(f"    {EXE}  {EXE.stat().st_size / 1048576:.1f} MB")
        return 0
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def main() -> int:
    ap = argparse.ArgumentParser(description="构建 Windows 单文件 exe")
    ap.add_argument("--clean", action="store_true", help="先清理旧产物")
    ap.add_argument("--no-verify", action="store_true", help="跳过冒烟验证")
    a = ap.parse_args()

    step("检查前置条件")
    rc = check_prereqs()
    if rc:
        return rc
    print("OK  前端产物、PyInstaller")

    step("打包")
    rc = build(a.clean)
    if rc:
        return rc

    if a.no_verify:
        print(f"\n完成（未验证）：{EXE}")
        return 0
    return verify()


if __name__ == "__main__":
    raise SystemExit(main())
