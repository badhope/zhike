"""提醒 / 待办的持久化层。

## 为什么从 store.py 拆出来

`core/store.py` 曾是一个 2141 行、130 个函数的单体模块，同时负责会话、消息、
记忆、配置、工具注册、审计、提醒、动作审批——七件互不相干的事。它的实际成本
不是"文件长"，而是：

- 改提醒的 SQL 要在两千行里定位，review 时无法判断改动边界；
- 提醒的并发语义（认领/释放）与其它模块的写入混在同一个连接工厂里，
  出问题时很难说清是谁的事务；
- 新增一类实体时，只能继续往同一个文件里堆。

提醒是本项目里**并发语义最强**的一块（多 worker 同时 tick、必须原子认领），
所以先拆它：边界清晰、测试充分、出问题影响面可控。

## 兼容策略

`core/store.py` 保留同名函数转发到这里，**所有调用方无需改动**。
这样做而不是让调用方改 import，是因为 40 个文件、103 个函数引用 store，
一次性全改等于把「低风险重构」变成「高风险大改」。

## 时间语义（容易踩的坑）

`due_at` 用 ISO 字符串落库。直接字符串比较会把不同时区偏移的时刻排错序：
`2026-10-25T09:00:00+08:00` 串面大于 `2026-10-25T01:30:00+00:00`，
但前者实际是 01:00Z，已经过期 30 分钟。所有到期判断统一走 `julianday()`。
"""
from __future__ import annotations

import sqlite3

from core.store import _now, get_conn

__all__ = [
    "reminder_add", "reminder_get", "reminder_list", "reminder_due",
    "reminder_claim", "reminder_release", "reminder_reschedule",
    "reminder_mark", "reminder_cancel",
]


def reminder_add(visitor_id: str, chat_key: str, text: str, due_at: str,
                 channel_type: str = "webchat", group_id: str | None = None,
                 channel_user_id: str | None = None, repeat_rule: str = "") -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO reminders (visitor_id, chat_key, channel_type, group_id, "
            "channel_user_id, text, due_at, status, created_at, repeat_rule) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (visitor_id, chat_key, channel_type, group_id, channel_user_id, text, due_at,
             _now(), repeat_rule),
        )
        rid = cur.lastrowid
    return reminder_get(int(rid)) or {}


def reminder_get(rid: int) -> dict | None:
    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        r = conn.execute("SELECT * FROM reminders WHERE id = ?", (rid,)).fetchone()
    return dict(r) if r else None


def reminder_list(visitor_id: str = "", status: str = "pending", limit: int = 50) -> list[dict]:
    sql = "SELECT * FROM reminders WHERE 1=1"
    args: list = []
    if visitor_id:
        sql += " AND visitor_id = ?"
        args.append(visitor_id)
    if status:
        sql += " AND status = ?"
        args.append(status)
    sql += " ORDER BY due_at LIMIT ?"
    args.append(limit)
    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def reminder_due(now: str, limit: int = 20) -> list[dict]:
    """取出到期且仍待发的提醒（按真实时刻排序，见模块文档）。"""
    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM reminders WHERE status = 'pending' "
            "AND julianday(due_at) <= julianday(?) "
            "ORDER BY julianday(due_at) LIMIT ?", (now, limit)).fetchall()
    return [dict(r) for r in rows]


def reminder_claim(rid: int) -> bool:
    """原子认领一条到期提醒：pending → sending。

    多 worker / 双实例同时 tick 时，只有抢到的那一个会投递；
    之前只 SELECT 不认领，同一提醒会被重复推送。
    """
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE reminders SET status = 'sending' WHERE id = ? AND status = 'pending'",
            (rid,),
        )
    return cur.rowcount > 0


def reminder_release(rid: int) -> None:
    """投递失败/进程中断后把 sending 放回 pending，等待下次重试。"""
    with get_conn() as conn:
        conn.execute("UPDATE reminders SET status = 'pending' WHERE id = ? AND status = 'sending'", (rid,))


def reminder_reschedule(rid: int, next_due: str) -> None:
    """周期提醒触发后：重置为 pending 并把 due_at 推到下一次。"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE reminders SET status = 'pending', due_at = ?, sent_at = NULL, "
            "attempts = 0, last_error = '' WHERE id = ?", (next_due, rid))


def reminder_mark(rid: int, status: str, error: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE reminders SET status = ?, sent_at = ?, attempts = attempts + 1, "
            "last_error = ? WHERE id = ?",
            (status, _now() if status == "sent" else None, error[:200], rid),
        )


def reminder_cancel(rid: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE reminders SET status = 'cancelled' WHERE id = ? AND status = 'pending'", (rid,))
    return cur.rowcount > 0
