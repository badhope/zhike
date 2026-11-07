import pytest


def test_reminders_api_list_create_cancel_compatibility(client):
    empty = client.get("/api/reminders", params={"visitor_id": "v-api"})
    assert empty.status_code == 200
    assert empty.json() == {"reminders": []}

    created = client.post("/api/reminders", json={
        "text": "给客户回电话",
        "when": "1天后",
        "visitor_id": "v-api",
        "chat_key": "webchat:v-api",
        "channel_type": "webchat",
    })
    assert created.status_code == 200, created.text
    reminder = created.json()
    assert reminder["text"] == "给客户回电话"
    assert reminder["visitor_id"] == "v-api"
    assert reminder["status"] == "pending"
    rid = reminder["id"]

    listed = client.get("/api/reminders", params={"visitor_id": "v-api", "limit": 0})
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()["reminders"]] == [rid]

    cancelled = client.delete(f"/api/reminders/{rid}")
    assert cancelled.status_code == 200
    assert cancelled.json() == {"cancelled": rid}

    after = client.get("/api/reminders", params={"visitor_id": "v-api"})
    assert after.status_code == 200
    assert after.json() == {"reminders": []}


def test_reminders_api_preserves_validation_errors(client):
    empty_text = client.post("/api/reminders", json={"text": " ", "when": "1天后"})
    assert empty_text.status_code == 400
    assert empty_text.json()["detail"] == "提醒内容不能为空"

    bad_time = client.post("/api/reminders", json={"text": "交周报", "when": "有空的时候"})
    assert bad_time.status_code == 400
    assert "看不懂时间表达" in bad_time.json()["detail"]

    missing = client.delete("/api/reminders/999999")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "提醒不存在或已触发"


def test_reminders_api_tick_returns_sent_count(client, monkeypatch):
    async def fake_tick():
        return 7

    monkeypatch.setattr("core.reminders.tick", fake_tick)
    response = client.post("/api/reminders/tick")
    assert response.status_code == 200
    assert response.json() == {"sent": 7}


@pytest.mark.parametrize("method, path, kwargs", [
    ("get", "/api/reminders", {}),
    ("post", "/api/reminders", {"json": {"text": "喝水", "when": "1天后"}}),
    ("delete", "/api/reminders/1", {}),
    ("post", "/api/reminders/tick", {}),
])
def test_reminders_api_requires_admin(app, method, path, kwargs):
    from fastapi.testclient import TestClient

    with TestClient(app) as unauthenticated:
        response = getattr(unauthenticated, method)(path, **kwargs)
    assert response.status_code == 401


def test_api_and_tool_agree_on_local_clock(client):
    """同一句「明天早上 9 点」，从 API 建和从工具建设的时间必须一致。

    修复前：API 传 datetime.now(timezone.utc) 给 parse_due，覆盖了它
    「用户说的 8 点 = 本地 8 点」的默认约定，两个入口相差整个时区偏移
    （UTC+8 环境差 8 小时）。ROADMAP 里挂了几版的 P1 缺陷就是这条。

    注意必须走真实 HTTP 端点，直接调 _parse_due 测不到这个缺陷
    （缺陷在参数传递处，不在解析函数本身）。
    """
    from datetime import datetime
    from core import tools as tools_mod

    when = "明天早上9点"

    created = client.post("/api/reminders", json={
        "text": "API 入口",
        "when": when,
        "visitor_id": "v-tz2",
        "chat_key": "webchat:v-tz2",
        "channel_type": "webchat",
    })
    assert created.status_code == 200, created.text
    api_due = datetime.fromisoformat(created.json()["due_at"])

    # 工具入口的解析结果（内部用本地时区默认值）
    tool_due = tools_mod._parse_due(when)
    assert tool_due is not None

    delta = abs((api_due - tool_due).total_seconds())
    assert delta < 60, (
        f"API 与工具入口相差 {delta/3600:.1f} 小时——时钟基准不一致"
        f"（API={api_due.isoformat()}，工具={tool_due.isoformat()}）"
    )

    # 且必须是本地 9 点，不能是 UTC 9 点
    local = api_due.astimezone(datetime.now().astimezone().tzinfo)
    assert (local.hour, local.minute) == (9, 0), f"应为本地 09:00，实际 {local.hour:02d}:{local.minute:02d}"


def test_api_created_reminder_due_matches_local_intent(client):
    """通过 API 创建「2 小时后」，due_at 应约等于本地时间 +2h（不是 UTC +2h）。"""
    from datetime import datetime, timedelta

    created = client.post("/api/reminders", json={
        "text": "时区一致性检查",
        "when": "2小时后",
        "visitor_id": "v-tz",
        "chat_key": "webchat:v-tz",
        "channel_type": "webchat",
    })
    assert created.status_code == 200, created.text
    due = datetime.fromisoformat(created.json()["due_at"])
    expect = datetime.now().astimezone() + timedelta(hours=2)
    delta = abs((due - expect).total_seconds())
    assert delta < 120, f"due 与本地预期相差 {delta:.0f}s，说明用了错误的时钟基准"
