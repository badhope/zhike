"""当前登录的 QQ 账号：唯一事实源。

## 这份文件解决什么问题

账号资料（QQ 号、昵称、头像）必须是「这次实际登录了谁」的结果，不能是配置里
写死的值。之前踩过的坑：

- 启动器配置里默认写了一个 QQ 号，换账号时用户没手动改，就变成
  「扫码扫的是新号、启动用的是旧号」——怎么登都是上一个账号；
- `AUTHORIZED_QQ`（特权工具白名单）是手工填的，换号后不更新，
  权限会继续挂在旧账号上，属于安全问题；
- NapCat 的凭据缓存在数据目录里，不带具体号启动会复用上次的凭据。

## 设计原则

1. **身份来自 NapCat 的实时登录结果**（`CheckLoginStatus` 的 `uin`/`nick`），
   不来自配置文件、不来自本地缓存。
2. **缓存只用于「加速显示」和「离线下展示上一次是谁」**，并且带
   `logged_in_at` 时间戳，界面上要能看出这是历史记录而不是当前状态。
3. **换号自动使旧缓存失效**：登录结果里的 uin 和缓存不一致时，
   直接覆盖，不做任何合并（不同账号的数据绝不能混）。
4. **AUTHORIZED_QQ 允许自动跟随**（可选）：登录成功后如果启用了
   `QQ_IDENTITY_FOLLOW_LOGIN=1`，把当前账号追加进特权名单。
   默认关闭——权限扩大必须由人显式决定，不能因为扫码就静默提权。

## 存储位置

`meta` 表（key-value），跟库绑在一起：换库就是换环境，不会串数据。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass

from core import store

logger = logging.getLogger("cs.events")

KEY = "qq_identity"
FOLLOW_ENV = "QQ_IDENTITY_FOLLOW_LOGIN"


@dataclass
class QQIdentity:
    """一次登录的账号身份。字段全部来自 NapCat 实时返回。"""

    uin: str = ""
    nick: str = ""
    avatar_url: str = ""
    logged_in_at: str = ""

    @property
    def present(self) -> bool:
        return bool(self.uin or self.nick)


def _read() -> QQIdentity:
    try:
        with store.get_conn() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (KEY,)).fetchone()
    except Exception:  # noqa: BLE001 - 读不到就是没有，不影响主流程
        return QQIdentity()
    if not row or not row["value"]:
        return QQIdentity()
    try:
        data = json.loads(row["value"])
    except (ValueError, TypeError):
        return QQIdentity()
    if not isinstance(data, dict):
        return QQIdentity()
    return QQIdentity(
        uin=str(data.get("uin") or ""),
        nick=str(data.get("nick") or ""),
        avatar_url=str(data.get("avatar_url") or ""),
        logged_in_at=str(data.get("logged_in_at") or ""),
    )


def _write(ident: QQIdentity) -> None:
    try:
        with store.get_conn() as conn:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (KEY, json.dumps(asdict(ident), ensure_ascii=False)),
            )
    except Exception:  # noqa: BLE001
        logger.warning("qq identity persist failed", exc_info=False)


def current() -> QQIdentity:
    """当前账号（上次成功登录的结果）。未登录过返回空对象。"""
    return _read()


def sync_from_login(uin: str, nick: str, avatar_url: str = "") -> QQIdentity:
    """把 NapCat 的实时登录结果同步为当前身份。

    每次登录状态轮询都会调用；只有 uin 变化或首次登录才写库，避免每 3 秒写一次。
    换号时直接覆盖，不与旧值合并——不同账号的数据绝不能混。
    """
    uin = (uin or "").strip()
    nick = (nick or "").strip()
    if not uin and not nick:
        return _read()

    # 头像 URL 在这里一次性推导出来，再用推导后的值参与"是否有变化"的比较。
    # 之前比较用的是入参 avatar_url（空串），而落库的是推导值，于是每次轮询都
    # 判定为"有变化"并重写 logged_in_at——界面上永远显示"刚刚登录"，看不出
    # 实际登录了多久（真踩过：轮询 3 秒一次，时间戳永远是最新）。
    avatar = (avatar_url or "").strip() or (
        f"https://q1.qlogo.cn/g?b=qq&nk={uin}&s=100" if uin else "")

    prev = _read()
    changed = prev.uin != uin
    if not changed and prev.nick == nick and prev.avatar_url == avatar:
        return prev

    from datetime import datetime, timezone

    ident = QQIdentity(
        uin=uin,
        nick=nick,
        avatar_url=avatar,
        logged_in_at=datetime.now(timezone.utc).isoformat(),
    )
    _write(ident)
    if changed:
        logger.info("qq identity switched: %s -> %s", prev.uin or "(none)", uin or "(none)")
        _maybe_follow_authorized(uin)
    return ident


def _maybe_follow_authorized(uin: str) -> None:
    """可选：登录成功后把当前账号追加进 AUTHORIZED_QQ。

    默认关闭。权限扩大必须是人的决定，不能因为扫了个码就静默提权。
    需要时显式设置 QQ_IDENTITY_FOLLOW_LOGIN=1。
    """
    if os.environ.get(FOLLOW_ENV, "").strip().lower() not in ("1", "true", "yes", "on"):
        return
    if not uin:
        return
    try:
        import pathlib

        from core.actions import authz

        if uin in authz.authorized_qq():
            return
        env_path = pathlib.Path(__file__).resolve().parent.parent / ".env"
        if not env_path.is_file():
            return
        lines = env_path.read_text(encoding="utf-8").splitlines()
        out: list[str] = []
        done = False
        for line in lines:
            if line.startswith("AUTHORIZED_QQ="):
                cur = line.split("=", 1)[1].strip()
                ids = [x for x in cur.replace("，", ",").split(",") if x.strip()]
                if uin not in ids:
                    ids.append(uin)
                out.append("AUTHORIZED_QQ=" + ",".join(ids))
                done = True
            else:
                out.append(line)
        if not done:
            out.append(f"AUTHORIZED_QQ={uin}")
        env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
        logger.warning(
            "AUTHORIZED_QQ 已跟随登录更新为包含 %s（QQ_IDENTITY_FOLLOW_LOGIN=1）", uin)
    except Exception:  # noqa: BLE001
        logger.warning("authorized qq follow failed", exc_info=False)


def clear() -> None:
    """清空当前账号记录（登出/换号前调用）。"""
    try:
        with store.get_conn() as conn:
            conn.execute("DELETE FROM meta WHERE key = ?", (KEY,))
    except Exception:  # noqa: BLE001
        pass
