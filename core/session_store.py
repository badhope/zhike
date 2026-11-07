"""会话与消息的持久化层。

## 为什么从 store.py 拆出来

`core/store.py` 曾是一个 2100+ 行的单体，同时负责会话、消息、记忆、配置、
工具注册、审计、提醒、动作审批。提醒和本模块是其中**内聚性最强、边界最清晰**
的两块，所以先拆它们。

本模块覆盖一次对话的完整生命周期：

- 建会话 / 校验归属（sid + secret 双因子）
- 写消息 / 读消息（含 SLA 首响计时、软删除过滤）
- 访客身份声明（首次 claim 领令牌，之后必须出示）
- 会话状态迁移（升级 / 结单 / 关闭）与工单创建

## 兼容策略

`core/store.py` 保留同名函数转发到这里，**调用方零改动**（40 个文件、103 处引用）。
新代码请直接 `from core import session_store`。

## 两个容易踩的坑（都有注释标在具体位置）

1. **SLA 首响只算第一次**：靠 `first_response_at IS NULL` 的条件更新保证，
   不能用"先查再写"，多 worker 下会重复计时。
2. **软删除必须能被模型过滤掉**：`get_messages(include_deleted=False)` 是模型
   上下文的入口，删掉的话不能再被读到。
"""
from __future__ import annotations

import hashlib
import json
import secrets as secrets_module
import uuid

from core.store import _now, get_conn

__all__ = [
    "create_session", "check_session_secret", "add_message", "get_messages",
    "visitor_claim_or_verify", "visitor_exists", "delete_message", "get_session",
    "mark_escalated", "set_status", "close_session", "create_ticket",
]


def create_session(session_id: str, visitor_id: str, secret: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (id, visitor_id, secret, status, started_at) VALUES (?, ?, ?, 'ongoing', ?)",
            (session_id, visitor_id, secret, _now()),
        )


