"""可观测性回归：日志落盘、检索模式可见、令牌不落日志。

Windows 注意：打开的 SQLite 连接与 file handler 会锁住文件，pytest 清理
tmp_path 时报 WinError 32。所以用例结束必须显式关闭自己打开的资源。
"""
import logging
import pathlib

import pytest


def test_events_log_writes_to_file(tmp_path, monkeypatch):
    """配了 CS_AGENT_LOG_FILE 就必须真落盘：否则重启后历史告警无从追溯。"""
    log_file = tmp_path / "app.log"
    monkeypatch.setenv("CS_AGENT_LOG_FILE", str(log_file))
    from core import events_log

    previous = events_log._FILE_HANDLER
    for name in ("cs.events", "core"):
        lg = logging.getLogger(name)
        for h in list(lg.handlers):
            if isinstance(h, logging.FileHandler):
                lg.removeHandler(h)
                h.close()
    events_log._FILE_HANDLER = None
    try:
        events_log.attach()
        logging.getLogger("cs.events").warning("probe-file-line")
        handler = events_log._FILE_HANDLER
        if handler is not None:
            handler.flush()
        assert log_file.exists()
        assert "probe-file-line" in log_file.read_text(encoding="utf-8")
    finally:
        for name in ("cs.events", "core"):
            lg = logging.getLogger(name)
            for h in list(lg.handlers):
                if isinstance(h, logging.FileHandler):
                    lg.removeHandler(h)
                    h.close()
        events_log._FILE_HANDLER = previous


def test_retrieval_mode_reports_actual_backend(tmp_path, monkeypatch):
    from core import retrieval

    info = retrieval.mode()
    assert info["mode"] in ("hybrid", "fts5", "bigram")
    assert isinstance(info["fts5"], bool) and isinstance(info["vector"], bool)

    import importlib.util
    if not importlib.util.find_spec("sqlite_vec"):
        pytest.skip("sqlite-vec 未安装，跳过向量后端断言")

    monkeypatch.setattr(retrieval, "RETRIEVAL_DB", tmp_path / "r.db")
    retrieval._INDEX_STATE.update({"sig": "", "fts5": False, "vec": False})
    conn = retrieval._ensure_conn()
    try:
        assert conn is not None
        assert retrieval.mode()["vector"] is True
    finally:
        if conn is not None:
            conn.close()
        retrieval._RET_LOCAL.conn = None
        retrieval._INDEX_STATE.update({"sig": "", "fts5": False, "vec": False})


def test_health_exposes_retrieval_mode(client):
    body = client.get("/api/agent/health").json()
    assert "retrieval" in body
    assert body["retrieval"]["mode"] in ("hybrid", "fts5", "bigram")
    assert "log_file" in body


def test_no_token_in_access_log_pattern():
    """回归：admin_token 不允许出现在 URL 里（access log 会原样记下）。"""
    repo = pathlib.Path(__file__).resolve().parent.parent
    hits = []
    for p in list(repo.glob("core/**/*.py")) + list(repo.glob("frontend/src/**/*.ts*")):
        text = p.read_text(encoding="utf-8", errors="ignore")
        if "admin_token=" in text:
            hits.append(str(p.relative_to(repo)))
    assert hits == [], f"URL 里带 admin_token 会把令牌写进访问日志：{hits}"


def test_file_path_reports_actual_path(tmp_path, monkeypatch):
    """health 的 log_path 必须反映真实落盘位置，不能只看环境变量。"""
    log_file = tmp_path / "custom.log"
    monkeypatch.setenv("CS_AGENT_LOG_FILE", str(log_file))
    from core import events_log

    previous = events_log._FILE_HANDLER
    events_log._FILE_HANDLER = None
    try:
        events_log.attach()
        assert events_log.file_path() == str(log_file)
    finally:
        h = events_log._FILE_HANDLER
        if h is not None:
            import logging as _lg
            for name in ("cs.events", "core"):
                lg = _lg.getLogger(name)
                if h in lg.handlers:
                    lg.removeHandler(h)
            h.close()
        events_log._FILE_HANDLER = previous


