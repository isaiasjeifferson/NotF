# PyInstaller: gera a pasta do programa (o instalador empacota depois)
from pathlib import Path
a = Analysis(["run.py"], datas=[("notara/ui", "notara/ui"), ("notara/schemas", "notara/schemas")],
             hiddenimports=["webview.platforms.edgechromium", "clr_loader"], excludes=["tkinter", "pytest"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Notara", icon="notara/icone.ico",
          console=False, version="installer/versao.txt")
coll = COLLECT(exe, a.binaries, a.datas, name="Notara")
