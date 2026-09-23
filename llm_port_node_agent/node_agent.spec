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

HERE = Path(SPECPATH)

# Read the identity from pyproject rather than restating it.
#
# A version typed in two places is a version that disagrees with itself, and
# the one embedded in a shipped binary is the copy people report bugs
# against.
try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11 running the build
    import tomli as tomllib

_PROJECT = tomllib.loads((HERE / "pyproject.toml").read_text(encoding="utf-8"))["project"]
VERSION = _PROJECT["version"]
DESCRIPTION = _PROJECT["description"]
COMPANY = _PROJECT["authors"][0]["name"]
COPYRIGHT = f"Copyright 2026 {COMPANY} and contributors. Apache License 2.0."

# The licence travels with the binary.
#
# The agent is Apache-2.0 and ships a NOTICE, and section 4(d) requires that
# notice to accompany every distribution of a derivative work. This binary is
# distributed to every node the product manages, and carried neither file --
# so what we handed out was not a compliant distribution. `--license` prints
# them back out, because a file nobody can read is a poor kind of notice.
_LICENCE_FILES = [
    (str(HERE / name), ".")
    for name in ("LICENSE", "NOTICE")
    if (HERE / name).is_file()
]

a = Analysis(
    ["llm_port_node_agent/__main__.py"],
    pathex=["."],
    binaries=[],
    datas=_LICENCE_FILES,
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

# Windows carries this in the PE resource; ELF and Mach-O have no equivalent
# slot, which is why the licence files above are bundled on every platform
# rather than only here.
version_info = None
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable,
        VarFileInfo, VarStruct, VSVersionInfo,
    )

    _parts = tuple(int(part) for part in VERSION.split(".")[:3]) + (0,)
    version_info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=_parts, prodvers=_parts),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("CompanyName", COMPANY),
                StringStruct("FileDescription", DESCRIPTION),
                StringStruct("FileVersion", VERSION),
                StringStruct("InternalName", "llmport-agent"),
                StringStruct("LegalCopyright", COPYRIGHT),
                StringStruct("OriginalFilename", "llmport-agent.exe"),
                StringStruct("ProductName", "LLM.Port Node Agent"),
                StringStruct("ProductVersion", VERSION),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )

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
    version=version_info,
)
