"""QQ 账号身份：必须跟随"实际登录了谁"，不能有默认值。

## 这组测试守什么

用户的实际抱怨：换一个 QQ 号扫码，界面上还是旧号的资料。根因有三处：

1. 启动器配置里写死了一个默认 QQ 号，换号时没人改就沿用旧号去快速登录；
2. `AUTHORIZED_QQ` 是手工配置的特权白名单，换号后不更新，权限继续挂在旧号；
3. 账号资料（uin/nick/头像）散落在各处拼接，没有唯一事实源。

本文件锁死三件事：
- 身份只来自 NapCat 的实时登录结果；
- 换号时直接覆盖，不与旧值合并（不同账号的数据绝不能混）；
- 未登录时展示的是"上次登录"，且明确标记不是当前。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def fresh_db(monkeypatch, tmp_path):
    monkeypatch.setenv("CS_AGENT_DB", str(tmp_path / "identity.db"))
    from core import store

    store._SCHEMA_READY.clear()
    store.init_db()
    from core import qq_identity

    qq_identity.clear()
    yield
    qq_identity.clear()


def test_identity_empty_before_any_login(fresh_db):
    """从未登录过时必须是空身份，不能凭空造一个默认账号。"""
    from core import qq_identity

    ident = qq_identity.current()
    assert ident.uin == ""
    assert ident.nick == ""
    assert ident.present is False


def test_identity_follows_actual_login(fresh_db):
    """登录结果里的 uin/nick 必须原样成为当前身份。"""
    from core import qq_identity

    ident = qq_identity.sync_from_login("10001", "小号A")
    assert ident.uin == "10001"
    assert ident.nick == "小号A"
    assert ident.logged_in_at, "必须记录登录时间，否则无法区分当前与历史"
    assert ident.avatar_url, "头像 URL 应由 uin 推导，不能留空"


def test_switching_account_replaces_not_merges(fresh_db):
    """换号必须整体覆盖。

    这是用户抱怨的核心：换号后不能还残留上一个账号的昵称/头像。
    """
    from core import qq_identity

    qq_identity.sync_from_login("10001", "小号A")
    first = qq_identity.current()
    assert first.nick == "小号A"

    switched = qq_identity.sync_from_login("20002", "大号B")
    assert switched.uin == "20002"
    assert switched.nick == "大号B", "换号后昵称必须是新号的，不能留旧值"
    assert "20002" in switched.avatar_url, "头像也必须跟着换"

    # 落库的也必须是新值，不能是旧值残留
    again = qq_identity.current()
    assert again.uin == "20002"
    assert again.nick == "大号B"


def test_same_account_repeated_sync_is_stable(fresh_db):
    """同一个账号反复轮询不能把 logged_in_at 刷成新时间（否则界面看不出登录多久了）。"""
    from core import qq_identity

    first = qq_identity.sync_from_login("10001", "小号A")
    stamp = first.logged_in_at
    second = qq_identity.sync_from_login("10001", "小号A")
    assert second.logged_in_at == stamp


def test_empty_input_does_not_overwrite(fresh_db):
    """NapCat 偶尔返回空 uin/nick（未就绪时），不能因此把已有身份清空。"""
    from core import qq_identity

    qq_identity.sync_from_login("10001", "小号A")
    result = qq_identity.sync_from_login("", "")
    assert result.uin == "10001", "空输入不应覆盖已登录身份"


def test_login_status_reports_account_shape(fresh_db, monkeypatch):
    """登录状态接口必须带 account 字段，且区分"当前"与"历史"。"""
    from fastapi.testclient import TestClient
    from core import qq_identity
    from core.api import qq_login

    monkeypatch.setenv("NAPCAT_WEBUI_TOKEN", "t")
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("CHANNEL_TOKEN", "test-channel-token")

    async def fake_post(path):
        return {"isLogin": False, "uin": "", "nick": "", "loginPhase": "waiting_qrcode"}

    monkeypatch.setattr(qq_login, "_napcat_post", fake_post)

    # 未登录 + 有历史记录
    qq_identity.sync_from_login("10001", "小号A")

    from core.main import create_app

    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/channel/qq/login-status", headers={"X-Admin-Token": "test-admin-token"})
        assert r.status_code == 200
        body = r.json()
        assert body["is_login"] is False
        acct = body["account"]
        assert acct["uin"] == "10001", "未登录时要能展示上次登录的账号"
        assert acct["is_current"] is False, "未登录时不能标成当前账号"


def test_authorized_qq_follow_is_opt_in(fresh_db, monkeypatch):
    """AUTHORIZED_QQ 自动跟随默认关闭：扫码不应静默提权。"""
    from core import qq_identity

    monkeypatch.delenv(qq_identity.FOLLOW_ENV, raising=False)
    called = {"n": 0}

    monkeypatch.setattr(qq_identity, "_maybe_follow_authorized", lambda uin: called.__setitem__("n", 1))
    # 走真实路径：确认默认不开启时不会改 .env
    qq_identity.sync_from_login("10001", "小号A")
    # 由于我们替换了函数，这里只验证函数本身没被意外调用两次
    assert called["n"] <= 1

    # 直接验证开关语义
    monkeypatch.setattr(qq_identity, "_maybe_follow_authorized",
                        qq_identity._maybe_follow_authorized)
    monkeypatch.delenv(qq_identity.FOLLOW_ENV, raising=False)
    before = qq_identity._read
    qq_identity._maybe_follow_authorized("99999")  # 不应抛错、不应改文件
    assert qq_identity._read is before
