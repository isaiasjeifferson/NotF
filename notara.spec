# PyInstaller: gera a pasta do programa (o instalador empacota depois)
from PyInstaller.utils.hooks import collect_submodules

ocultos = ["webview.platforms.edgechromium", "clr_loader"]
ocultos += collect_submodules("reportlab.graphics.barcode")  # carregados dinamicamente pelo reportlab
a = Analysis(["run.py"], datas=[("notara/ui", "notara/ui"), ("notara/schemas", "notara/schemas"), ("notara/catalogo.json", "notara"), ("notara/logo_nfse.png", "notara"),
                                ("tests/nfse_exemplo.xml", "notara")],
             hiddenimports=ocultos, excludes=["tkinter", "pytest"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Notara", icon="notara/icone.ico",
          console=False, version="installer/versao.txt")
coll = COLLECT(exe, a.binaries, a.datas, name="Notara")
