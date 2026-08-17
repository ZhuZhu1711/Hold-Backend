# -*- mode: python ; coding: utf-8 -*-
# Hold-Backend 打包：onefile（单个 HoldBackend.exe）
# 构建：conda activate web 后执行  .\build.ps1
# 产物：dist\HoldBackend.exe

import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

SPECDIR = os.path.dirname(os.path.abspath(SPEC))

datas = [
    (os.path.join(SPECDIR, 'app', 'templates'), os.path.join('app', 'templates')),
    (os.path.join(SPECDIR, 'app', 'static'), os.path.join('app', 'static')),
]
_artifacts = os.path.join(SPECDIR, 'app', 'hold_predict', 'artifacts')
if os.path.isdir(_artifacts):
    datas.append((_artifacts, os.path.join('app', 'hold_predict', 'artifacts')))

binaries = []
hiddenimports = [
    'waitress',
    'flask',
    'flask_sqlalchemy',
    'jinja2',
    'sqlalchemy',
    'sqlalchemy.dialects.oracle',
    'sqlalchemy.dialects.oracle.oracledb',
    'oracledb',
    'cryptography',
    'lxml',
    'lxml.etree',
    'openpyxl',
    'schedule',
    'joblib',
    'numpy',
    'sklearn',
    'lightgbm',
]
hiddenimports += collect_submodules('app')

for pkg in (
    'flask',
    'jinja2',
    'sqlalchemy',
    'flask_sqlalchemy',
    'oracledb',
    'lxml',
    'cryptography',
    'openpyxl',
    'waitress',
    'sklearn',
    'joblib',
    'numpy',
    'lightgbm',
    'scipy',
):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hidden
    except Exception:
        pass

a = Analysis(
    [os.path.join(SPECDIR, 'app', 'main.py')],
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
        'xlwings',
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
    name='HoldBackend',
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
