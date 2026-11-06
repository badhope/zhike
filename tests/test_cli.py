"""CLI 与路径层：用户接触程序的第一道门。

为什么必须测：CLI 出错时用户拿不到任何有用信息，甚至直接崩在第一步。
这里守四件事：
1. `init` 在空目录能建出可用环境，且令牌只出现一次（不重复键）
2. `init` 不覆盖用户已填的配置
3. `run` 遇到未初始化会自动初始化，而不是甩一句「先跑 init」就退出
4. 路径层在「打包」与「源码」两种模式下都把可写数据和只读资源分开
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import cli, paths  # noqa: E402


@pytest.fixture()
def fresh_root(tmp_path, monkeypatch):
    """把 CLI 的 ROOT/ENV_FILE 指向临时目录，模拟全新安装。"""
    example = tmp_path / ".env.example"
    example.write_text(
        "# 示例配置\nADMIN_TOKEN=\nCHANNEL_TOKEN=\nCS_AGENT_DB=cs_agent.db\n",
        encoding="utf-8",
    )
    (tmp_path / "VERSION").write_text("9.9.9", encoding="utf-8")

    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(cli, "ENV_EXAMPLE", example)
    monkeypatch.setattr(cli, "VERSION_FILE", tmp_path / "VERSION")
    monkeypatch.setenv("CS_AGENT_DB", str(tmp_path / "cs_agent.db"))
    return tmp_path


def test_init_creates_env_with_single_token_lines(fresh_root, monkeypatch):
    """令牌必须原地填空，不能在文件末尾再追加一份同名键。"""
    monkeypatch.setattr(cli, "paths", type("P", (), {
        "resource": staticmethod(lambda *p: fresh_root.joinpath(*p)),
        "writable_dir": staticmethod(lambda: fresh_root),
    }))

    assert cli.main(["init"]) == 0
    text = (fresh_root / ".env").read_text(encoding="utf-8")
    assert text.count("ADMIN_TOKEN=") == 1, "不该出现两个 ADMIN_TOKEN 键"
    assert text.count("CHANNEL_TOKEN=") == 1

    from dotenv import dotenv_values

    values = dotenv_values(fresh_root / ".env")
    assert len(values["ADMIN_TOKEN"]) == 64, "32 字节随机 = 64 个十六进制字符"
    assert values["ADMIN_TOKEN"] != values["CHANNEL_TOKEN"], "两个令牌不能相同"


def test_init_preserves_existing_values(fresh_root, monkeypatch):
    """用户已经填好的令牌/Key 不能被 init 覆盖。"""
    env = fresh_root / ".env"
    env.write_text("ADMIN_TOKEN=my-own-token\nCHANNEL_TOKEN=my-channel\n", encoding="utf-8")
    monkeypatch.setattr(cli, "paths", type("P", (), {
        "resource": staticmethod(lambda *p: fresh_root.joinpath(*p)),
        "writable_dir": staticmethod(lambda: fresh_root),
    }))

    assert cli.main(["init"]) == 0
    text = env.read_text(encoding="utf-8")
    assert "my-own-token" in text
    assert "my-channel" in text


def test_run_auto_inits_when_env_missing(fresh_root, monkeypatch):
    """未初始化直接 run 时应自动 init，而不是报错退出。"""
    monkeypatch.setattr(cli, "paths", type("P", (), {
        "resource": staticmethod(lambda *p: fresh_root.joinpath(*p)),
        "writable_dir": staticmethod(lambda: fresh_root),
    }))
    # 让 run 在通过初始化后立刻停下，不去真的起服务。
    # 注意：cmd_run 内部是 `import uvicorn` 局部导入，直接替换 sys.modules
    # 不生效（uvicorn 已在别处 import 过），必须给真实模块的 run 打补丁。
    class Stop(Exception):
        pass

    import uvicorn

    def fake_uvicorn_run(*a, **kw):
        raise Stop()

    monkeypatch.setattr(uvicorn, "run", fake_uvicorn_run)
    monkeypatch.setattr(cli, "_port_free", lambda *a, **kw: True)

    # main() 会把任何异常收成「人话 + 退出码」，这正是 CLI 该有的行为；
    # 这里断言的是「uvicorn.run 真的被调用到了」，而不是异常穿透。
    called = {"n": 0}

    def fake_uvicorn_run(*a, **kw):
        called["n"] += 1

    monkeypatch.setattr(uvicorn, "run", fake_uvicorn_run)

    assert cli.main(["run", "--port", "9999"]) == 0
    assert called["n"] == 1, "初始化之后必须继续启动服务"
    assert (fresh_root / ".env").exists(), "run 应该顺手把环境初始化好"


def test_paths_frozen_mode_separates_writable_from_bundle(monkeypatch, tmp_path):
    """打包模式：只读资源来自 _MEIPASS，可写数据来自 exe 同级或 LOCALAPPDATA。"""
    bundle = tmp_path / "meipass"
    bundle.mkdir()
    exe_dir = tmp_path / "app"
    exe_dir.mkdir()

    monkeypatch.setattr(paths, "IS_FROZEN", True)
    monkeypatch.setattr(paths.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(paths.sys, "executable", str(exe_dir / "zhike.exe"))
    monkeypatch.setattr(paths, "_CACHED", None)
    monkeypatch.delenv("ZHIKE_HOME", raising=False)

    assert paths.bundle_dir() == bundle
    w = paths.writable_dir()
    assert w != bundle, "可写目录绝不能是 _MEIPASS（退出即删）"
    assert w == exe_dir / "zhike-data", "优先用 exe 同级目录（绿色版可整体搬走）"


def test_paths_respects_zhike_home(monkeypatch, tmp_path):
    """显式指定 ZHIKE_HOME 时优先用它。"""
    custom = tmp_path / "custom-home"
    monkeypatch.setattr(paths, "IS_FROZEN", True)
    monkeypatch.setattr(paths.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(paths, "_CACHED", None)
    monkeypatch.setenv("ZHIKE_HOME", str(custom))

    assert paths.writable_dir() == custom
    assert custom.exists()


def test_paths_source_mode_uses_repo_root(monkeypatch):
    """源码模式：可写目录就是仓库根，与既有 .env/.tmp 布局一致。"""
    monkeypatch.setattr(paths, "IS_FROZEN", False)
    monkeypatch.setattr(paths, "_CACHED", None)
    root = paths.writable_dir()
    assert (root / "pyproject.toml").exists(), "源码模式应解析到仓库根"


def test_cli_symbols_degrade_to_ascii(monkeypatch):
    """控制台不支持 UTF-8 符号时必须降级成 ASCII。

    真实事故：Windows 默认代码页 GBK，exe 首次 init 直接
    `UnicodeEncodeError: 'gbk' codec can't encode character '✓'`，
    用户双击程序后什么都做不了。所以降级判据必须可测。
    """
    import importlib

    monkeypatch.setattr(cli.sys, "stdout", type("FakeOut", (), {
        "encoding": "gbk", "isatty": staticmethod(lambda: False),
        "reconfigure": staticmethod(lambda **kw: None),
        "write": staticmethod(lambda s: None), "flush": staticmethod(lambda: None),
    })())
    reloaded = importlib.reload(cli)
    try:
        assert reloaded._SYMBOLS is False, "GBK 控制台必须判定为不支持符号"
        assert reloaded._MARK_OK == "[OK]"
        assert reloaded._MARK_BAD == "[X]"
        # 不抛异常是底线
        reloaded.ok("测试")
        reloaded.warn("测试")
        reloaded.bad("测试")
    finally:
        importlib.reload(cli)


def test_cli_symbols_kept_on_utf8(monkeypatch):
    """UTF-8 终端保留符号，界面才不至于退化成 [OK]。"""
    import importlib

    monkeypatch.setattr(cli.sys, "stdout", type("FakeOut", (), {
        "encoding": "utf-8", "isatty": staticmethod(lambda: False),
        "reconfigure": staticmethod(lambda **kw: None),
        "write": staticmethod(lambda s: None), "flush": staticmethod(lambda: None),
    })())
    reloaded = importlib.reload(cli)
    try:
        assert reloaded._SYMBOLS is True
        assert reloaded._MARK_OK == "✓"
    finally:
        importlib.reload(cli)
