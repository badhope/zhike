"""PyInstaller 入口：把 CLI 包成 exe 时的最外层。

## 为什么不能直接把 core/cli.py 当入口

`core/cli.py` 里的 `ROOT` 由 `core/paths.py` 决定，而 `paths.py` 判断自己
是否在打包环境里，靠的是 `sys.frozen`——这个标志由 PyInstaller 的引导程序设置，
它只在真正被 exe 启动时存在。如果直接用 `core/cli.py` 当入口，PyInstaller 会把
它当成普通脚本执行，模块级 import 时的相对定位会以 `_MEIPASS` 为基准，
但 CLI 里又有一堆 `from core import ...` 的绝对导入——只有把仓库根放进
sys.path 才成立。这个入口文件负责这一件事，让打包行为可预测。

另外，双击 exe（不带参数）时给出引导界面而不是一堆 argparse 报错：
这是绝大多数用户第一次接触这个程序的方式。
"""
import os
import sys
from pathlib import Path

# 打包后：模块在 _MEIPASS 里；源码运行：在仓库根。
if getattr(sys, "frozen", False):
    BASE = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    BASE = Path(__file__).resolve().parent.parent

if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

# 单文件 exe 的默认前端目录：包内的 frontend/dist
if getattr(sys, "frozen", False) and not os.environ.get("CS_AGENT_STATIC_DIR"):
    dist = BASE / "frontend" / "dist"
    if dist.is_dir():
        os.environ["CS_AGENT_STATIC_DIR"] = str(dist)

from core.cli import main  # noqa: E402

if __name__ == "__main__":
    # 双击运行（无参数）时，先尝试直接启动服务；已初始化就起，未初始化就引导 init。
    # 命令行里显式带参数则完全交给 CLI 解析。
    if len(sys.argv) == 1 and getattr(sys, "frozen", False):
        from core import paths

        if not (paths.writable_dir() / ".env").exists():
            print("知客 Zhike · 首次运行，正在初始化…\n")
            code = main(["init"])
            if code != 0:
                input("\n初始化未完成，按回车退出…")
                raise SystemExit(code)
            print("\n初始化完成。现在启动服务，浏览器打开 http://127.0.0.1:8010")
            print("（关闭这个窗口即停止服务）\n")
            input("按回车启动…")
        raise SystemExit(main(["run"]))
    raise SystemExit(main())
