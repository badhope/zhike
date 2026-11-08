"""工具返回值的标准结构。

## 为什么单独一个文件

这些函数原本定义在 `core/tools.py` 里。但当通用工具集（`core/tools_general.py`）
也从 `tools.py` 导入它们时，就形成了三方循环：

    store → tools_general → tools → tools_general（未初始化）

把返回结构这种「零依赖的纯函数」提到最底层，循环自然消失。
这也是分层的基本原则：**越底层的模块越不该依赖上层**。

本模块只有标准库依赖，任何工具模块都可以安全导入。
"""
from __future__ import annotations

from typing import Any


def ok_result(tool: str, note: str, **extra: Any) -> dict:
    return {"tool": tool, "connected": True, "found": True, "note": note, **extra}


def miss_result(tool: str, note: str, **extra: Any) -> dict:
    return {"tool": tool, "connected": True, "found": False, "note": note, **extra}


def empty_result(tool: str, note: str, **extra: Any) -> dict:
    """正常但为空（「没有待办」「还没记过你」）。

    必须与 miss_result 分开：这类答案是事实本身，不是知识库缺资料，
    管线不能因此开升级工单——否则用户每问一次空列表就多一张票。

    注意契约字段是 `status="empty"`（不是 `empty=True`）：管线靠 status 判别，
    写成布尔标志会让「空结果」被当成「没查到」而误升级（已由
    tests/test_tools_secretary.py 的 3 条用例锁死）。
    """
    return {"tool": tool, "connected": True, "found": False, "status": "empty",
            "note": note, **extra}


def off_result(tool: str, note: str, **extra: Any) -> dict:
    return {"tool": tool, "connected": False, "found": False, "note": note, **extra}


def need_ctx(tool: str, what: str) -> dict:
    return {"tool": tool, "connected": True, "found": False, "status": "need_context",
            "note": f"缺少必要信息：{what}。请向用户确认后再试。"}
