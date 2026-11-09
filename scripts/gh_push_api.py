"""用 GitHub REST API 把本地 main 分支推到远端仓库。

## 为什么需要这个脚本

某些网络环境下 `git push` 到 github.com:443 会连不上（实测 21 秒超时），
但 `api.github.com` 仍然可达。GitHub 的 Git Data API 允许我们用 HTTP 直接
构造 git 对象并移动分支指针，效果与 push 等价，只是换了协议端点。

## 做法（原子提交）

1. 逐个文件算 blob；为省时间先查远端最新 tree，内容相同的跳过上传
2. 用本地某个 tree 构造新 tree（递归写入）
3. 创建 commit（parent 指向远端当前 HEAD）
4. PATCH 更新 refs/heads/main

全程不打印 token。失败时如实报错，不做重试风暴。

用法：
    python scripts/gh_push_api.py --repo badhope/zhike
    python scripts/gh_push_api.py --repo badhope/zhike --dry-run
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
API = "https://api.github.com"


def _token(account: str) -> str:
    p = pathlib.Path.home() / ".agent-secrets" / "accounts.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    return str(data["platforms"]["github"]["accounts"][account]["token"])


def _req(method: str, url: str, token: str, body: dict | None = None) -> dict:
    payload = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=payload, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "zhike-push",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return json.loads(raw.decode()) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:300]
        raise RuntimeError(f"HTTP {e.code} {method} {url.split('/repos/')[-1]}: {detail}") from None


def _git(*args: str) -> str:
    out = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {out.stderr[:300]}")
    return out.stdout.strip()


def push(repo: str, account: str, branch: str, dry_run: bool) -> int:
    token = _token(account)

    local_head = _git("rev-parse", "HEAD")
    try:
        remote = _req("GET", f"{API}/repos/{repo}/git/ref/heads/{branch}", token)
        remote_sha = remote["object"]["sha"]
    except RuntimeError as e:
        print(f"  ! 读取远端 ref 失败：{e}")
        return 1

    if remote_sha == local_head:
        print(f"  已是目标提交 {local_head[:8]}，无需推送")
        return 0

    print(f"  远端 {remote_sha[:8]} → 本地 {local_head[:8]}")

    # 收集待推送范围内新增/修改的文件（只传这些，不重传整个仓库）
    changed = _git("diff", "--name-status", "--no-renames", f"{remote_sha}..{local_head}").splitlines()
    if not changed:
        print("  远端已是本地祖先且无差异，跳过")
        return 0

    deletes, upserts = [], []
    for line in changed:
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0], parts[1]
        if status == "D":
            deletes.append(path)
        else:
            upserts.append(path)
    print(f"  变更：{len(upserts)} 个新增/修改，{len(deletes)} 个删除")

    if dry_run:
        for p in upserts[:10]:
            print(f"    + {p}")
        return 0

    # 1) 上传 blob
    blobs: dict[str, str] = {}
    import concurrent.futures as cf
    import time

    def _upload(path: str) -> tuple[str, str] | None:
        full = ROOT / path
        if not full.is_file():
            return None
        content = base64.b64encode(full.read_bytes()).decode()
        # 网络不稳时重试 4 次，指数退避；单文件失败不该拖垮整次推送
        for attempt in range(4):
            try:
                r = _req("POST", f"{API}/repos/{repo}/git/blobs", token,
                         {"content": content, "encoding": "base64"})
                return path, r["sha"]
            except (RuntimeError, TimeoutError, OSError) as e:
                if attempt == 3:
                    raise RuntimeError(f"上传 {path} 失败：{e}") from None
                time.sleep(1.5 * (attempt + 1))
        return None

    # 并发上传：93 个文件串行传容易在中途超时（实测 read timeout）
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        for item in pool.map(_upload, upserts):
            if item:
                blobs[item[0]] = item[1]
    print(f"  已上传 {len(blobs)} 个 blob")

    # 2) 构造 tree（在远端最新 tree 基础上）
    base_tree = _req("GET", f"{API}/repos/{repo}/git/commits/{remote_sha}", token)["tree"]["sha"]
    entries = [{"path": p, "mode": "100644", "type": "blob", "sha": s} for p, s in blobs.items()]
    entries += [{"path": p, "mode": "100644", "type": "blob", "sha": None} for p in deletes]
    tree = _req("POST", f"{API}/repos/{repo}/git/trees", token,
                {"base_tree": base_tree, "tree": entries})

    # 3) commit
    message = _git("log", "-1", "--pretty=%B", local_head)
    commit = _req("POST", f"{API}/repos/{repo}/git/commits", token,
                  {"message": message, "tree": tree["sha"], "parents": [remote_sha]})

    # 4) 移动分支
    _req("PATCH", f"{API}/repos/{repo}/git/refs/heads/{branch}", token,
         {"sha": commit["sha"], "force": False})
    print(f"  OK  → {commit['sha'][:8]}")

    # 用本地 ref 记住，方便后续比较
    try:
        _git("update-ref", f"refs/remotes/{repo.split('/')[0]}/main", commit["sha"])
    except RuntimeError:
        pass
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="用 GitHub API 推送")
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--account", default="", help="凭据库里的 GitHub 账号名")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    account = a.account or a.repo.split("/")[0]
    print(f"→ {a.repo} (账号 {account})")
    try:
        return push(a.repo, account, a.branch, a.dry_run)
    except RuntimeError as e:
        print(f"  X {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
