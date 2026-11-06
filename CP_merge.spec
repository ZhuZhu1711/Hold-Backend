# -*- mode: python ; coding: utf-8 -*-
# CP 合批调度独立打包：onefile（单个 CP_HoldMerge.exe）
# 构建：conda activate web 后执行  .\build.ps1
# 产物：dist\CP_HoldMerge.exe
#
# 不挂 Web，也不打进 FT_HoldMerge.exe。启动后常驻。
# 不设置 HOLD_ENV 为测试；正式机必须设 HOLD_ENV=release。

import os
import sys
from PyInstaller.utils.hooks import collect_all

SPECDIR = os.path.dirname(os.path.abspath(SPEC))
if SPECDIR not in sys.path:
    sys.path.insert(0, SPECDIR)

datas = []
binaries = []
hiddenimports = [
    'oracledb',
    'schedule',
    'cryptography',
    'app.backend_schedule.singleton_lock',
    'app.backend_schedule.CP_HOLD_MERGE_sche',
]

# oracledb thin 在 .pyd 里才 import kdf，静态分析扫不到，必须整包收进来。
for pkg in ('oracledb', 'cryptography'):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hidden
    except Exception:
        pass

a = Analysis(
    [os.path.join(SPECDIR, 'app', 'backend_schedule', 'CP_HOLD_MERGE_sche.py')],
    pathex=[SPECDIR],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'IPython',
        'notebook',
        'pytest',
        'waitress',
        'openpyxl',
        'lxml',
        'app.backend_schedule.FT_HOLD_MERGE_sche',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='CP_HoldMerge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
