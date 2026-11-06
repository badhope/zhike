"""版本自检与 tracing 开关：两处都是「静默失效」的高危点。

为什么值得单独测：
- 版本号三处不一致时，服务照常启动，只有 /healthz 和前端关于页各说各话；
  线上排查「用户说版本不对」会白查半天。
- tracing 没装依赖时必须整体 no-op，而不是每次请求抛 ImportError；
  但装了依赖却没按环境变量开启，就等于以为有链路追踪、实际全是空 span。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------- core/version.py ----------------

def test_version_matches_version_file():
    """VERSION 文件与 core/version.py 必须一致（发布流程的硬约束）。"""
    from core import version

    root = Path(__file__).resolve().parent.parent
    assert (root / "VERSION").read_text(encoding="utf-8").strip() == version.VERSION


def test_version_tuple_parses_plain_and_prefixed():
    from core.version import version_tuple

    assert version_tuple("1.5.0") == (1, 5, 0)
    assert version_tuple("2.0.1-rc.3") == (2, 0, 1), "预发布后缀不能影响数字部分"
    assert version_tuple("1.2") == (1, 2, 0), "缺段要补零，否则比较会出错"


def test_version_info_shape():
    from core.version import VERSION, version_info

    info = version_info()
    assert info["version"] == VERSION
    assert info["channel"] == "stable"
    major, minor, patch = VERSION.split(".")
    assert (info["major"], info["minor"], info["patch"]) == (int(major), int(minor), int(patch))


def test_version_strict_mode_raises_on_mismatch(monkeypatch):
    """开启 ZHIKE_VERSION_STRICT 后不一致必须直接拒绝启动，而不是只打日志。"""
    from core import version

    monkeypatch.setenv("ZHIKE_VERSION_STRICT", "1")
    monkeypatch.setattr(version, "_read_version_file", lambda: "9.9.9")
    with pytest.raises(RuntimeError, match="版本不一致"):
        version._self_check()


def test_version_strict_mode_passes_when_consistent(monkeypatch):
    """strict 模式下版本一致就不应该报错（否则等于永远起不来）。"""
    from core import version

    monkeypatch.setenv("ZHIKE_VERSION_STRICT", "1")
    monkeypatch.setattr(version, "_read_version_file", lambda: version.VERSION)
    version._self_check()


def test_version_strict_mode_ignores_missing_file(monkeypatch):
    """打包后 VERSION 文件可能不存在，strict 模式也不能因此拒绝启动。"""
    from core import version

    monkeypatch.setenv("ZHIKE_VERSION_STRICT", "1")
    monkeypatch.setattr(version, "_read_version_file", lambda: None)
    version._self_check()


def test_version_strict_off_tolerates_mismatch(monkeypatch):
    """默认（非 strict）不因版本文件缺失或过时而让服务起不来。"""
    from core import version

    monkeypatch.delenv("ZHIKE_VERSION_STRICT", raising=False)
    monkeypatch.setattr(version, "_read_version_file", lambda: "9.9.9")
    version._self_check()  # 不应抛异常


# ---------------- core/tracing.py ----------------

def test_tracing_noop_when_disabled(monkeypatch):
    """没配 OTEL 环境变量时，span() 必须是可用的 no-op 上下文。"""
    from core import tracing

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    monkeypatch.setattr(tracing, "_INITIALIZED", False)
    monkeypatch.setattr(tracing, "_TRACER", None)

    with tracing.span("test.op", {"k": "v"}) as s:
        assert s is None, "未启用时应 yield None，调用方用 `if s:` 判空即可"


def test_tracing_noop_when_sdk_missing(monkeypatch):
    """配了 endpoint 但没装 opentelemetry 时必须降级为 no-op，不能让请求失败。"""
    from core import tracing

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4317")
    monkeypatch.setattr(tracing, "_INITIALIZED", False)
    monkeypatch.setattr(tracing, "_TRACER", None)
    # 让 import opentelemetry 必然失败，模拟未安装可选依赖
    monkeypatch.setitem(sys.modules, "opentelemetry", None)
    monkeypatch.setitem(sys.modules, "opentelemetry.trace", None)

    with tracing.span("test.op") as s:
        assert s is None


def test_tracing_span_swallows_attribute_errors(monkeypatch):
    """给 span 设属性失败（非字符串值/只读 span）不能影响业务主流程。"""
    from core import tracing

    class BadSpan:
        def set_attribute(self, k, v):
            raise RuntimeError("attribute rejected")

    class FakeTracer:
        def start_as_current_span(self, name):
            import contextlib

            @contextlib.contextmanager
            def _cm():
                yield BadSpan()

            return _cm()

    monkeypatch.setattr(tracing, "_INITIALIZED", True)
    monkeypatch.setattr(tracing, "_TRACER", FakeTracer())

    with tracing.span("test.op", {"bad": object()}) as s:
        assert isinstance(s, BadSpan), "属性设置失败不应中断上下文"
