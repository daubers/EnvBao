# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: builds a single self-contained executable at dist/envbao.
# Usage: uv run pyinstaller envbao.spec --clean --noconfirm
a = Analysis(
    ["src/envbao/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=[],
    hiddenimports=[],
    excludes=["tkinter", "unittest", "pydoc", "doctest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="envbao",
    debug=False,
    strip=False,
    upx=False,
    console=True,
    runtime_tmpdir=None,
)
