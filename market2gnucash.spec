# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules


project_root = Path(SPECPATH)

hiddenimports = []
binaries = []

try:
    hiddenimports.append("gnucash")
    hiddenimports.extend(collect_submodules("gnucash"))
    binaries.extend(collect_dynamic_libs("gnucash"))
except Exception:
    # The build helper validates gnucash ahead of time. Keep the spec import-safe.
    pass


a = Analysis(
    ["market2gnucash/app.py"],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="market2gnucash",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="market2gnucash",
)