def test_file_path_empty_when_disabled(monkeypatch):
    monkeypatch.setenv("CS_AGENT_LOG_FILE", "off")
    from core import events_log

    previous = events_log._FILE_HANDLER
    events_log._FILE_HANDLER = None
    try:
        events_log.attach()
        assert events_log.file_path() == ""
    finally:
        events_log._FILE_HANDLER = previous


# ---------- 日志级别可配 + 500 响应的关联 ID ----------

def test_log_level_defaults_to_info_and_accepts_override(monkeypatch):
    """修复前：attach() 把级别硬编码 INFO，全项目 logger.debug() 永远进不了内存环。"""
    import logging

    from core import events_log

    monkeypatch.delenv("CS_AGENT_LOG_LEVEL", raising=False)
    assert events_log.log_level() == "INFO"

    monkeypatch.setenv("CS_AGENT_LOG_LEVEL", "debug")
    assert events_log.log_level() == "DEBUG", "大小写不应影响结果"

    monkeypatch.setenv("CS_AGENT_LOG_LEVEL", "verbose")
    assert events_log.log_level() == "INFO", "非法值必须回落到 INFO 而不是让服务起不来"

    monkeypatch.setenv("CS_AGENT_LOG_LEVEL", "DEBUG")
    events_log.attach()
    assert logging.getLogger("core").level == logging.DEBUG


def test_debug_entries_reach_ring_when_level_enabled(monkeypatch):
    """开 DEBUG 后，debug 级记录必须真的出现在日志中心数据源里（不只是级别字段对了）。"""
    import logging

    from core import events_log

    monkeypatch.setenv("CS_AGENT_LOG_LEVEL", "DEBUG")
    events_log.attach()
    logger = logging.getLogger("core.selftest")
    logger.setLevel(logging.DEBUG)
    logger.debug("检索打分 0.31 未过阈值")

    found = [e for e in events_log.entries(limit=50) if "检索打分" in e["message"]]
    assert found, "DEBUG 级别的日志必须能被 /api/admin/logs 读到"
    assert found[0]["level"] == "DEBUG"


def test_unhandled_500_carries_request_id_in_body_and_header(monkeypatch):
    """修复前：两个 @app.exception_handler(Exception) 后者覆盖前者，
    500 响应既没有 request_id 字段也没有 X-Request-ID 头，报错和日志对不上。"""
    from fastapi.testclient import TestClient

    from core import retrieval as r
    from core.main import create_app
    from core import store

    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("CHANNEL_TOKEN", "test-channel-token")

    def boom(*a, **kw):
        raise RuntimeError("unexpected internal failure")

    monkeypatch.setattr(r, "load_kb", boom)
    store.init_db()
    app = create_app()

    handlers = [k for k in app.exception_handlers if k is Exception]
    assert len(handlers) == 1, "Exception 兜底只能注册一个，注册两个会静默覆盖前一个"

    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get("/api/kb", headers={"X-Admin-Token": "test-admin-token", "X-Request-ID": "trace-me-123"})

    assert resp.status_code == 500
    body = resp.json()
    assert body["request_id"], "500 响应必须带回关联 ID，否则用户报错无法定位"
    assert resp.headers.get("X-Request-ID") == body["request_id"]
    assert "Traceback" not in resp.text, "不能把堆栈回显给客户端"


def test_unhandled_500_generates_request_id_when_absent(monkeypatch):
    """客户端没带 X-Request-ID 时也必须生成一个，而不是返回空串。"""
    from fastapi.testclient import TestClient

    from core import retrieval as r
    from core.main import create_app
    from core import store

    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("CHANNEL_TOKEN", "test-channel-token")
    monkeypatch.setattr(r, "load_kb", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    store.init_db()

    with TestClient(create_app(), raise_server_exceptions=False) as c:
        resp = c.get("/api/kb", headers={"X-Admin-Token": "test-admin-token"})

    assert resp.status_code == 500
    assert len(resp.json()["request_id"]) >= 8
