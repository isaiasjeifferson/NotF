"""Gera notara/catalogo.json a partir das planilhas oficiais da NFS-e Nacional.

Uso: python tools/gerar_catalogo.py ANEXO_B.xlsx ANEXO_VIII.xlsx
Fontes (gov.br/nfse › Documentação técnica):
  - Anexo B: lista de serviços nacional (cTribNac) e NBS 2.0
  - Anexo VIII: correlação item LC 116 × NBS
"""
import json, re, sys, unicodedata
from pathlib import Path
import openpyxl

def dig(v): return re.sub(r"\D", "", str(v or ""))
def limpo(s): return re.sub(r"\s+", " ", str(s or "")).strip()

def main(b, viii, saida):
    wb = openpyxl.load_workbook(b, read_only=True, data_only=True)
    servicos = []
    for r in list(wb["LISTA.SERV.NAC."].iter_rows(values_only=True))[1:]:
        if r[0] is None or not r[4]: continue
        c = dig(r[0]).zfill(6)
        servicos.append({"c": c, "d": limpo(r[4])})
    nbs = {}
    for r in list(wb["LISTA.NBS_v2.0"].iter_rows(values_only=True))[1:]:
        c = dig(r[0])
        if len(c) == 9 and r[1]: nbs[c] = limpo(r[1])
    wb = openpyxl.load_workbook(viii, read_only=True, data_only=True)
    corr, item = {}, None
    for r in list(wb["tabela geral"].iter_rows(values_only=True))[1:]:
        if r[0]: item = dig(r[0]).zfill(4)
        n = dig(r[2])
        if item and len(n) == 9:
            lst = corr.setdefault(item, [])
            if n not in lst: lst.append(n)
            if n not in nbs and r[3]: nbs[n] = limpo(r[3])
    assert len(servicos) > 300 and len(nbs) > 500 and len(corr) > 150, (len(servicos), len(nbs), len(corr))
    dados = {"fonte": f"{Path(b).name} + {Path(viii).name}", "servicos": servicos, "nbs": nbs, "correlacao": corr}
    Path(saida).write_text(json.dumps(dados, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(len(servicos), "serviços,", len(nbs), "NBS,", len(corr), "itens com correlação")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else Path(__file__).parent.parent / "notara" / "catalogo.json")
