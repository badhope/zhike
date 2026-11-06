"""首次启动向导：让控制台用户第一次打开就知道该干什么。

守四件事：
1. 全新库首访 needs_onboarding=True；完成后变 False 且幂等
2. 老实例（已有会话）升级后自动豁免，不被弹窗打扰
3. 非本机来源一律 403（向导能改 Key，等价于机器主人权限）
4. 模型探测真的发请求，失败如实返回而不是打绿勾
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 向导写操作需要管理令牌（见 core/api/setup.py 的双门禁说明）
AUTH = {"X-Admin-Token": "test-admin-token"}


@pytest.fixture()
def wizard_app(monkeypatch, tmp_path):
    monkeypatch.setenv("CS_AGENT_DB", str(tmp_path / "wiz.db"))
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("CHANNEL_TOKEN", "test-channel-token")
    monkeypatch.delenv("CS_AGENT_TRUSTED_PROXY", raising=False)
    from core import store

    store._SCHEMA_READY.clear()
    store.init_db()
    from core.main import create_app

    return create_app()


def test_fresh_install_needs_onboarding(wizard_app):
    from fastapi.testclient import TestClient

    with TestClient(wizard_app) as c:
        r = c.get("/api/setup/status")
    assert r.status_code == 200
    body = r.json()
    assert body["needs_onboarding"] is True
    assert body["step"] == "welcome"
    assert body["is_local"] is True


def test_complete_is_idempotent(wizard_app):
    from fastapi.testclient import TestClient

    with TestClient(wizard_app) as c:
        first = c.post("/api/setup/complete", json={"skipped": ["channel"]}, headers=AUTH)
        assert first.status_code == 200
        stamp = first.json()["completed_at"]
        assert stamp

        second = c.post("/api/setup/complete", json={}, headers=AUTH)
        assert second.status_code == 200
        assert second.json().get("already_done") is True
        assert second.json()["completed_at"] == stamp, "重复完成不应改写首次时间戳"

        status = c.get("/api/setup/status").json()
        assert status["needs_onboarding"] is False


def test_step_progress_persists(wizard_app):
    from fastapi.testclient import TestClient

    with TestClient(wizard_app) as c:
        assert c.post("/api/setup/step", json={"step": "model"}, headers=AUTH).status_code == 200
        assert c.get("/api/setup/status").json()["step"] == "model"

        bad = c.post("/api/setup/step", json={"step": "不存在"}, headers=AUTH)
        assert bad.status_code == 400, "未知步骤必须拒绝，否则前端会卡在空白页"


def test_legacy_instance_auto_skips_wizard(wizard_app):
    """已有会话的库是「老实例」，升级后不该被当成新装弹向导。"""
    from fastapi.testclient import TestClient
    from core import store

    store.create_session("legacy-sid", "legacy-visitor")
    with TestClient(wizard_app) as c:
        body = c.get("/api/setup/status").json()
    assert body["needs_onboarding"] is False, "老实例必须自动豁免"
    assert body["completed_at"], "豁免也要留下时间戳，避免下次又判定一次"


def test_non_local_request_is_rejected(wizard_app, monkeypatch):
    """非本机来源不能操作向导（能改 Key 就等于机器主人）。"""
    from fastapi.testclient import TestClient
    from core.api import setup as setup_mod

    with TestClient(wizard_app) as c:
        monkeypatch.setattr(setup_mod, "_is_local", lambda request: False)
        assert c.post("/api/setup/complete", json={}, headers=AUTH).status_code == 403
        assert c.post("/api/setup/step", json={"step": "model"}, headers=AUTH).status_code == 403


def test_probe_model_reports_failure_honestly(wizard_app, monkeypatch):
    """探测失败必须如实返回 ok=false 与原因，不能假装连通。"""
    from fastapi.testclient import TestClient
    from core import providers

    async def fake_probe(req):
        return {"ok": False, "reply": "鉴权失败（401）：API Key 不正确",
                "model": req.model, "latency_ms": 12}

    monkeypatch.setattr(providers, "test_connection", fake_probe)
    with TestClient(wizard_app) as c:
        r = c.post("/api/setup/probe-model",
                   json={"base_url": "https://api.example.com/v1", "api_key": "bad", "model": "m1"},
                   headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "401" in body["reply"], "上游错误要原样透出，否则用户不知道是 Key 的问题"


def test_probe_model_requires_base_url(wizard_app):
    from fastapi.testclient import TestClient

    with TestClient(wizard_app) as c:
        r = c.post("/api/setup/probe-model", json={"base_url": "  ", "api_key": "k", "model": "m"},
                   headers=AUTH)
    assert r.status_code == 400


def test_write_endpoints_require_admin_token(wizard_app):
    """向导写操作必须要求管理令牌，不能只靠回环判定。

    真实漏洞：不带任何令牌 POST /api/setup/complete 返回 200。
    回环判定挡不住浏览器里的第三方页面（CSRF / DNS rebinding），
    而向导能改模型配置、探测内网地址，等价于机器主人权限。
    """
    from fastapi.testclient import TestClient

    with TestClient(wizard_app) as c:
        assert c.post("/api/setup/complete", json={"skipped": []}).status_code == 401
        assert c.post("/api/setup/step", json={"step": "model"}).status_code == 401
        assert c.post("/api/setup/probe-model",
                      json={"base_url": "https://x.test/v1", "api_key": "k", "model": "m"}
                      ).status_code == 401
        assert c.post("/api/setup/discover-models",
                      json={"base_url": "https://x.test/v1", "api_key": "k"}).status_code == 401
        # 带正确令牌则放行（幂等完成）
        assert c.post("/api/setup/complete", json={}, headers=AUTH).status_code == 200


def test_status_stays_readable_without_token(wizard_app):
    """只读状态必须保持开放：前端要靠它决定是否显示向导。"""
    from fastapi.testclient import TestClient

    with TestClient(wizard_app) as c:
        r = c.get("/api/setup/status")
    assert r.status_code == 200
    assert "needs_onboarding" in r.json()
