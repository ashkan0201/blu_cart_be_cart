# -*- mode: python ; coding: utf-8 -*-
# ساخت:  pyinstaller BluTransfer.spec   (از ریشهٔ پوشهٔ ساخت، کنار app.py)
a = Analysis(
    ['app.py'],
    pathex=['code'],
    binaries=[],
    datas=[('templates', 'templates'), ('static', 'static')],
    hiddenimports=['db', 'blu_cart_be_cart'],
    hookspath=[],
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
    name='BluTransfer',
    debug=False,
    strip=False,
    upx=False,
    console=True,   # پنجرهٔ لاگ؛ بستنش برنامه را متوقف می‌کند
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='BluTransfer')
