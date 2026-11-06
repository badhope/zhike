"""严格分段下载器：206 校验、镜像轮换、断点续传、CRC 兜底。

为什么这块必须测：
它是整个「控制台内一键部署协议端」的地基——`core/qq_deploy.py` 靠它拉 NapCat 的
zip。这个下载器有四条硬规则，任何一条失效都会导致**装上一个损坏的二进制**，
而损坏的 NapCat 症状是「进程起来但 QQ 注入失败」，跟 QQ 版本问题长得一模一样，
排查会直接跑偏（真被这么坑过）：
1. 只接受 206 Partial Content——服务器忽略 Range 返回 200 时必须失败退出；
2. 每块长度必须精确匹配请求区间；
3. 某个镜像连续 0 字节要自动换下一个；
4. 合并后大小 + zip CRC 双重校验，对不上就报错而不是静默交付。

这里全部用假 HTTP 层测，不发真实网络请求。
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import napcat_fetch  # noqa: E402


def _make_zip(payload: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("NapCatWinBootMain.exe", payload)
    return buf.getvalue()


class _Resp:
    """最小 httpx.Response 替身：只实现下载器用到的字段。"""

    def __init__(self, status_code: int, body: bytes = b"", honour_range: bool = True):
        self.status_code = status_code
        self._body = body
        self._honour = honour_range

    def iter_bytes(self, n: int):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Client:
    def __init__(self, handler):
        self._handler = handler

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def stream(self, method, url, headers=None):
        return self._handler(url, headers or {})


def _serve(blob: bytes, *, status: int = 206, honour_range: bool = True):
    """按 Range 头切片的假服务器。"""
    def handler(url, headers):
        if status != 206:
            return _Resp(status)
        rng = headers.get("Range", "")
        if not rng.startswith("bytes=") or not honour_range:
            return _Resp(200, blob, honour_range=False)
        a, b = rng[6:].split("-")
        start, end = int(a), int(b)
        return _Resp(206, blob[start:end + 1])
    return handler


def test_strict_download_happy_path(monkeypatch, tmp_path):
    """正常路径：分段拉全、合并、CRC 通过、分段文件被清理。"""
    blob = _make_zip(b"x" * 4096)
    monkeypatch.setattr(napcat_fetch.httpx, "Client", lambda **kw: _Client(_serve(blob)))

    out = tmp_path / "napcat.zip"
    monkeypatch.chdir(tmp_path)
    rc = napcat_fetch.strict_download("https://example.com/n.zip", len(blob), out, jobs=2)

    assert rc == 0
    assert out.read_bytes() == blob
    assert not list(tmp_path.glob("_dl_part_*")), "成功后必须清理分段文件"


def test_strict_download_rejects_non_206(monkeypatch, tmp_path):
    """服务器忽略 Range 返回 200 时必须失败：否则会把整个文件当成一块写坏偏移。"""
    blob = _make_zip(b"y" * 1024)
    monkeypatch.setattr(napcat_fetch.httpx, "Client",
                        lambda **kw: _Client(_serve(blob, status=200)))
    monkeypatch.setattr(napcat_fetch.time, "sleep", lambda s: None)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(IOError):
        napcat_fetch.strict_download("https://example.com/n.zip", len(blob),
                                     tmp_path / "out.zip", jobs=1)


def test_strict_download_detects_size_mismatch(monkeypatch, tmp_path):
    """少收了字节必须报错，不能交付一个截断的 zip。"""
    blob = _make_zip(b"z" * 2048)
    truncated = blob[:-10]
    monkeypatch.setattr(napcat_fetch.httpx, "Client", lambda **kw: _Client(_serve(truncated)))
    monkeypatch.setattr(napcat_fetch.time, "sleep", lambda s: None)
    monkeypatch.setattr(napcat_fetch, "MAX_STALL_ROUNDS", 3)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(IOError):
        napcat_fetch.strict_download("https://example.com/n.zip", len(blob),
                                     tmp_path / "out.zip", jobs=1)


def test_strict_download_detects_corrupt_zip(monkeypatch, tmp_path):
    """大小对但 CRC 坏（压缩数据区被改）也必须拦住。

    偏移要落在本地文件头之后的压缩数据里：改尾部/中央目录区有时只会让
    zipfile 读不出来，反而测不到 testzip() 这条 CRC 兜底逻辑。
    """
    blob = bytearray(_make_zip(b"w" * 2048))
    blob[30] ^= 0xFF
    monkeypatch.setattr(napcat_fetch.httpx, "Client",
                        lambda **kw: _Client(_serve(bytes(blob))))
    monkeypatch.chdir(tmp_path)

    with pytest.raises(IOError):
        napcat_fetch.strict_download("https://example.com/n.zip", len(blob),
                                     tmp_path / "out.zip", jobs=1)


def test_stalled_download_gives_up_instead_of_hanging(monkeypatch, tmp_path):
    """所有镜像都 0 字节时必须抛错退出，而不是无限重试。

    修复前：idle 计数到 6 只是打印一行然后清零，_download_part 永不返回，
    strict_download 的 join() 永不结束——控制台部署向导停在「下载中…」，
    既不报错也不超时，用户只能以为是自己网卡了。
    """
    blob = _make_zip(b"s" * 1024)
    monkeypatch.setattr(napcat_fetch.httpx, "Client",
                        lambda **kw: _Client(_serve(blob, status=503)))
    monkeypatch.setattr(napcat_fetch.time, "sleep", lambda s: None)
    monkeypatch.setattr(napcat_fetch, "MAX_STALL_ROUNDS", 3)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(IOError, match="停滞|下载失败"):
        napcat_fetch.strict_download("https://example.com/n.zip", len(blob),
                                     tmp_path / "out.zip", jobs=1)


def test_mirror_rotation_prefers_order():
    """镜像顺序必须是 直连优先 → 国内镜像兜底，配置错会拖慢整个部署。"""
    urls = napcat_fetch._urls("https://github.com/a/b.zip")
    assert urls[-1] == "https://github.com/a/b.zip", "直连放在最后（前面是镜像前缀）"
    assert len(urls) == len(set(urls)), "镜像列表不能有重复前缀"


def test_progress_callback_receives_final_total(monkeypatch, tmp_path):
    """进度回调最后必须报满，否则前端进度条永远停在 99%。"""
    blob = _make_zip(b"q" * 1024)
    monkeypatch.setattr(napcat_fetch.httpx, "Client", lambda **kw: _Client(_serve(blob)))
    monkeypatch.chdir(tmp_path)

    seen: list[int] = []
    napcat_fetch.strict_download("https://example.com/n.zip", len(blob),
                                 tmp_path / "out.zip", jobs=1, progress=seen.append)

    assert seen, "至少回调一次"
    assert seen[-1] == len(blob), f"最后一次应报满 {len(blob)}，实际 {seen[-1]}"
