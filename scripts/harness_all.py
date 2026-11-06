"""统一 harness 入口：一条命令跑完所有**离线、免模型、可进 CI** 的检查。

## 为什么需要这个文件

`scripts/` 下有 20 多个 harness，但此前没有任何统一入口，也没有一个挂进 CI：

- 谁想跑一遍得先知道有哪些脚本、各自要什么参数、哪些要花钱；
- CI 里只跑 pytest，角色一致性、工具选择这类「不像单测但同样会悄悄坏掉」
  的检查就永远不会被执行；
- 新增角色、改检索权重后，没有任何自动化会告诉你「零不再像零了」。

本脚本只串**确定性检查**（不调模型、不碰真实 QQ、不需要令牌），因此可以
无条件进 CI。需要真机或花钱的检查保留为 `--live` 单独跑。

## 分级

- `offline`（默认，CI 用）：静态断言，秒级，零成本
- `live`：真实模型 / 真实网关，花钱且慢，只在发版前手动跑

用法：
    python scripts/harness_all.py                # 全部离线检查
    python scripts/harness_all.py --list         # 只列出会跑什么
    python scripts/harness_all.py --only persona,kb
    python scripts/harness_all.py --live         # 追加联网检查（需配置好模型与网关）

退出码：0 全过；1 有失败（可直接作为 CI 门禁）。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Check:
    key: str
    title: str
    argv: list[str]
    live: bool = False
    note: str = ""


CHECKS: tuple[Check, ...] = (
    Check(
        key="version",
        title="版本号三处一致（VERSION / core/version.py / pyproject.toml）",
        argv=["scripts/check_version.py"],
        note="不一致会让 /healthz 和前端关于页各说各话",
    ),
    Check(
        key="persona",
        title="角色一致性：15 张角色卡渲染完整 + 漂移检查 + 角色间区分度",
        argv=["scripts/character_harness.py"],
        note="新增角色或改提示词后必跑；不需要模型",
    ),
    Check(
        key="personas-registry",
        title="预设角色注册表：id/字段/分类自洽",
        argv=["-m", "pytest", "tests/test_builtin_persona.py", "-q"],
        note="注册表是单一事实源，改一处就要过这里",
    ),
    Check(
        key="guard",
        title="回复一致性守卫：客服腔/身份漂移/禁忌词判据",
        argv=["-m", "pytest", "tests/test_character_guard.py", "-q"],
    ),
    Check(
        key="emotes",
        title="表情包标签库：tags.json / index.json 与磁盘文件一致",
        argv=["-m", "pytest", "tests/test_emote_tags.py", "-q"],
        note="真机核对（下载图再视觉复检）走 scripts/verify_emotes.py，需真实 QQ",
    ),
    Check(
        key="tool-select",
        title="工具选择准确率（规则层 + 模型选择，走生产路径）",
        argv=["scripts/tool_selection.py"],
        live=True,
        note="要调模型，默认不在 CI 跑",
    ),
    Check(
        key="e2e",
        title="全能力端到端（需要已启动的 core）",
        argv=["scripts/e2e_check.py"],
        live=True,
        note="需要 ADMIN_TOKEN/CHANNEL_TOKEN 与运行中的服务",
    ),
)


def _run(check: Check) -> tuple[bool, float, str]:
    started = time.perf_counter()
    cmd = [sys.executable, *check.argv]
    try:
        proc = subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True, timeout=900,
        )
    except subprocess.TimeoutExpired:
        return False, time.perf_counter() - started, "超时（>900s）"
    elapsed = time.perf_counter() - started
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, elapsed, output


def main() -> int:
    ap = argparse.ArgumentParser(description="统一 harness 入口（默认只跑离线检查）")
    ap.add_argument("--list", action="store_true", help="列出检查项后退出")
    ap.add_argument("--only", default="", help="只跑这些 key，逗号分隔")
    ap.add_argument("--live", action="store_true", help="追加需要模型/真实服务的检查")
    ap.add_argument("--verbose", action="store_true", help="失败时打印完整输出")
    a = ap.parse_args()

    only = {s.strip() for s in a.only.split(",") if s.strip()}
    selected = [c for c in CHECKS if (not only or c.key in only) and (a.live or not c.live)]

    if a.list:
        for c in selected:
            tag = "live " if c.live else "offline"
            print(f"[{tag}] {c.key:16} {c.title}")
            if c.note:
                print(f"{'':26}{c.note}")
        return 0

    if not selected:
        print("没有匹配的检查项", file=sys.stderr)
        return 1

    failures: list[tuple[Check, str]] = []
    print(f"harness_all: {len(selected)} 项检查，模式={'live' if a.live else 'offline'}\n")

    for c in selected:
        ok, elapsed, output = _run(c)
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {c.key:16} {elapsed:6.1f}s  {c.title}")
        if not ok:
            failures.append((c, output))
            tail = "\n".join(output.strip().splitlines()[-12:])
            print(tail if a.verbose else "".join(f"        {ln}\n" for ln in tail.splitlines()), end="")

    print()
    if failures:
        print(f"失败 {len(failures)}/{len(selected)}：{', '.join(c.key for c, _ in failures)}")
        return 1
    print(f"全部通过（{len(selected)}/{len(selected)}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
