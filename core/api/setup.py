"""首次启动向导：把「装了之后第一步该干什么」变成界面里的四步。

## 为什么需要它

`zhike init` 解决了命令行用户的首次配置，但控制台用户的第一眼是浏览器：
他打开 http://127.0.0.1:8010，看到的是一堆空白图表和一个没配模型的聊天框，
不知道要先去「模型中心」填 Key，也不知道 QQ 是可选的。竞品（AnythingLLM 的
五步 onboarding、Dify 的 `/install`）都把这段引导做进了产品里。

## 设计要点

1. **双门禁：回环 + 管理员令牌**。向导能改管理员密码、填 API Key，等价于
   「这台机器的主人」。只用回环判定是不够的——浏览器里任何一个网页都能向
   `127.0.0.1` 发请求（CSRF / DNS rebinding 的经典攻击面），本机其它用户
   进程也能打这个端口。真实复现：不带任何令牌 POST `/api/setup/complete`
   返回 200。所以写操作一律再要求 `X-Admin-Token`，与其它管理接口同一套鉴权。

2. **一次性**。完成后在 `meta` 表写 `onboarded_at`，之后任何请求都返回
   `needs_onboarding=false`。**老实例自动豁免**：已有会话或已配模型 Key 的库，
   升级后不该被当成新装（AnythingLLM 的 `markOnboarded` 同款处理）。

3. **每步可跳过、状态可恢复**。进度存 `meta` 的 `onboarding_step`，刷新页面
   不会从头开始；「跳过」也记进度，不反复弹窗烦人。

4. **不假装成功**。模型连通性检测真的发一次请求，失败就把上游错误原样告诉用户，
   而不是打个绿勾。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core import store

logger = logging.getLogger("cs.events")

router = APIRouter(prefix="/api/setup", tags=["setup"])

META_DONE = "onboarded_at"
META_STEP = "onboarding_step"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _meta_get(key: str) -> str:
    with store.get_conn() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return (row["value"] if row else "") or ""


def _meta_set(key: str, value: str) -> None:
    with store.get_conn() as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def _is_local(request: Request) -> bool:
    """是否来自本机。

    与 `/api/bootstrap/local-token` 保持同一套判定，避免出现「令牌能拿但向导不让进」
    这种自相矛盾的行为。反代场景同样要求 CS_AGENT_TRUSTED_PROXY=1 且最右一跳是回环。
    """
    host = (request.client.host if request.client else "") or ""
    trusted = {"127.0.0.1", "::1", "localhost", "testclient"}
    if host in trusted:
        return True
    if os.environ.get("CS_AGENT_TRUSTED_PROXY", "").lower() in ("1", "true", "yes", "on"):
        chain = [p.strip() for p in (request.headers.get("x-forwarded-for") or "").split(",") if p.strip()]
        return bool(chain) and chain[-1] in trusted
    return False


def _legacy_instance() -> bool:
    """是不是「老库」：已有会话或已配模型，说明用户早就在用了。

    老实例升级后不该被当成新装弹向导——那是打扰，不是帮助。
    """
    try:
        if _meta_get(META_DONE):
            return True
        with store.get_conn() as conn:
            sessions = conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
            messages = conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]
        return bool(sessions or messages)
    except Exception:  # noqa: BLE001 - 判定失败时按新装处理，让用户看到向导而不是空白页
        return False


def _model_configured() -> bool:
    try:
        from core import config

        cfg = config.get_config()
        llm = cfg.get("llm") or {}
        return bool(llm.get("has_key") and llm.get("model"))
    except Exception:  # noqa: BLE001
        return False


@router.get("/status")
def setup_status(request: Request) -> dict:
    """向导状态。始终可读（前端要据此决定是否显示向导）。"""
    done = _meta_get(META_DONE)
    # 老实例自动标记为已完成，避免升级后被弹窗打扰
    if not done and _legacy_instance():
        _meta_set(META_DONE, _now())
        done = _meta_get(META_DONE)
        logger.info("onboarding auto-completed (legacy instance)")
    return {
        "needs_onboarding": not bool(done),
        "completed_at": done,
        "step": _meta_get(META_STEP) or "welcome",
        "is_local": _is_local(request),
        "checks": {
            "model": _model_configured(),
            "kb": _kb_count() > 0,
        },
    }


def _kb_count() -> int:
    try:
        from core import retrieval

        return len(retrieval.load_kb())
    except Exception:  # noqa: BLE001
        return 0


class StepIn(BaseModel):
    step: str


@router.post("/step")
def setup_step(body: StepIn, request: Request) -> dict:
    """记录当前进行到哪一步，供刷新后恢复。"""
    _require_local(request)
    if _meta_get(META_DONE):
        return {"ok": True, "already_done": True}
    allowed = {"welcome", "model", "channel", "selfcheck", "done"}
    if body.step not in allowed:
        raise HTTPException(400, f"未知步骤：{body.step}")
    _meta_set(META_STEP, body.step)
    return {"ok": True, "step": body.step}


class CompleteIn(BaseModel):
    skipped: list[str] = []


@router.post("/complete")
def setup_complete(body: CompleteIn, request: Request) -> dict:
    """标记向导完成。幂等：重复调用只更新时间戳，不报错。"""
    _require_local(request)
    if _meta_get(META_DONE):
        return {"ok": True, "already_done": True, "completed_at": _meta_get(META_DONE)}
    _meta_set(META_DONE, _now())
    _meta_set(META_STEP, "done")
    if body.skipped:
        _meta_set("onboarding_skipped", json.dumps(body.skipped, ensure_ascii=False))
    logger.info("onboarding completed (skipped=%s)", ",".join(body.skipped) or "none")
    return {"ok": True, "completed_at": _meta_get(META_DONE)}


class ModelProbeIn(BaseModel):
    base_url: str
    api_key: str = ""
    model: str = ""


class DiscoverIn(BaseModel):
    base_url: str
    api_key: str = ""
    protocol: str = "openai-compatible"


@router.post("/discover-models")
async def discover_models(body: DiscoverIn, request: Request) -> dict:
    """拉取该端点可用的模型列表（向导第二步用）。"""
    _require_local(request)
    if not body.base_url.strip():
        raise HTTPException(400, "请填写 API 地址")
    from core import providers

    req = providers.ProviderRequest(
        base_url=body.base_url.strip(),
        api_key=body.api_key.strip(),
        protocol=body.protocol or "openai-compatible",
        private_ok=True,  # 向导里允许本地 Ollama / LM Studio 等内网端点
    )
    return await providers.discover(req)


class ProbeIn(BaseModel):
    base_url: str
    api_key: str = ""
    model: str = ""
    protocol: str = "openai-compatible"


@router.post("/probe-model")
async def probe_model(body: ProbeIn, request: Request) -> dict:
    """真实探测模型连通性：发一次最小对话请求。

    必须真的发请求——只校验字段格式然后打绿勾，会让用户带着不可用的配置
    进入下一步，最后在聊天框里看到「没接住」，却不知道是 Key 的问题。
    """
    _require_local(request)
    if not body.base_url.strip():
        raise HTTPException(400, "请填写 API 地址")
    from core import providers

    req = providers.ProviderRequest(
        base_url=body.base_url.strip(),
        api_key=body.api_key.strip(),
        model=body.model.strip(),
        protocol=body.protocol or "openai-compatible",
        private_ok=True,
        timeout_s=20.0,
    )
    return await providers.test_connection(req)


def _require_local(request: Request) -> None:
    """向导写操作的门禁：必须是本机来源 **且** 持有管理员令牌。

    两道缺一不可：
    - 回环：防止公网/局域网直接访问；
    - 管理员令牌：防止本机浏览器里的第三方页面借用户身份发起请求
      （CSRF / DNS rebinding），也防止同机其它用户进程。
    """
    if not _is_local(request):
        raise HTTPException(403, "首次启动向导只允许在本机访问")
    from core import security

    token = security.admin_token()
    if not token:
        # 未配置管理员令牌时管理接口本身已经全禁用，向导也不该放行
        raise HTTPException(503, "ADMIN_TOKEN 未配置：管理接口已禁用")
    supplied = request.headers.get("x-admin-token") or ""
    if not security.constant_time_equals(supplied, token):
        raise HTTPException(401, "需要管理员令牌")
