"""抽取前端源码里的 t('中文') msgid，找出 en.ts 缺失的翻译。

为什么用逐字符扫描而不是正则：msgid 里可能含括号、斜杠、引号、反引号，
正则很容易断在奇怪的地方（已经踩过两次）。这里做一个很小的状态机：
遇到 `t('` 就吃到配对引号为止，跳过转义。
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
CJK = re.compile("[\u4e00-\u9fff]")


def extract_ids(src: str) -> set[str]:
    ids: set[str] = set()
    i = 0
    n = len(src)
    while i < n - 2:
        # 找 t( 后紧跟引号的位置
        if src[i] == "t" and src[i + 1] == "(":
            j = i + 2
            while j < n and src[j] in " \t":
                j += 1
            if j < n and src[j] in "'\"":
                q = src[j]
                k = j + 1
                buf = []
                while k < n:
                    ch = src[k]
                    if ch == "\\" and k + 1 < n:
                        buf.append(src[k:k + 2])
                        k += 2
                        continue
                    if ch == q:
                        break
                    buf.append(ch)
                    k += 1
                if k < n:
                    val = "".join(buf)
                    if CJK.search(val) and "\n" not in val:
                        ids.add(val)
                    i = k
        i += 1
    return ids


def main() -> int:
    ids: set[str] = set()
    for f in list((ROOT / "frontend/src").rglob("*.tsx")) + list((ROOT / "frontend/src").rglob("*.jsx")):
        ids |= extract_ids(f.read_text(encoding="utf-8"))

    en_text = (ROOT / "frontend/src/locales/en.ts").read_text(encoding="utf-8")
    have = set(re.findall(r"^\s*'([^'\n]+)':", en_text, re.M))
    # en.ts 里也有用双引号写的键
    have |= set(re.findall(r'^\s*"([^"\n]+)":', en_text, re.M))

    missing = sorted(ids - have)
    print(f"源码 msgid: {len(ids)} | en.ts 键: {len(have)} | 缺失: {len(missing)}")
    for m in missing:
        print("MISS\t" + m.replace("\t", " "))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