def check_session_secret(session_id: str, secret: str) -> bool:
    """校验访客会话归属：sid + secret 双因子。未设置 secret 的老会话/渠道会话一律拒绝 web 写操作。"""
    if not secret:
        return False
    with get_conn() as conn:
        row = conn.execute("SELECT secret FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row or not row["secret"]:
        return False
    # compare_digest 对含非 ASCII 的 str 会抛 TypeError（攻击者可用来把接口打成 500）。
    # 统一编码成 bytes 再比，任何异常都按"不匹配"处理。
    try:
        return secrets_module.compare_digest(
            str(row["secret"]).encode("utf-8"), str(secret).encode("utf-8"))
    except (UnicodeError, TypeError):
        return False


def add_message(session_id: str, role: str, content: str, refs: list | None = None,
                tool_used: str | None = None, thought: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO messages (session_id, role, content, refs, tool_used, thought, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, role, content, json.dumps(refs or []), tool_used,
             (thought or None), _now()),
        )
        # SLA 首响计时（Chatwoot 对标）：首条 agent/operator 回复落库即记。
        # 用 `first_response_at IS NULL` 做条件更新，多 worker 并发时只有第一条生效。
        if role in ("agent", "operator"):
            row = conn.execute(
                "SELECT started_at, first_response_at FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row and row["first_response_at"] is None and row["started_at"]:
                try:
                    from datetime import datetime, timezone
                    t0 = datetime.fromisoformat(row["started_at"])
                    secs = int((datetime.now(timezone.utc) - t0).total_seconds())
                except (ValueError, TypeError):
                    secs = None
                conn.execute(
                    "UPDATE sessions SET first_response_at = ?, first_response_seconds = ? "
                    "WHERE id = ? AND first_response_at IS NULL",
                    (_now(), secs, session_id),
                )
        return cur.lastrowid or 0


def get_messages(session_id: str, limit: int | None = None,
                 include_deleted: bool = True) -> list[dict]:
    """读取会话消息，按时间正序。

    limit 给定时只取最近 N 条（倒序查再翻转），避免长会话全量加载：
    实测 2000 条历史会让上下文构建耗时从 18ms 涨到 37ms。
    include_deleted=False 用于模型上下文：删掉的话不能再被模型看到。
    """
    cond = "" if include_deleted else " AND deleted = 0"
    with get_conn() as conn:
        if limit and limit > 0:
            rows = conn.execute(
                "SELECT id, role, content, refs, tool_used, thought, deleted, created_at FROM ("
                "  SELECT id, role, content, refs, tool_used, thought, deleted, created_at "
                f"  FROM messages WHERE session_id = ?{cond} ORDER BY id DESC LIMIT ?"
                ") ORDER BY id",
                (session_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, role, content, refs, tool_used, thought, deleted, created_at "
                f"FROM messages WHERE session_id = ?{cond} ORDER BY id",
                (session_id,),
            ).fetchall()
    return [
        {
            "id": r["id"],
            "role": r["role"],
            "content": r["content"],
            "refs": json.loads(r["refs"] or "[]"),
            "tool_used": r["tool_used"],
            "thought": r["thought"] or "",
            "deleted": bool(r["deleted"]),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def visitor_claim_or_verify(visitor_id: str, token: str) -> tuple[bool, str]:
    """首次声明 visitor_id 的调用方获得令牌；已有记录则必须出示匹配令牌。

    返回 (ok, reason)：ok=False 时 reason ∈ {"taken", "bad_token"}。
    只存令牌的 SHA-256，泄库也无法反推。
    """
    vid = (visitor_id or "").strip()
    if not vid:
        return False, "bad_token"
    digest = hashlib.sha256((token or "").encode("utf-8")).hexdigest()
    with get_conn() as conn:
        row = conn.execute("SELECT token_hash FROM visitors WHERE id = ?", (vid,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO visitors (id, token_hash, created_at) VALUES (?, ?, ?)",
                (vid, digest, _now()),
            )
            return True, "created"
        stored = row["token_hash"] or ""
    ok = bool(stored) and secrets_module.compare_digest(stored.encode(), digest.encode())
    return (True, "verified") if ok else (False, "bad_token")


def visitor_exists(visitor_id: str) -> bool:
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM visitors WHERE id = ?", (visitor_id,)).fetchone() is not None


def delete_message(session_id: str, message_id: int) -> bool:
    """软删除一条消息（只允许删本会话内的）。返回是否命中。"""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE messages SET deleted = 1 WHERE id = ? AND session_id = ? AND deleted = 0",
            (message_id, session_id),
        )
        return cur.rowcount > 0


def get_session(session_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return dict(row) if row else None


def mark_escalated(session_id: str, reason: str) -> None:
    """标记升级。已关闭的会话不允许被"复活"成待接管。

    之前无条件改成 pending_agent，导致关闭后访客再发一条消息就把已结束
    的会话重新拉回队列（已复现），工单也会被重开。
    """
    with get_conn() as conn:
        conn.execute(
            "UPDATE sessions SET escalated = 1, status = 'pending_agent', escalate_reason = ? "
            "WHERE id = ? AND status IN ('ongoing', 'agent_handled')",
            (reason, session_id),
        )


def set_status(session_id: str, status: str, assignee: str | None = None) -> bool:
    """更新会话状态。返回是否命中（False 表示会话不存在）。"""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE sessions SET status = ?, assignee = COALESCE(?, assignee) WHERE id = ?",
            (status, assignee, session_id),
        )
    return cur.rowcount > 0


def close_session(session_id: str) -> None:
    now = _now()
    with get_conn() as conn:
        conn.execute(
            "UPDATE sessions SET status = 'closed', ended_at = ? WHERE id = ?",
            (now, session_id),
        )
        conn.execute(
            "UPDATE tickets SET status = 'resolved', resolved_at = ?, "
            "duration_s = CAST((julianday(?) - julianday(created_at)) * 86400 AS INTEGER) "
            "WHERE session_id = ? AND status = 'pending'",
            (now, now, session_id),
        )


def create_ticket(session_id: str, priority: str = "normal") -> str:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM tickets WHERE session_id = ? AND status = 'pending'",
            (session_id,),
        ).fetchone()
        if row:
            return row["id"]
        ticket_id = f"T-{uuid.uuid4().hex[:8]}"
        conn.execute(
            "INSERT INTO tickets (id, session_id, priority, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
            (ticket_id, session_id, priority, _now()),
        )
    return ticket_id
