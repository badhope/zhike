"""通用工具：任何账号都能用、不绑定业务场景的能力。

这些工具的失败模式很特别——它们「注册成功但永远不被调用」时最难发现。
所以这里既测函数行为，也测它们确实出现在 active_tools 里。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def db(monkeypatch, tmp_path):
    monkeypatch.setenv("CS_AGENT_DB", str(tmp_path / "tools.db"))
    from core import store

    store._SCHEMA_READY.clear()
    store.init_db()
    return tmp_path


def test_all_general_tools_are_registered(db):
    """注册表里的通用工具必须真的出现在 active_tools（写库）里。

    项目里 tools 的真相源是数据库表，不是代码字典。只在代码里定义、忘了
    播种，会得到「工具存在但模型永远看不到」——这类缺陷没有任何报错。
    """
    from core import tools, tools_general

    active = set(tools.active_tools({"tools": {}}).keys())
    expected = set(tools_general.SYNC_TOOLS) | set(tools_general.ASYNC_TOOLS)
    missing = expected - active
    assert not missing, f"这些通用工具没有写进 tools 表：{sorted(missing)}"


def test_unit_conversion_length_and_temp():
    from core.tools_general import tool_convert_unit

    r = tool_convert_unit(value="5", from_unit="km", to_unit="英里")
    assert r["found"]
    assert "3.106" in r["note"]

    r2 = tool_convert_unit(value="100", from_unit="c", to_unit="f")
    assert r2["found"] and abs(r2["value"] - 212) < 0.01


def test_unit_conversion_rejects_mixed_dimensions():
    """长度和重量不能互换——必须报错而不是给出无意义的数字。"""
    from core.tools_general import tool_convert_unit

    r = tool_convert_unit(value="1", from_unit="km", to_unit="kg")
    assert not r["found"]
    assert "不能互换" in r["note"]


def test_timestamp_roundtrip():
    from core.tools_general import tool_timestamp

    to_date = tool_timestamp(value="1730000000", action="to_date")
    assert to_date["found"]
    assert "2024" in to_date["note"]

    back = tool_timestamp(value="2024-10-27", action="to_ts")
    assert back["found"] and back["timestamp"] > 0


def test_base64_roundtrip():
    from core.tools_general import tool_base64

    enc = tool_base64(text="你好世界", action="encode")
    assert enc["found"]
    dec = tool_base64(text=enc["encoded"], action="decode")
    assert dec["decoded"] == "你好世界"


def test_hash_known_vector():
    from core.tools_general import tool_hash

    r = tool_hash(text="abc", algorithm="sha256")
    assert r["digest"] == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_json_format_reports_syntax_errors():
    from core.tools_general import tool_json_format

    ok = tool_json_format(text='{"a":1}')
    assert ok["found"] and '"a"' in ok["note"]

    bad = tool_json_format(text='{"a":}')
    assert not bad["found"]
    assert "语法错误" in bad["note"]


def test_regex_finds_and_reports_positions():
    from core.tools_general import tool_regex_test

    r = tool_regex_test(pattern=r"\d+", text="abc123def456")
    assert r["found"] and r["count"] == 2
    assert r["matches"][0]["match"] == "123"


def test_color_parses_hex_with_letters():
    """#0E5C55 里含字母，曾经的实现把数字抠出来当 RGB，得出 #000537。

    这是一个真实出现过的解析 bug：判断分支看的是「提取到的数字个数」，
    而十六进制里的 E/C 不是数字，导致漏判成 3 个分量。
    """
    from core.tools_general import tool_color

    r = tool_color(hex_value="#0E5C55")
    assert r["hex"] == "#0E5C55", f"应保持原色，实际 {r['hex']}"
    assert r["rgb"] == [14, 92, 85]

    short = tool_color(hex_value="#fff")
    assert short["hex"] == "#FFFFFF"

    from_rgb = tool_color(hex_value="14,92,85")
    assert from_rgb["hex"] == "#0E5C55"


def test_password_has_required_classes():
    from core.tools_general import tool_password

    r = tool_password(length="20")
    pwd = r["note"]
    assert len(pwd) == 20
    assert any(c.isupper() for c in pwd)
    assert any(c.islower() for c in pwd)
    assert any(c.isdigit() for c in pwd)


def test_random_pick_respects_count_and_pool():
    from core.tools_general import tool_random_pick

    r = tool_random_pick(items="A,B,C,D", count="2")
    assert r["found"]
    assert len(r["picked"]) == 2
    assert len(set(r["picked"])) == 2, "不能重复抽同一个"
    assert set(r["picked"]) <= {"A", "B", "C", "D"}


# ---------------------------------------------------------------- 规则路由

def test_rule_routing_hits_general_tools(db):
    """无模型 Key 时（规则模式），这些明确问法也必须能路由到正确工具。

    重点覆盖两个曾经踩过的坑：
    1. 「100摄氏度等于多少华氏度」——宽泛的单位正则会把「等于」前的字吞进单位；
    2. 「时间戳 1730000000 是几号」——含"戳"字会被 qq_poke 的「戳一戳」规则抢走。
    """
    from core import tools

    cases = [
        ("5km 换算成英里", "convert_unit"),
        ("100摄氏度等于多少华氏度", "convert_unit"),
        ("时间戳 1730000000 是几号", "timestamp"),
        ("现在时间戳", "timestamp"),
        ("base64 编码 你好", "base64"),
        ("base64 解码 5L2g5aW9", "base64"),
        ("sha256 abc", "hash"),
        ('json 格式化 {"a":1}', "json_format"),
    ]
    for query, expect in cases:
        got = tools.detect_tool(query)[0]
        assert got == expect, f"{query!r} 应路由到 {expect}，实际 {got}"


def test_rule_routing_does_not_break_platform_actions(db):
    """通用路由不能抢走平台动作：戳一戳必须仍然是 qq_poke。"""
    from core import tools

    # 只断言项目实际支持的形态。「拍一拍我」本来就不在 qq_poke 的匹配规则里
    # （既有行为，与本次改动无关），拿它当断言会得到一个假的失败。
    assert tools.detect_tool("戳一戳他")[0] == "qq_poke"
    assert tools.detect_tool("戳一下")[0] == "qq_poke"


def test_rule_routing_leaves_chitchat_alone(db):
    """闲聊不能被误路由 —— 误判比不判更糟（会把问候变成工具调用）。"""
    from core import tools

    for q in ["你好呀", "今天心情不错", "陪我聊会儿"]:
        assert tools.detect_tool(q)[0] is None, f"{q!r} 不该被路由"


# ---------------------------------------------------------------- 隐私约束

def test_qr_code_never_sends_content_to_third_party():
    """二维码必须在本地生成。

    这是一个真实的隐私缺陷：原实现在缺 qrcode 库时退回 api.qrserver.com，
    把用户输入（可能是密码、地址、私密链接）明文发到第三方服务器。
    测试锁死：结果里不能出现任何外部域名。
    """
    from core.tools_general import tool_qr_code

    secret = "我的密码是 hunter2"
    r = tool_qr_code(text=secret)

    def _walk(obj):
        if isinstance(obj, str):
            yield obj
        elif isinstance(obj, dict):
            for v in obj.values():
                yield from _walk(v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                yield from _walk(v)

    joined = " ".join(_walk(r))
    for domain in ("qrserver", "api.qr", "googleapis", "chart.googleapis"):
        assert domain not in joined, f"二维码内容被发到了第三方（{domain}）"


def test_qr_code_returns_local_data_url():
    """正常路径必须返回可直接发送的 data URL，而不是需要外部下载的链接。"""
    from core.tools_general import tool_qr_code

    r = tool_qr_code(text="https://example.com")
    assert r["found"] is True
    assert r.get("source") == "local"
    assert r["image_data_url"].startswith("data:image/png;base64,")
