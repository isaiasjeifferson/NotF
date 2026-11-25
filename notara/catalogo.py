"""Tabela oficial de serviços (código de tributação nacional) e NBS, com a correlação entre eles.

Gerada por tools/gerar_catalogo.py a partir dos Anexos B e VIII da documentação da NFS-e Nacional.
"""
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _dados():
    d = json.loads((Path(__file__).parent / "catalogo.json").read_text(encoding="utf-8"))
    d["_srv"] = [(s["c"], s["d"], _norm(s["d"])) for s in d["servicos"]]
    d["_nbs"] = [(c, t, _norm(t)) for c, t in d["nbs"].items()]
    return d


def _norm(s):
    s = unicodedata.normalize("NFD", str(s or "").lower())
    return re.sub(r"[^a-z0-9 ]", " ", "".join(ch for ch in s if not unicodedata.combining(ch)))


def fonte():
    return _dados()["fonte"]


def _busca(itens, q, limite):
    q = str(q or "").strip()
    num = re.sub(r"\D", "", q)
    palavras = [p for p in _norm(q).split() if p and not p.isdigit()]
    achados = []
    for cod, texto, norm in itens:
        if palavras:
            if not all(p in norm for p in palavras):
                continue
            nota = sum(1 for p in palavras if re.search(rf"\b{p}", norm)) + (2 if norm.startswith(palavras[0]) else 0)
        elif num:
            if not cod.startswith(num):
                continue
            nota = 0
        else:
            nota = 0
        achados.append((-nota, cod, texto))
    achados.sort()
    return [{"codigo": c, "descricao": t} for _, c, t in achados[:limite]]


def servicos(q="", limite=40):
    return _busca(_dados()["_srv"], q, limite)


def servico(cod):
    cod = re.sub(r"\D", "", str(cod or "")).zfill(6)
    return next(({"codigo": c, "descricao": t} for c, t, _ in _dados()["_srv"] if c == cod), None)


def nbs_sugeridos(cod_servico):
    """NBS que a tabela oficial liga ao item da lista (os 4 primeiros dígitos do código do serviço)."""
    d = _dados()
    item = re.sub(r"\D", "", str(cod_servico or "")).zfill(6)[:4]
    return [{"codigo": n, "descricao": d["nbs"].get(n, "")} for n in d["correlacao"].get(item, [])]


def nbs(q="", cod_servico="", limite=40):
    sug = nbs_sugeridos(cod_servico) if cod_servico else []
    if not str(q or "").strip():
        return [{**s, "sugerido": True} for s in sug]
    ligados = {s["codigo"] for s in sug}
    achados = _busca(_dados()["_nbs"], q, 400)
    achados.sort(key=lambda a: a["codigo"] not in ligados)
    return [{**a, "sugerido": a["codigo"] in ligados} for a in achados[:limite]]


def nbs_descricao(cod):
    return _dados()["nbs"].get(re.sub(r"\D", "", str(cod or "")), "")
