# -*- mode: python ; coding: utf-8 -*-
# Hold-Backend 打包：onefile（单个 HoldBackend.exe）
# 构建：conda activate web 后执行  .\build.ps1
# 产物：dist\HoldBackend.exe
#
# 可放行预测（sklearn / scipy / lightgbm 等）仅在 Config.HOLD_PREDICT_ENABLED=True 时打入。
# 当前默认关闭，exe 体积会小很多；重新启用预测后改开关再打包即可。

import os
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules

SPECDIR = os.path.dirname(os.path.abspath(SPEC))
if SPECDIR not in sys.path:
    sys.path.insert(0, SPECDIR)

from app.config import Config

INCLUDE_HOLD_PREDICT_ML = bool(getattr(Config, 'HOLD_PREDICT_ENABLED', False))
print(
    'Hold-Backend spec: HOLD_PREDICT_ENABLED=%s → pack ML=%s'
    % (getattr(Config, 'HOLD_PREDICT_ENABLED', False), INCLUDE_HOLD_PREDICT_ML)
)

ML_PACKAGES = (
    'sklearn',
    'scikit-learn',
    'joblib',
    'numpy',
    'lightgbm',
    'scipy',
)
# 训练/评估脚本只在源码环境跑，冻结 exe 不需要
ML_APP_MODULES_PREFIX = (
    'app.hold_predict.train',
    'app.hold_predict.eval',
)

datas = [
    (os.path.join(SPECDIR, 'app', 'templates'), os.path.join('app', 'templates')),
    (os.path.join(SPECDIR, 'app', 'static'), os.path.join('app', 'static')),
]
if INCLUDE_HOLD_PREDICT_ML:
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
]

app_mods = collect_submodules('app')
if not INCLUDE_HOLD_PREDICT_ML:
    app_mods = [
        m for m in app_mods
        if not any(m == p or m.startswith(p + '.') for p in ML_APP_MODULES_PREFIX)
    ]
hiddenimports += app_mods

collect_pkgs = [
    'flask',
    'jinja2',
    'sqlalchemy',
    'flask_sqlalchemy',
    'oracledb',
    'lxml',
    'cryptography',
    'openpyxl',
    'waitress',
]
if INCLUDE_HOLD_PREDICT_ML:
    hiddenimports += ['joblib', 'numpy', 'sklearn', 'lightgbm']
    collect_pkgs += ['sklearn', 'joblib', 'numpy', 'lightgbm', 'scipy']

for pkg in collect_pkgs:
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hidden
    except Exception:
        pass

excludes = [
    'tkinter',
    'matplotlib',
    'IPython',
    'notebook',
    'pytest',
    'xlwings',
]
if not INCLUDE_HOLD_PREDICT_ML:
    excludes += list(ML_PACKAGES)

a = Analysis(
    [os.path.join(SPECDIR, 'app', 'main.py')],
    pathex=[SPECDIR],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
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
