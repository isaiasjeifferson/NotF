"""Lê a lista de notas de um PDF ou de texto colado."""
import re

CNPJ_RE = re.compile(r"\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}|\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
VALOR_RE = re.compile(r"R\$\s*([\d.]+,\d{2})|(?<![\d/.-])(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})(?![\d])")


def texto_pdf(caminho):
    from pypdf import PdfReader
    return "\n".join(p.extract_text() or "" for p in PdfReader(caminho).pages)


def _valor(s):
    m = VALOR_RE.search(s)
    if not m:
        return None
    return float((m.group(1) or m.group(2)).replace(".", "").replace(",", "."))


def ler(texto):
    """Devolve (notas, ignoradas). Cada nota: {unidade, cnpj, descricao, valor}."""
    linhas = [l.strip() for l in texto.replace("\r", "").split("\n")]
    ignoradas = [l.split(" - ")[0].strip() for l in linhas if re.search(r"n[ãa]o emitir", l, re.I)]
    linhas = [l for l in linhas if l and not re.search(r"n[ãa]o emitir", l, re.I)]

    if any(re.match(r"(?i)^cnpj\s*:?", l) for l in linhas):
        return _blocos(linhas), ignoradas
    return _simples(linhas), ignoradas


def _blocos(linhas):
    notas, atual, campo = [], None, None
    for i, l in enumerate(linhas):
        low = l.lower()
        if low.startswith("cnpj"):
            unidade = linhas[i - 1] if i else ""
            atual = {"unidade": unidade, "cnpj": "", "descricao": "", "valor": None}
            notas.append(atual)
            resto = l.split(":", 1)[1] if ":" in l else ""
            m = CNPJ_RE.search(resto)
            atual["cnpj"] = m.group(0) if m else ""
            campo = "cnpj" if not m else None
        elif atual is None:
            continue
        elif low.startswith("descri"):
            atual["descricao"] = l.split(":", 1)[1].strip() if ":" in l else ""
            campo = "descricao"
        elif low.startswith("valor"):
            v = _valor(l)
            atual["valor"] = v
            campo = "valor" if v is None else None
        elif campo == "cnpj":
            m = CNPJ_RE.search(l)
            if m:
                atual["cnpj"], campo = m.group(0), None
        elif campo == "valor":
            atual["valor"], campo = _valor(l), None
        elif campo == "descricao":
            nxt = linhas[i + 1].lower() if i + 1 < len(linhas) else ""
            if nxt.startswith("cnpj"):  # é o título do próximo bloco
                campo = None
            else:
                atual["descricao"] = (atual["descricao"] + " " + l).strip()
    return [n for n in notas if n["cnpj"] and n["valor"]]


def _simples(linhas):
    """Uma nota por linha: 'unidade  CNPJ  valor' em qualquer ordem."""
    notas = []
    for l in linhas:
        m = CNPJ_RE.search(l)
        v = _valor(CNPJ_RE.sub(" ", l))
        if not (m and v):
            continue
        unidade = VALOR_RE.sub(" ", CNPJ_RE.sub(" ", l)).replace("R$", " ")
        unidade = re.sub(r"\s*[;|,\t-]+\s*$|^\s*[;|,\t-]+\s*", "", re.sub(r"\s{2,}", " ", unidade)).strip(" ;|,-\t")
        notas.append({"unidade": unidade, "cnpj": m.group(0), "descricao": "", "valor": v})
    return notas
