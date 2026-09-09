# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

customtkinter_datas = collect_data_files("customtkinter")
customtkinter_hiddenimports = collect_submodules("customtkinter")
pystray_hiddenimports = collect_submodules("pystray")


a = Analysis(
    ["main_PC_Helper_V4_1_TRAY_UPDATES.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("pc_background.png", "."),
        *customtkinter_datas,
    ],
    hiddenimports=customtkinter_hiddenimports + pystray_hiddenimports,
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
    a.binaries,
    a.datas,
    [],
    name="PC Helper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
