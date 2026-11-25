"""Abre a janela do Notara."""
import sys
from pathlib import Path

import webview

from notara.app import Api

BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
UI = BASE / "notara" / "ui" / "index.html"


def _fundo():
    """Cor de fundo da janela antes de a interface carregar (evita clarão no tema escuro)."""
    from notara import dados
    tema = dados.ajustes().get("tema", "auto")
    if tema == "auto" and sys.platform == "win32":
        try:
            import winreg
            chave = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            tema = "claro" if winreg.QueryValueEx(chave, "AppsUseLightTheme")[0] else "escuro"
        except OSError:
            tema = "claro"
    return "#000000" if tema == "escuro" else "#F5F5F7"


def verificar():
    """Autoteste do programa empacotado (usado na publicação): tudo o que o Notara carrega precisa existir."""
    from notara import catalogo, danfse, importar, nfse  # noqa: F401
    assert catalogo.servicos("lavanderia"), "tabela de serviços"
    exemplo = BASE / "notara" / "nfse_exemplo.xml"
    assert danfse.gerar(exemplo.read_bytes())[:4] == b"%PDF"
    nfse.validar.__call__  # schemas carregados sob demanda
    from lxml import etree
    etree.XMLSchema(etree.parse(str(nfse.SCHEMAS / "DPS_v1.01.xsd")))
    print("Notara OK")


def main():
    if "--verificar" in sys.argv:
        try:
            verificar()
        except Exception as e:  # sem janela de erro: o código de saída informa a falha
            print(repr(e), file=sys.stderr)
            sys.exit(1)
        return
    api = Api()
    api._janela = webview.create_window("Notara", str(UI), js_api=api, width=1100, height=760,
                                       min_size=(860, 580), background_color=_fundo())
    webview.start(private_mode=True)  # sem cache/cookies em disco


if __name__ == "__main__":
    main()
