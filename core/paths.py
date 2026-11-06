"""路径解析的唯一入口：同时支持源码运行与 PyInstaller 单文件 exe。

## 为什么需要单独一层

源码运行时，`core/data/kb.json`、`VERSION`、`frontend/dist` 都能用
`Path(__file__).parent.parent` 找到。打进 exe 之后这个假设全部失效：

- 单文件模式下，模块被解包到临时目录 `sys._MEIPASS`，`__file__` 指向那里；
- 但**可写数据**（数据库、日志、.env）绝不能写进 `_MEIPASS`——那是每次启动
  都会重新解包、退出即删的临时目录，写进去等于每次重启都丢配置。

所以必须把两类路径分开：

| 类型 | 源码运行 | 单文件 exe |
|---|---|---|
| 只读资源（kb.json、角色卡、前端产物） | 仓库相对路径 | `sys._MEIPASS` |
| 可写数据（db、logs、.env、部署目录） | 仓库根 | exe 所在目录 |

之前每个模块各自写 `Path(__file__).parent`，打包时逐处失效、逐处打补丁，
必然漏。集中到这里之后，打包只面向一种假设。

## exe 首次运行

exe 所在目录通常是 `C:\\Program Files\\Zhike`（无写权限）或用户从下载目录直接双击。
可写目录按以下顺序选，第一个能建目录且能写的赢：

1. 环境变量 `ZHIKE_HOME`（用户显式指定）
2. exe 同级的 `zhike-data/`（绿色版，整个文件夹可搬走）
3. `%LOCALAPPDATA%\\Zhike`（Windows 安装版的标准位置）
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

__all__ = [
    "IS_FROZEN", "bundle_dir", "data_dir", "writable_dir",
    "resource", "ensure_dirs",
]


IS_FROZEN = bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """只读资源根：源码模式是仓库根，单文件模式是解包目录。"""
    if IS_FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def _exe_dir() -> Path:
    if IS_FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _candidates() -> list[Path]:
    out: list[Path] = []
    explicit = (os.environ.get("ZHIKE_HOME") or "").strip()
    if explicit:
        out.append(Path(explicit).expanduser())
    out.append(_exe_dir() / "zhike-data")
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if local:
        out.append(Path(local) / "Zhike")
    out.append(Path(tempfile.gettempdir()) / "zhike-data")
    return out


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


_CACHED: Path | None = None


def writable_dir() -> Path:
    """可写数据根：数据库、日志、.env、协议端部署目录都放这里。"""
    global _CACHED
    if _CACHED is not None:
        return _CACHED
    # 源码模式优先用仓库根：与既有 .env / .tmp 布局保持一致，不搬家。
    if not IS_FROZEN:
        _CACHED = Path(__file__).resolve().parent.parent
        return _CACHED
    for cand in _candidates():
        if _writable(cand):
            _CACHED = cand
            return cand
    _CACHED = Path.cwd()
    return _CACHED


def data_dir() -> Path:
    """可写数据目录（自动创建）。"""
    d = writable_dir() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def resource(*parts: str) -> Path:
    """只读资源路径，例如 ``resource("core", "data", "kb.json")``。"""
    return bundle_dir().joinpath(*parts)


def ensure_dirs() -> None:
    """启动时统一建好可写目录，避免各模块各自 mkdir。"""
    for sub in ("data", "logs"):
        (writable_dir() / sub).mkdir(parents=True, exist_ok=True)
