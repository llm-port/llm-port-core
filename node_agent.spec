# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for llmport-agent single-file executable.

Build:
    pyinstaller node_agent.spec

Output:
    dist/llmport-agent       (Linux/macOS)
    dist/llmport-agent.exe   (Windows)
"""

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ["llm_port_node_agent/__main__.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=[
        # psutil platform backends — PyInstaller misses these on cross-collect
        "psutil._pslinux",
        "psutil._pswindows",
        "psutil._psosx",
        "psutil._psposix",
        # httpx transport
        "httpcore",
        "httpcore._async",
        "httpcore._sync",
        "h11",
        "certifi",
        "anyio",
        "anyio._backends",
        "anyio._backends._asyncio",
        "sniffio",
        # websockets
        "websockets.legacy",
        "websockets.legacy.client",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim unnecessary stdlib modules to reduce binary size
        "tkinter",
        "unittest",
        "xmlrpc",
        "pydoc",
        "doctest",
        "test",
    ],
    noarchive=False,
    optimize=1,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="llmport-agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=sys.platform != "win32",  # strip symbols on Linux/macOS
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
