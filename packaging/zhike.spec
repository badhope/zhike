# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：把知客打成单个 Windows exe。

## 打包策略

- **单文件（onefile）**：用户拿到一个 zhike.exe 就能跑，不需要装 Python。
  代价是启动时要解包到临时目录（首次约 3-5 秒），但换来的分发体验最重要——
  这是「个人自托管工具」，不是常驻服务器进程，启动慢一点可以接受。
- **控制台程序**（console=True）：CLI 是主入口（`zhike init` / `zhike run`），
  必须能打印输出。用 GUI 子系统会让命令行窗口里看不到任何提示。
- **前端产物一起打进去**：`CS_AGENT_STATIC_DIR` 默认指向包内 `frontend/dist`，
  用户双击 exe 就有完整的 Web 控制台，不用自己 npm build。

## 数据文件

只读资源（知识库种子、角色卡、前端产物）进包；可写数据（db、日志、.env）
由 core/paths.py 在运行时决定位置，不打包。

## 排除项

打包体积的大头是可选的 AI 依赖（torch/sentence-transformers/faster-whisper）。
它们不是运行必需（没装就是规则模式 + 关键词检索），排除掉能把体积从
数百 MB 压到可接受的量级。需要向量检索的用户用 pip 安装完整版。

用法：
    pyinstaller packaging/zhike.spec --noconfirm
产物：dist/zhike.exe
"""
from pathlib import Path

# spec 文件里 __file__ 不可用，用 SPECPATH（PyInstaller 注入）
ROOT = Path(SPECPATH).resolve().parent  # noqa: F821

datas = [
    (str(ROOT / "core" / "data" / "kb.json"), "core/data"),
    (str(ROOT / "core" / "data" / "characters"), "core/data/characters"),
    (str(ROOT / "frontend" / "dist"), "frontend/dist"),
    (str(ROOT / ".env.example"), "."),
    (str(ROOT / "VERSION"), "."),
]

# 可选重型依赖：没装也能跑（规则模式），装了才有向量检索/语音/本地推理。
# 放进 excludes 能显著减小体积；用户要完整能力时用 pip 安装。
excludes = [
    "torch", "torchvision", "torchaudio",
    "sentence_transformers", "transformers",
    "faster_whisper", "ctranslate2", "onnxruntime",
    "matplotlib", "scipy", "pandas", "IPython", "notebook",
    "tkinter", "PyQt5", "PySide6",
    "pytest", "mypy", "ruff", "pip", "setuptools", "wheel",
]

hiddenimports = [
    # uvicorn 的 worker/loop 实现是运行时按字符串导入的，静态分析看不到
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    # FastAPI/Starlette 同理
    "starlette.middleware.gzip",
    "starlette.middleware.cors",
    # 项目内按需导入的模块
    "core.api.kb",
    "core.api.qq_deploy",
    "core.api.qq_login",
    "core.api.reminders",
    "core.actions.registry",
    "core.actions.service",
    "core.mcp_server",
    "core.napcat_fetch",
    "core.qq_deploy",
    "core.tracing",
    "gateway.bot",
]

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="zhike",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX 压缩会被部分杀软误报，且与现代 CPU 的解压开销不划算
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,       # CLI 主入口，必须保留控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "packaging" / "zhike.ico") if (ROOT / "packaging" / "zhike.ico").exists() else None,
)
