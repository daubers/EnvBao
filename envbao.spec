# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: builds a one-directory application at dist/envbao/ (run dist/envbao/envbao).
# One-dir starts much faster than one-file because nothing is unpacked on every run.
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
    [],
    exclude_binaries=True,
    name="envbao",
    debug=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="envbao",
)
