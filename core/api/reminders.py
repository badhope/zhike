"""提醒 API 路由：管理提醒的查询、创建、取消和手动调度。"""
from __future__ import annotations


from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core import store
from core.api.deps import require_admin

router = APIRouter(prefix="/api/reminders", tags=["reminders"])


class ReminderIn(BaseModel):
    text: str
    when: str = ""
    due_at: str = ""
    visitor_id: str = ""
    chat_key: str = ""
    channel_type: str = "webchat"
    group_id: str | None = None
    channel_user_id: str | None = None
    repeat_rule: str = ""


@router.get("", dependencies=[Depends(require_admin)])
def reminders_list(visitor_id: str = "", status: str = "pending", limit: int = 50):
    return {"reminders": store.reminder_list(visitor_id=visitor_id, status=status,
                                             limit=min(max(limit, 1), 200))}


@router.post("", dependencies=[Depends(require_admin)])
def reminders_create(body: ReminderIn):
    from core import tools as tools_mod

    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "提醒内容不能为空")
    due_iso = (body.due_at or "").strip()
    repeat_rule = body.repeat_rule.strip()
    when_raw = (body.when or "").strip()
    if not repeat_rule:
        repeat_rule, when_raw = tools_mod._parse_repeat(when_raw)
    if not due_iso:
        # 不要传 UTC 时钟！parse_due 的约定是「用户说的 8 点 = 本地 8 点」，
        # 它内部默认就是 datetime.now().astimezone()。这里显式传 timezone.utc
        # 会覆盖那个正确默认值，导致同一条「明天早上 9 点」——
        # 从网页 API 建是 UTC 9 点（= 北京 17 点），从聊天工具建是北京 9 点，
        # 两个入口差 8 小时。ROADMAP 里挂了几版的 P1 缺陷就是这条。
        due = tools_mod._parse_due(when_raw)
        if due is None:
            raise HTTPException(400, f"看不懂时间表达：{body.when or '(空)'}")
        due_iso = due.isoformat()
    rec = store.reminder_add(
        visitor_id=(body.visitor_id or "admin").strip(),
        chat_key=(body.chat_key or f"{body.channel_type}:{body.visitor_id or 'admin'}").strip(),
        text=text, due_at=due_iso, channel_type=body.channel_type,
        group_id=body.group_id, channel_user_id=body.channel_user_id,
        repeat_rule=repeat_rule,
    )
    return rec


@router.delete("/{rid}", dependencies=[Depends(require_admin)])
def reminders_cancel(rid: int):
    if not store.reminder_cancel(rid):
        raise HTTPException(404, "提醒不存在或已触发")
    return {"cancelled": rid}


@router.post("/tick", dependencies=[Depends(require_admin)])
async def reminders_tick():
    from core import reminders

    return {"sent": await reminders.tick()}
