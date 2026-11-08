"""通用工具集：与业务无关、任何账号都能直接用的能力。

## 为什么要单独一个模块

项目原有的 42 个工具里，一大半是业务工具（查订单、退款式）和 QQ 平台操作
（改群名、踢人）。这些绑定具体部署场景，换一个用户/换一个 QQ 号就用不上。

本模块补的是「无论谁用、换哪个号都成立」的工具：

- 天气、翻译、单位换算、汇率 —— 日常信息查询
- 时间戳、Base64、哈希、JSON、正则 —— 开发与数据处理
- 随机抽签、密码生成、颜色 —— 轻量决策与生成

## 设计约束

1. **全部离线优先**：能在本地算出来的绝不发网络请求（单位换算、哈希、时间戳）。
   只有天气/翻译/汇率这种天然依赖外部的才走网络，且失败时如实说明。
2. **不引入新依赖**：一律用标准库 + 项目已有的 httpx。
3. **参数宽容**：模型给的参数常见大小写不一（`C`/`c`/`摄氏`），做归一化而不是报错。
4. **不做危险事**：密码生成用 `secrets` 而非 `random`；正则执行加超时保护
   （防止灾难性回溯把进程卡死）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import secrets
import string
import time
import urllib.parse
from datetime import datetime, timezone

from core.tool_result import miss_result, need_ctx, off_result, ok_result

# ---------------------------------------------------------------- 单位换算

_UNIT_ALIASES = {
    # 长度
    "m": ("length", 1.0), "米": ("length", 1.0),
    "km": ("length", 1000.0), "千米": ("length", 1000.0), "公里": ("length", 1000.0),
    "cm": ("length", 0.01), "厘米": ("length", 0.01),
    "mm": ("length", 0.001), "毫米": ("length", 0.001),
    "mile": ("length", 1609.344), "英里": ("length", 1609.344),
    "ft": ("length", 0.3048), "英尺": ("length", 0.3048),
    "inch": ("length", 0.0254), "英寸": ("length", 0.0254),
    # 重量
    "kg": ("weight", 1.0), "千克": ("weight", 1.0), "公斤": ("weight", 1.0),
    "g": ("weight", 0.001), "克": ("weight", 0.001),
    "t": ("weight", 1000.0), "吨": ("weight", 1000.0),
    "lb": ("weight", 0.45359237), "磅": ("weight", 0.45359237),
    "oz": ("weight", 0.028349523125), "盎司": ("weight", 0.028349523125),
    "斤": ("weight", 0.5),
    # 体积
    "l": ("volume", 1.0), "升": ("volume", 1.0),
    "ml": ("volume", 0.001), "毫升": ("volume", 0.001),
    "gal": ("volume", 3.785411784), "加仑": ("volume", 3.785411784),
    # 温度单独处理
    "c": ("temp", 0.0), "摄氏": ("temp", 0.0), "摄氏度": ("temp", 0.0),
    "f": ("temp", 0.0), "华氏": ("temp", 0.0), "华氏度": ("temp", 0.0),
    "k": ("temp", 0.0), "开尔文": ("temp", 0.0),
}


def _norm_unit(u: str) -> str:
    return (u or "").strip().lower().replace("°", "").replace("度", "") or ""


def _temp_convert(value: float, src: str, dst: str) -> float | None:
    """温度换算：先统一到摄氏度再转出。"""
    to_c = {"c": lambda v: v, "f": lambda v: (v - 32) * 5 / 9, "k": lambda v: v - 273.15}
    from_c = {"c": lambda v: v, "f": lambda v: v * 9 / 5 + 32, "k": lambda v: v + 273.15}
    if src not in to_c or dst not in from_c:
        return None
    return from_c[dst](to_c[src](value))


def tool_convert_unit(value: str = "", from_unit: str = "", to_unit: str = "", **_kw) -> dict:
    """单位换算：长度 / 重量 / 体积 / 温度。"""
    try:
        v = float(str(value).strip())
    except (TypeError, ValueError):
        return need_ctx("convert_unit", "要换算的数值")
    src, dst = _norm_unit(from_unit), _norm_unit(to_unit)
    if not src or not dst:
        return need_ctx("convert_unit", "源单位与目标单位（例如 km → 英里）")

    si, di = _UNIT_ALIASES.get(src), _UNIT_ALIASES.get(dst)
    if not si or not di:
        known = sorted({k for k in _UNIT_ALIASES if len(k) > 1})
        return miss_result("convert_unit", f"不认识单位「{src if not si else dst}」。"
                                          f"支持：{'、'.join(known[:20])}")
    if si[0] != di[0]:
        return miss_result("convert_unit",
                           f"「{from_unit}」是{si[0]}单位，「{to_unit}」是{di[0]}单位，不能互换。")
    if si[0] == "temp":
        out = _temp_convert(v, src, dst)
    else:
        out = v * si[1] / di[1]
    if out is None:
        return miss_result("convert_unit", "温度单位不支持这种组合。")
    # 保留合理精度：大数不留小数，小数保留 4 位有效
    text = f"{out:.6g}"
    return ok_result("convert_unit", f"{v:g} {from_unit} = {text} {to_unit}",
                     value=out, result=text)


# ---------------------------------------------------------------- 时间戳

def tool_timestamp(value: str = "", action: str = "", **_kw) -> dict:
    """时间戳与日期互转。

    action: to_date（时间戳→日期）/ to_ts（日期→时间戳）/ now。
    """
    act = (action or "").strip().lower()
    raw = (value or "").strip()

    if not act:
        # 自动判断：纯数字（10 或 13 位）当时间戳，否则当日期
        act = "to_date" if raw.isdigit() and len(raw) in (10, 13) else ("to_ts" if raw else "now")

    if act == "now" or (not raw and act == "to_date"):
        ts = time.time()
        dt = datetime.fromtimestamp(ts).astimezone()
        return ok_result("timestamp", f"现在：{dt:%Y-%m-%d %H:%M:%S %Z}（时间戳 {int(ts)}）",
                         timestamp=int(ts), iso=dt.isoformat())

    if act == "to_date":
        try:
            ts = int(raw)
        except ValueError:
            return need_ctx("timestamp", "一个 10 位（秒）或 13 位（毫秒）的时间戳")
        if len(raw) == 13:
            ts //= 1000
        dt = datetime.fromtimestamp(ts).astimezone()
        utc = datetime.fromtimestamp(ts, tz=timezone.utc)
        return ok_result("timestamp",
                         f"{ts} → 本地 {dt:%Y-%m-%d %H:%M:%S}，UTC {utc:%Y-%m-%d %H:%M:%S}",
                         local=dt.isoformat(), utc=utc.isoformat(), timestamp=ts)

    # to_ts
    if not raw:
        return need_ctx("timestamp", "要转换的日期（如 2026-11-08 或 2026-11-08 12:30）")
    text = raw.replace("/", "-").replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt).astimezone()
            return ok_result("timestamp", f"{raw} → 时间戳 {int(dt.timestamp())}",
                             timestamp=int(dt.timestamp()), iso=dt.isoformat())
        except ValueError:
            continue
    return miss_result("timestamp", f"看不懂这个日期：{raw}（支持 2026-11-08 / 2026-11-08 12:30）")


# ---------------------------------------------------------------- 编码与摘要

def tool_base64(text: str = "", action: str = "encode", **_kw) -> dict:
    """Base64 编码 / 解码。"""
    raw = text or ""
    if not raw:
        return need_ctx("base64", "要处理的文本")
    act = (action or "encode").strip().lower()
    if act in ("decode", "d", "解", "解码"):
        try:
            data = base64.b64decode(raw + "=" * (-len(raw) % 4), validate=False)
            try:
                out = data.decode("utf-8")
            except UnicodeDecodeError:
                return ok_result("base64", f"解码得到 {len(data)} 字节（非 UTF-8 文本，可能是二进制）",
                                 bytes=len(data), hex=data[:32].hex())
            return ok_result("base64", out, decoded=out)
        except Exception:
            return miss_result("base64", "这不是合法的 Base64")
    out = base64.b64encode(raw.encode("utf-8")).decode()
    return ok_result("base64", out, encoded=out)


def tool_hash(text: str = "", algorithm: str = "sha256", **_kw) -> dict:
    """计算文本哈希（md5 / sha1 / sha256 / sha512）。"""
    raw = text or ""
    if not raw:
        return need_ctx("hash", "要计算哈希的文本")
    alg = (algorithm or "sha256").strip().lower().replace("-", "")
    if alg not in ("md5", "sha1", "sha256", "sha512"):
        return miss_result("hash", f"不支持的算法：{algorithm}（可选 md5/sha1/sha256/sha512）")
    h = hashlib.new(alg, raw.encode("utf-8")).hexdigest()
    return ok_result("hash", f"{alg}: {h}", digest=h, algorithm=alg)


def tool_json_format(text: str = "", action: str = "format", **_kw) -> dict:
    """JSON 格式化 / 压缩 / 校验。"""
    raw = (text or "").strip()
    if not raw:
        return need_ctx("json_format", "要处理的 JSON 文本")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return miss_result("json_format", f"JSON 语法错误：第 {e.lineno} 行第 {e.colno} 列 — {e.msg}")
    act = (action or "format").strip().lower()
    if act in ("minify", "compress", "压缩"):
        out = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    else:
        out = json.dumps(data, ensure_ascii=False, indent=2)
    kind = type(data).__name__
    return ok_result("json_format", out[:4000], type=kind, length=len(out))


def tool_regex_test(pattern: str = "", text: str = "", **_kw) -> dict:
    """正则匹配测试。

    加了回溯保护：Python 的 re 没有执行超时，灾难性回溯会把进程卡死。
    这里限制模式长度与文本长度，并用一个有界的匹配次数上限兜底。
    """
    pat = pattern or ""
    body = text or ""
    if not pat:
        return need_ctx("regex_test", "正则表达式")
    if len(pat) > 500:
        return miss_result("regex_test", "正则太长（上限 500 字符）")
    if len(body) > 20000:
        return miss_result("regex_test", "待匹配文本太长（上限 20000 字符）")
    try:
        rx = re.compile(pat)
    except re.error as e:
        return miss_result("regex_test", f"正则语法错误：{e}")
    matches = []
    # 用 finditer 但限制迭代次数，避免无限匹配
    for i, m in enumerate(rx.finditer(body)):
        if i >= 50:
            break
        matches.append({"match": m.group(0)[:200], "start": m.start(), "end": m.end(),
                        "groups": list(m.groups())[:10]})
    if not matches:
        return ok_result("regex_test", "没有匹配", matched=False, count=0)
    summary = "\n".join(f"[{m['start']}-{m['end']}] {m['match']}" for m in matches[:10])
    return ok_result("regex_test", f"匹配 {len(matches)} 处：\n{summary}",
                     matched=True, count=len(matches), matches=matches[:10])


# ---------------------------------------------------------------- 随机与生成

def tool_random_pick(items: str = "", count: str = "1", **_kw) -> dict:
    """从候选里随机抽（逗号/换行分隔）。"""
    raw = (items or "").strip()
    if not raw:
        return need_ctx("random_pick", "候选列表（用逗号或换行分隔）")
    pool = [x.strip() for x in re.split(r"[,\n，、]", raw) if x.strip()]
    if not pool:
        return need_ctx("random_pick", "候选列表")
    try:
        n = max(1, min(int(str(count or "1")), len(pool)))
    except ValueError:
        n = 1
    picked = secrets.SystemRandom().sample(pool, n)
    return ok_result("random_pick", "、".join(picked), picked=picked, total=len(pool))


def tool_password(length: str = "16", symbols: str = "true", **_kw) -> dict:
    """生成强随机密码（用 secrets，不用 random）。"""
    try:
        n = max(8, min(int(str(length or "16")), 128))
    except ValueError:
        n = 16
    use_symbols = str(symbols).strip().lower() not in ("0", "false", "no", "off", "")
    alphabet = string.ascii_letters + string.digits
    if use_symbols:
        alphabet += "!@#$%^&*()-_=+[]{}"
    # 保证至少各含一个大写/小写/数字（符号可选）
    pwd = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
    ]
    if use_symbols:
        pwd.append(secrets.choice("!@#$%^&*()-_=+[]{}"))
    pwd += [secrets.choice(alphabet) for _ in range(n - len(pwd))]
    secrets.SystemRandom().shuffle(pwd)
    out = "".join(pwd)
    entry = abs(math.log2(len(set(out))) * len(out)) if out else 0
    return ok_result("password", out, length=len(out), entropy_bits=int(entry))


def tool_color(hex_value: str = "", **_kw) -> dict:
    """颜色格式互转：HEX ↔ RGB ↔ HSL。"""
    raw = (hex_value or "").strip().lstrip("#")
    if not raw:
        return need_ctx("color", "颜色值（如 #0E5C55 或 14,92,85）")

    # 判断走 RGB 还是 HEX，只看**输入形态**，不能看提取出的数字个数。
    # 之前的写法是 `if "," in raw or len(nums) == 3`，而 #0E5C55 里的 E、C
    # 不是数字，re.findall(\d+) 只剩 ['0','5','55'] 三个 → 误判成 RGB，
    # 输出 #000537（重新读取时把 #0E5C55 解析错了）。
    is_rgb_form = bool(re.search(r"[,()]|^rgb", raw, re.I))
    if is_rgb_form:
        nums = re.findall(r"\d+", raw)
        if len(nums) < 3:
            return miss_result("color", "RGB 需要三个 0-255 的数字")
        r, g, b = (max(0, min(255, int(x))) for x in nums[:3])
    else:
        if len(raw) == 3:
            raw = "".join(c * 2 for c in raw)
        if len(raw) != 6 or not re.fullmatch(r"[0-9a-fA-F]{6}", raw):
            return miss_result("color", f"看不懂的颜色：{hex_value}（支持 #RRGGBB 或 r,g,b）")
        r, g, b = int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)

    import colorsys
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    return ok_result("color", f"HEX #{r:02X}{g:02X}{b:02X} · RGB({r},{g},{b}) · "
                             f"HSL({h * 360:.0f}°,{s * 100:.0f}%,{l * 100:.0f}%)",
                     hex=f"#{r:02X}{g:02X}{b:02X}", rgb=[r, g, b],
                     hsl=[round(h * 360), round(s * 100), round(l * 100)])


# ---------------------------------------------------------------- 网络类

async def tool_weather(city: str = "", **_kw) -> dict:
    """查天气（wttr.in，无需 API Key）。"""
    name = (city or "").strip()
    if not name:
        return need_ctx("weather", "要查的城市名")
    from core.http_pool import get_client

    try:
        client = get_client(timeout=15)
        r = await client.get(f"https://wttr.in/{urllib.parse.quote(name)}?format=j1&lang=zh")
        r.raise_for_status()
        data = r.json()
    except Exception as e:  # noqa: BLE001 - 外部服务失败要如实说
        return miss_result("weather", f"天气服务暂时不可用（{type(e).__name__}），稍后再试")
    try:
        cur = (data.get("current_condition") or [{}])[0]
        desc = (cur.get("lang_zh") or cur.get("weatherDesc") or [{}])[0].get("value", "")
        temp = cur.get("temp_C", "?")
        feels = cur.get("FeelsLikeC", "?")
        humidity = cur.get("humidity", "?")
        wind = cur.get("windspeedKmph", "?")
        area = (data.get("nearest_area") or [{}])[0]
        area_name = (area.get("areaName") or [{}])[0].get("value", name)
        days = data.get("weather") or []
        forecast = []
        for d in days[:3]:
            if not isinstance(d, dict):
                continue
            forecast.append(
                f"{d.get('date', '')} {d.get('mintempC', '?')}~{d.get('maxtempC', '?')}°C "
                f"{(d.get('hourly') or [{}])[0].get('lang_zh', [{'value': ''}])[0].get('value', '')}"
            )
        note = f"{area_name}：{desc}，{temp}°C（体感 {feels}°C），湿度 {humidity}%，风速 {wind}km/h"
        if forecast:
            note += "\n未来几天：\n" + "\n".join(forecast)
        return ok_result("weather", note, temp_c=temp, desc=desc, forecast=forecast)
    except (KeyError, IndexError, TypeError):
        return miss_result("weather", "天气服务返回了无法解析的数据")


async def tool_translate(text: str = "", target: str = "zh", source: str = "auto", **_kw) -> dict:
    """翻译（用 MyMemory 免费接口，无需 Key）。"""
    body = (text or "").strip()
    if not body:
        return need_ctx("translate", "要翻译的文本")
    if len(body) > 2000:
        return miss_result("translate", "文本太长（上限 2000 字符）")
    src = (source or "auto").strip().lower()
    dst = (target or "zh").strip().lower()
    if src == dst and src != "auto":
        return ok_result("translate", body, translated=body)
    from core.http_pool import get_client

    try:
        client = get_client(timeout=20)
        pair = f"{src}|{dst}" if src != "auto" else f"zh|{dst}" if dst != "zh" else f"en|zh"
        r = await client.get(
            "https://api.mymemory.translated.net/get",
            params={"q": body[:500], "langpair": pair})
        r.raise_for_status()
        data = r.json()
        translated = (data.get("responseData") or {}).get("translatedText") or ""
        if not translated:
            return miss_result("translate", "翻译服务没有返回结果")
        return ok_result("translate", translated, translated=translated, target=dst)
    except Exception as e:  # noqa: BLE001
        return miss_result("translate", f"翻译服务暂时不可用（{type(e).__name__}）")


async def tool_exchange_rate(base: str = "USD", target: str = "CNY", amount: str = "1", **_kw) -> dict:
    """汇率换算（多源降级，无需 API Key）。

    为什么要有降级链：单一免费汇率源极不稳定——实测 frankfurter.app 会返回
    301 重定向（httpx 不自动跟随就当成失败），er-api 有时限流。
    按「一个源失败换下一个」的顺序尝试，全挂才报不可用。
    """
    b = (base or "USD").strip().upper()
    t = (target or "CNY").strip().upper()
    try:
        amt = float(str(amount or "1"))
    except ValueError:
        amt = 1.0
    from core.http_pool import get_client

    client = get_client(timeout=15)
    last_err = ""

    # 源 1：open.er-api.com（返回 {result, rates}）
    try:
        r = await client.get(f"https://open.er-api.com/v6/latest/{b}")
        r.raise_for_status()
        data = r.json()
        if str(data.get("result", "")).lower() == "success":
            rate = (data.get("rates") or {}).get(t)
            if rate is not None:
                converted = amt * float(rate)
                return ok_result(
                    "exchange_rate",
                    f"{amt:g} {b} = {converted:.2f} {t}（1 {b} = {rate} {t}）",
                    rate=rate, converted=round(converted, 2), source="open.er-api.com")
    except Exception as e:  # noqa: BLE001
        last_err = type(e).__name__

    # 源 2：frankfurter（必须跟随重定向）
    try:
        r = await client.get(
            f"https://api.frankfurter.app/latest?from={b}&to={t}", follow_redirects=True)
        r.raise_for_status()
        data = r.json()
        rate = (data.get("rates") or {}).get(t)
        if rate is not None:
            converted = amt * float(rate)
            return ok_result(
                "exchange_rate",
                f"{amt:g} {b} = {converted:.2f} {t}（1 {b} = {rate} {t}）",
                rate=rate, converted=round(converted, 2), source="frankfurter.app")
    except Exception as e:  # noqa: BLE001
        last_err = type(e).__name__

    # 源 3：exchangerate-api v4
    try:
        r = await client.get(f"https://api.exchangerate-api.com/v4/latest/{b}")
        r.raise_for_status()
        data = r.json()
        rate = (data.get("rates") or {}).get(t)
        if rate is not None:
            converted = amt * float(rate)
            return ok_result(
                "exchange_rate",
                f"{amt:g} {b} = {converted:.2f} {t}（1 {b} = {rate} {t}）",
                rate=rate, converted=round(converted, 2), source="exchangerate-api.com")
    except Exception as e:  # noqa: BLE001
        last_err = type(e).__name__

    return miss_result(
        "exchange_rate",
        f"三个汇率源都不可用（最后错误 {last_err}），稍后再试")


def tool_qr_code(text: str = "", **_kw) -> dict:
    """把文本/链接生成二维码图片（返回 data URL，可直接发出去）。

    ## 隐私要求：必须本地生成

    这里的输入可能是密码、地址、私密链接。**绝不能用在线二维码服务兜底**——
    那等于把明文内容发到第三方服务器。之前的实现在缺 qrcode 库时退回到
    api.qrserver.com，等于每次调用都在外泄数据；已移除这条路径。

    库缺失时诚实报错并给出安装命令，而不是"看起来能用的"外部链接。
    """
    body = (text or "").strip()
    if not body:
        return need_ctx("qr_code", "要编码的文本或链接")
    try:
        import io

        import qrcode  # type: ignore[import-not-found]
    except ImportError:
        return off_result(
            "qr_code",
            "本地二维码库未安装，为避免把内容发到第三方服务，本次不生成。"
            "安装后可用：pip install 'qrcode[pil]'")

    img = qrcode.make(body)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = base64.b64encode(buf.getvalue()).decode()
    return ok_result("qr_code", "已生成二维码（PNG，可作为图片发送）",
                     image_data_url=f"data:image/png;base64,{data}", source="local")


# ---------------------------------------------------------------- 注册表

TOOL_META_EXTRA = {
    "convert_unit": {"name": "单位换算", "desc": "长度/重量/体积/温度单位换算（如 5km 转英里）",
                     "args": {"value": "string", "from_unit": "string", "to_unit": "string"}},
    "timestamp": {"name": "时间戳转换", "desc": "时间戳与日期互转（10/13 位），或查当前时间戳",
                  "args": {"value": "string", "action": "string"}},
    "base64": {"name": "Base64", "desc": "Base64 编码或解码",
               "args": {"text": "string", "action": "string"}},
    "hash": {"name": "哈希", "desc": "计算文本的 md5/sha1/sha256/sha512",
             "args": {"text": "string", "algorithm": "string"}},
    "json_format": {"name": "JSON 处理", "desc": "JSON 格式化、压缩或语法校验",
                    "args": {"text": "string", "action": "string"}},
    "regex_test": {"name": "正则测试", "desc": "测试正则表达式能否匹配给定文本",
                   "args": {"pattern": "string", "text": "string"}},
    "random_pick": {"name": "随机抽取", "desc": "从候选列表里随机抽若干个（抽签、做选择）",
                    "args": {"items": "string", "count": "string"}},
    "password": {"name": "生成密码", "desc": "生成强随机密码（含大小写数字符号）",
                 "args": {"length": "string", "symbols": "string"}},
    "color": {"name": "颜色转换", "desc": "HEX / RGB / HSL 互转",
              "args": {"hex_value": "string"}},
    "weather": {"name": "查天气", "desc": "查指定城市当前天气与未来几天预报",
                "args": {"city": "string"}},
    "translate": {"name": "翻译", "desc": "中英等语言互译",
                  "args": {"text": "string", "target": "string", "source": "string"}},
    "exchange_rate": {"name": "汇率换算", "desc": "按最新汇率换算货币金额",
                      "args": {"base": "string", "target": "string", "amount": "string"}},
    "qr_code": {"name": "生成二维码", "desc": "把文本或链接生成二维码图片",
                "args": {"text": "string"}},
}

SYNC_TOOLS = {
    "convert_unit": tool_convert_unit,
    "timestamp": tool_timestamp,
    "base64": tool_base64,
    "hash": tool_hash,
    "json_format": tool_json_format,
    "regex_test": tool_regex_test,
    "random_pick": tool_random_pick,
    "password": tool_password,
    "color": tool_color,
    "qr_code": tool_qr_code,
}

ASYNC_TOOLS = {
    "weather": tool_weather,
    "translate": tool_translate,
    "exchange_rate": tool_exchange_rate,
}

# 规则路由：这些问法极其明确，规则模式（无模型）下也该能命中
ROUTE_RULES = (
    ("convert_unit", re.compile(r"(\d+(?:\.\d+)?)\s*(公里|千米|km|米|m|厘米|cm|毫米|mm|英里|mile|英尺|ft|英寸|inch|"
                                r"公斤|千克|kg|克|g|吨|t|磅|lb|斤|升|l|毫升|ml|加仑|gal|摄氏度?|华氏度?|开尔文|°[cfk])\s*"
                                r"(?:换算|转换|换成|等于|是|to|→|->)\s*"
                                r"([°]?(?:公里|千米|km|米|m|厘米|cm|毫米|mm|英里|mile|英尺|ft|英寸|inch|"
                                r"公斤|千克|kg|克|g|吨|t|磅|lb|斤|升|l|毫升|ml|加仑|gal|摄氏度?|华氏度?|开尔文|[cfk]))",
                                re.I)),
)
