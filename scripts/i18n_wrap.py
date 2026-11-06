"""把 JSX / TSX 里未国际化的中文文案批量包进 t()。

## 为什么写脚本而不是手改

536 处缺口分布在 15 个文件里，手改必然漏、且改错一处（比如把对象 key 也包了）
会直接让界面崩。脚本做三件事：

1. **只改"用户可见的中文"**：JSX 文本节点、常见文案属性（title/placeholder/
   aria-label/alt）、以及对象字面量里 `label/desc/title/text/sub` 这类字段的值。
2. **跳过代码语义位置**：比较运算符、正则、import、对象 key、类型注解、
   已经是 t() 的调用，一律不动。
3. **幂等**：已经包了 t() 的再跑一遍不会变成 t(t('x'))。

## 为什么必须配测试

这类机械改写最容易出两类错：
- 把 `'zh-CN'` 这种 locale 标识当成文案包进去（运行时仍是原字符串，无害但不该包）；
- 把对象 key `{ label: '全部' }` 的 key 也包了（会变成语法错误）。

所以脚本跑完必须 `npm run build` + `npm test`，并且人工抽检 diff。

用法：
    python scripts/i18n_wrap.py --check     # 只报告，不改
    python scripts/i18n_wrap.py --write     # 实际改写
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "frontend" / "src"

# 这些属性值通常是给用户看的
TEXT_ATTRS = ("title", "placeholder", "aria-label", "alt", "label", "desc", "sub")

# 对象字面量里这些键的值是文案
TEXT_KEYS = ("label", "desc", "title", "text", "sub", "hint", "note", "confirmText", "cancelText")

CJK = re.compile(r"[\u4e00-\u9fff]")

# 不该被包装的：语言代码、纯符号、已经是 t()、模板块
SKIP_VALUES = {
    "zh-CN", "en", "zh", "中", "EN", "UTF-8", "utf-8",
}


def _already_wrapped(s: str) -> bool:
    return bool(re.search(r"\bt\(\s*$", s)) or s.lstrip().startswith("t(")


def wrap_jsx_text(line: str) -> tuple[str, int]:
    """把 `>中文<` 或 `中文</tag>` 这类文本节点包进 {t('...')}。

    只处理单行内、不含 { } < > 的纯文本片段，避免碰到表达式。
    """
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        prefix, text, suffix = m.group(1), m.group(2), m.group(3)
        raw = text.strip()
        if not raw or not CJK.search(raw) or _already_wrapped(prefix + raw):
            return m.group(0)
        if raw in SKIP_VALUES:
            return m.group(0)
        count += 1
        lead = text[: len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()):]
        escaped = raw.replace("\\", "\\\\").replace("'", "\\'")
        return f"{prefix}{lead}{{t('{escaped}')}}{trail}{suffix}"

    # >文本<   （文本里不含花括号与尖括号）
    line = re.sub(r"(>)([^<>{}\n]*[\u4e00-\u9fff][^<>{}\n]*)(<)", repl, line)
    return line, count


def wrap_attr(line: str) -> tuple[str, int]:
    """把 title="中文" / placeholder={'中文'} 之类的属性值包进 t()。"""
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        attr, value = m.group(1), m.group(2)
        if not CJK.search(value) or value.strip() in SKIP_VALUES:
            return m.group(0)
        if _already_wrapped(value):
            return m.group(0)
        count += 1
        escaped = value.replace("\\", "\\\\").replace("'", "\\'")
        return f'{attr}={{t(\'{escaped}\')}}'

    # 只处理双引号字面量形式的属性，单引号形式在 JSX 里少见且易与 t('') 冲突
    line = re.sub(
        r"\b(" + "|".join(re.escape(a) for a in TEXT_ATTRS) + r')="([^"]*)"',
        repl, line)
    return line, count


def wrap_object_value(line: str) -> tuple[str, int]:
    """把 `label: '中文'` 这类对象字段值包进 t()。"""
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        key, value = m.group(1), m.group(3)
        if not CJK.search(value) or value.strip() in SKIP_VALUES:
            return m.group(0)
        count += 1
        escaped = value.replace("\\", "\\\\").replace("'", "\\'")
        return f"{key}: t('{escaped}')"

    line = re.sub(
        r"\b(" + "|".join(TEXT_KEYS) + r")\s*:\s*(['\"])([^'\"]*[\u4e00-\u9fff][^'\"]*)\2",
        repl, line)
    return line, count


def process(path: Path, write: bool) -> int:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    total = 0
    out = []
    for line in lines:
        stripped = line.strip()
        # 跳过注释与 import
        if stripped.startswith(("//", "*", "/*", "import ", "from ")):
            out.append(line)
            continue
        new, c1 = wrap_jsx_text(line)
        new, c2 = wrap_attr(new)
        new, c3 = wrap_object_value(new)
        total += c1 + c2 + c3
        out.append(new)
    if write and total:
        path.write_text("".join(out), encoding="utf-8")
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description="批量把中文文案包进 t()")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="只报告，不改文件")
    g.add_argument("--write", action="store_true", help="实际改写")
    a = ap.parse_args()

    files = sorted(list(SRC.rglob("*.tsx")) + list(SRC.rglob("*.jsx")))
    grand = 0
    for f in files:
        n = process(f, write=a.write)
        if n:
            print(f"{'WRITE' if a.write else 'CHECK'} {n:3}  {f.relative_to(ROOT)}")
            grand += n
    print(f"\n合计 {grand} 处{'已改写' if a.write else '待改写'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
