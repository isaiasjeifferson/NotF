"""Abre a janela do Notara."""
import sys
from pathlib import Path

import webview

from notara.app import Api

BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
UI = BASE / "notara" / "ui" / "index.html"


def main():
    api = Api()
    api._janela = webview.create_window("Notara", str(UI), js_api=api, width=1100, height=760,
                                       min_size=(860, 580), background_color="#F5F5F7")
    webview.start(private_mode=True)  # sem cache/cookies em disco


if __name__ == "__main__":
    main()
