"""Gera o DANFSe (PDF da nota) a partir do XML da NFS-e autorizada pela Receita.

O XML é o documento fiscal; o DANFSe é só a representação impressa dele. Gerar o PDF localmente
é obrigação do sistema emissor desde que a Receita desligou a API de PDF (NT 008/2026). O layout reproduz
o DANFSe v2.0 que o portal do Emissor Nacional gera, bloco a bloco.
"""
from datetime import datetime
from io import BytesIO

from pathlib import Path

from lxml import etree
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import HexColor, black
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

NS = {"n": "http://www.sped.fazenda.gov.br/nfse"}
CONSULTA = "https://www.nfse.gov.br/ConsultaPublica/?tpc=1&chave={chave}"
CINZA = HexColor("#F2F2F2")
LINHA = black
LOGO = Path(__file__).parent / "logo_nfse.png"

REG_SN = {"1": "Regime de apuração dos tributos federais e municipal pelo Simples Nacional",
          "2": "Regime de apuração dos tributos federais pelo SN e ISSQN pela NFS-e",
          "3": "Regime de apuração dos tributos federais e municipal pela NFS-e"}
OP_SN = {"1": "Não Optante", "2": "Optante - Microempreendedor Individual (MEI)",
         "3": "Optante - Microempresa ou Empresa de Pequeno Porte (ME/EPP)"}
TRIB_ISS = {"1": "Operação Tributável", "2": "Imunidade", "3": "Exportação de Serviço", "4": "Não Incidência"}
RET_ISS = {"1": "Não Retido", "2": "Retido pelo Tomador", "3": "Retido pelo Intermediário"}


def _t(el, caminho, padrao="-"):
    if el is None:
        return padrao
    r = el.xpath(caminho, namespaces=NS)
    if not r:
        return padrao
    v = r[0] if isinstance(r[0], str) else (r[0].text or "")
    return v.strip() or padrao


def _doc(d):
    d = "".join(c for c in d if c.isdigit())
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    return d or "-"


def _brl(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "-"
    return "R$ " + f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _data(v, hora=True):
    try:
        d = datetime.fromisoformat(v)
        return d.strftime("%d/%m/%Y %H:%M:%S" if hora else "%d/%m/%Y")
    except (TypeError, ValueError):
        return v or "-"


def _endereco(el):
    if el is None:
        return "-"
    partes = [_t(el, "n:xLgr", ""), _t(el, "n:nro", ""), _t(el, "n:xCpl", ""), _t(el, "n:xBairro", "")]
    return ", ".join(p for p in partes if p) or "-"



H = A4[1]
COLS = (11.9, 156.5, 301.0, 445.6)   # x das 4 colunas no DANFSe do portal
LARG = 144.6
K = 0.80                              # topo do texto -> linha de base (calibrado contra o PDF do portal)


class _Pag:
    """Desenha em coordenadas 'de cima para baixo', iguais às medidas do PDF oficial."""

    def __init__(self, c):
        self.c = c

    def txt(self, x, top, s, tam=7, negrito=False, larg=None, centro=False):
        fonte = "Helvetica-Bold" if negrito else "Helvetica"
        s = str(s)
        if larg and stringWidth(s, fonte, tam) > larg:
            while s and stringWidth(s + " ...", fonte, tam) > larg:
                s = s[:-1]
            s = s.rstrip() + " ..."
        self.c.setFont(fonte, tam)
        y = H - top - K * tam
        (self.c.drawCentredString if centro else self.c.drawString)(x, y, s)

    def linha(self, x0, top, x1, top1=None):
        self.c.setStrokeColor(LINHA)
        self.c.setLineWidth(0.5)
        self.c.line(x0, H - top, x1, H - (top if top1 is None else top1))

    def cinza(self, x, top, w, h):
        self.c.setFillColor(CINZA)
        self.c.rect(x, H - top - h, w, h, stroke=0, fill=1)
        self.c.setFillColor(black)

    def celula(self, col, top, rot, val, vaos=1, cinza=False):
        if cinza:
            self.cinza(COLS[col] - 3.4, top - 0.9, LARG, 19.1)
        larg = LARG * vaos - 6
        self.txt(COLS[col], top, rot, 6, True, larg)
        self.txt(COLS[col], top + 6.9, val, 7, False, larg)

    def secao(self, top, titulo, linhas):
        """linhas: listas de 4 células (rótulo, valor) | None (continuação da anterior). Título ocupa a 1ª célula."""
        self.linha(8.5, top, 586.8)
        self.cinza(8.5, top + 0.2, LARG, 19.1)
        tam = 7
        while stringWidth(titulo, "Helvetica-Bold", tam) > LARG - 6:
            tam -= 0.25
        self.txt(COLS[0], top + 1.3, titulo, tam, True)
        y = top + 1.1
        for i, l in enumerate(linhas):
            for j, cel in enumerate(l):
                if cel is None or (i == 0 and j == 0):
                    continue
                vaos = 1
                while j + vaos < 4 and l[j + vaos] is None:
                    vaos += 1
                self.celula(j, y, cel[0], cel[1], vaos, cinza=len(cel) > 2)
            y += 19.1
        return top + 19.1 * len(linhas)

    def paragrafo(self, top, texto, tam=7, passo=8.0, larg=575):
        linhas, atual = [], ""
        for palavra in str(texto).split():
            t = (atual + " " + palavra).strip()
            if stringWidth(t, "Helvetica", tam) <= larg:
                atual = t
            else:
                linhas.append(atual)
                atual = palavra
        linhas.append(atual)
        linhas = [l for l in linhas if l] or ["-"]
        for k, l in enumerate(linhas):
            self.txt(COLS[0], top + k * passo, l, tam)
        return top + passo * len(linhas)


def _ibge(c):
    c = "".join(ch for ch in str(c) if ch.isdigit())
    return f"{c[:2]}.{c[2:]}" if len(c) == 7 else (c or "-")


def _cep(c):
    c = "".join(ch for ch in str(c) if ch.isdigit())
    return str(int(c)) if c else "-"


def gerar(xml_nfse: bytes) -> bytes:
    raiz = etree.fromstring(xml_nfse)
    inf = raiz.find("n:infNFSe", NS) if raiz.tag.endswith("NFSe") else raiz
    if inf is None:
        raise ValueError("XML não é de uma NFS-e")
    dps = inf.find("n:DPS/n:infDPS", NS)
    emit, toma = inf.find("n:emit", NS), dps.find("n:toma", NS)
    prest, serv = dps.find("n:prest", NS), dps.find("n:serv", NS)
    vdps, vnf = dps.find("n:valores", NS), inf.find("n:valores", NS)
    chave = inf.get("Id", "")[3:]
    end_emit = emit.find("n:enderNac", NS) if emit is not None else None
    uf_emi = _t(end_emit, "n:UF", "")

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"NFS-e {_t(inf, 'n:nNFSe')}")
    g = _Pag(c)

    # Moldura e cabeçalho
    c.setStrokeColor(LINHA)
    c.setLineWidth(0.5)
    c.rect(5, H - 5 - 832, 585, 832, stroke=1, fill=0)
    g.cinza(8.5, 5.7, 144.6, 34)
    g.cinza(153.1, 5.7, 289.1, 34)
    g.cinza(442.2, 5.7, 144.6, 34)
    if LOGO.exists():
        c.drawImage(ImageReader(str(LOGO)), 11.9, H - 10.3 - 22.9, width=115.7, height=22.9, mask="auto")
    g.txt(297.6, 13.7, "DANFSe v2.0", 9, True, centro=True)
    g.txt(297.6, 24.0, "Documento Auxiliar da NFS-e", 9, True, centro=True)
    g.txt(445.6, 12.4, f"Município: {_t(inf, 'n:xLocEmi')}" + (f" - {uf_emi}" if uf_emi else ""), 8)
    g.txt(445.6, 21.2, f"Ambiente Gerador: {_t(inf, 'n:ambGer')}", 6)
    g.txt(445.6, 28.0, f"Tipo de Ambiente: {_t(dps, 'n:tpAmb')}", 6)
    g.linha(8.5, 39.9, 586.8)

    # Identificação
    qr = QrCodeWidget(CONSULTA.format(chave=chave))
    qr.barBorder = 0
    b = qr.getBounds()
    d = Drawing(45, 45, transform=[45 / (b[2] - b[0]), 0, 0, 45 / (b[3] - b[1]), 0, 0])
    d.add(qr)
    renderPDF.draw(d, c, 492.0, H - 44.8 - 45)
    for k, l in enumerate(["A autenticidade desta NFS-e pode ser verificada", "pela leitura deste código QR ou pela consulta da",
                           "chave de acesso no portal nacional da NFS-e"]):
        g.txt(445.6, 92.7 + 6.8 * k, l, 6)
    g.txt(COLS[0], 45.5, "CHAVE DE ACESSO DA NFS-e", 7, True)
    g.txt(COLS[0], 53.4, chave, 7)
    g.cinza(8.5, 105.1, 144.6, 20.2)
    for top, linha in ((65.7, [("NÚMERO DA NFS-e", _t(inf, "n:nNFSe")), ("COMPETÊNCIA DA NFS-e", _data(_t(dps, "n:dCompet"), False)),
                               ("DATA E HORA DA EMISSÃO DA NFS-e", _data(_t(inf, "n:dhProc")))]),
                       (85.9, [("NÚMERO DA DPS", _t(dps, "n:nDPS")), ("SÉRIE DA DPS", _t(dps, "n:serie")),
                               ("DATA E HORA DA EMISSÃO DA DPS", _data(_t(dps, "n:dhEmi")))]),
                       (106.2, [("EMITENTE DA NFS-e", {"1": "Prestador", "2": "Tomador", "3": "Intermediário"}.get(_t(dps, "n:tpEmit"), "-")),
                                ("SITUAÇÃO DA NFS-e", "NFS-e Gerada" if _t(inf, "n:cStat") == "100" else _t(inf, "n:cStat")),
                                ("FINALIDADE", "-")])):
        for j, (rot, val) in enumerate(linha):
            g.txt(COLS[j], top, rot, 7, True)
            g.txt(COLS[j], top + 7.9, val, 7)

    # Prestador
    y = g.secao(125.6, "PRESTADOR / FORNECEDOR", [
        [None, ("CNPJ / CPF / NIF", _doc(_t(emit, "n:CNPJ|n:CPF", ""))), ("Indicador Municipal (Inscrição)", _t(emit, "n:IM")),
         ("Telefone", _t(emit, "n:fone"))],
        [("Nome / Nome Empresarial", _t(emit, "n:xNome")), None, ("Município / Sigla UF", f"{_t(inf, 'n:xLocEmi')} / {uf_emi or '-'}"),
         ("Código IBGE / CEP", f"{_ibge(_t(end_emit, 'n:cMun', ''))} / {_cep(_t(end_emit, 'n:CEP', ''))}")],
        [("Endereço", _endereco(end_emit)), None, ("E-mail", _t(emit, "n:email")), None],
        [("Simples Nacional na Data de Competência", OP_SN.get(_t(prest, "n:regTrib/n:opSimpNac"), "-")),
         ("Regime de Apuração Tributária pelo SN", REG_SN.get(_t(prest, "n:regTrib/n:regApTribSN"), "-")), None, None],
    ])

    # Tomador
    end_t = toma.find("n:end", NS) if toma is not None else None
    end_tn = end_t.find("n:endNac", NS) if end_t is not None else None
    y = g.secao(y + 0.4, "TOMADOR / ADQUIRENTE", [
        [None, ("CNPJ / CPF / NIF", _doc(_t(toma, "n:CNPJ|n:CPF", ""))), ("Indicador Municipal (Inscrição)", _t(toma, "n:IM")),
         ("Telefone", _t(toma, "n:fone"))],
        [("Nome / Nome Empresarial", _t(toma, "n:xNome")), None, ("Município / Sigla UF", "" if end_tn is None else "-"),
         ("Código IBGE / CEP", f"{_ibge(_t(end_tn, 'n:cMun', ''))} / {_cep(_t(end_tn, 'n:CEP', ''))}" if end_tn is not None else "-")],
        [("Endereço", _endereco(end_t)), None, ("E-mail", _t(toma, "n:email")), None],
    ])

    y += 0.4
    g.linha(8.5, y, 586.8)
    g.txt(297.6, y + 1.2, "DESTINATÁRIO DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e", 7, centro=True)
    g.linha(8.5, y + 8.4, 586.8)
    g.txt(297.6, y + 9.6, "INTERMEDIÁRIO DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e", 7, centro=True)
    y += 16.8

    # Serviço
    ctn = _t(serv, "n:cServ/n:cTribNac", "")
    cod = f"{ctn[:2]}.{ctn[2:4]}.{ctn[4:]}" if len(ctn) == 6 else ctn
    nbs = _t(serv, "n:cServ/n:cNBS", "")
    topo = y
    y = g.secao(y, "SERVIÇO PRESTADO", [
        [None, ("Código de Tributação Nacional/Municipal", f"{cod} / {_t(serv, 'n:cServ/n:cTribMun')}"),
         ("Código da NBS", f"{nbs[0]}.{nbs[1:5]}.{nbs[5:7]}.{nbs[7:]}" if len(nbs) == 9 else (nbs or "-")),
         ("Local da Prestação / Sigla UF / País", f"{_t(inf, 'n:xLocPrestacao')} / {uf_emi or '-'} / -")],
    ])
    y = g.paragrafo(topo + 20.3, _t(inf, "n:xTribNac", "-"))
    y += 4.1
    g.txt(COLS[0], y, "Descrição do Serviço", 6, True)
    y = g.paragrafo(y + 7.0, _t(serv, "n:cServ/n:xDescServ")) + 3.5

    # Tributação municipal
    tm = vdps.find("n:trib/n:tribMun", NS) if vdps is not None else None
    aliq = _t(vnf, "n:pAliqAplic")
    y = g.secao(y, "TRIBUTAÇÃO MUNICIPAL (ISSQN)", [
        [None, ("Tipo de Tributação do ISSQN", TRIB_ISS.get(_t(tm, "n:tribISSQN"), "-")),
         ("Município / Sigla UF / País de Incidência do ISSQN", f"{_t(inf, 'n:xLocIncid')} / {uf_emi or '-'} / -"), None],
        [("BC ISSQN", _brl(_t(vnf, "n:vBC", ""))), ("Alíquota Aplicada", (aliq + "%") if aliq != "-" else "-"),
         ("Retenção do ISSQN", RET_ISS.get(_t(tm, "n:tpRetISSQN"), "-")), ("ISSQN Apurado", _brl(_t(vnf, "n:vISSQN", "")))],
    ])

    tf = vdps.find("n:trib/n:tribFed", NS) if vdps is not None else None
    y = g.secao(y + 0.4, "TRIBUTAÇÃO FEDERAL (EXCETO CBS)", [
        [None, ("IRRF", _brl(_t(tf, "n:vRetIRRF", ""))), ("Contribuição Previdenciária - Retida", _brl(_t(tf, "n:vRetCP", ""))),
         ("Contribuições Sociais - Retidas", _brl(_t(tf, "n:vRetCSLL", "")))],
        [("PIS - Débito Apuração Própria", _brl(_t(tf, "n:piscofins/n:vPis", ""))),
         ("COFINS - Débito Apuração Própria", _brl(_t(tf, "n:piscofins/n:vCofins", ""))),
         ("Descrição Contrib. Sociais - Retidas", "-"), None],
    ])

    ib = inf.find("n:IBSCBS", NS)
    gi = lambda cam: _t(ib, cam)
    gd = lambda cam: _t(dps, "n:IBSCBS/" + cam)
    y = g.secao(y + 0.4, "TRIBUTAÇÃO IBS/CBS", [
        [None, ("CST / cClassTrib", f"{gd('n:valores/n:trib/n:gIBSCBS/n:CST')} / {gd('n:valores/n:trib/n:gIBSCBS/n:cClassTrib')}"),
         ("Indicador de Operação / Código IBGE Incidência / Município Incidência / Sigla UF",
          f"{gd('n:cIndOp')} / {gi('n:cLocalidadeIncid')} / {gi('n:xLocalidadeIncid')} / -"), None],
        [("Exclusões e Reduções da Base de Cálculo", _brl(_t(ib, "n:valores/n:vCalcReeRepRes", "0"))),
         ("Base de Cálculo Após Exclusões e Reduções", _brl(gi("n:valores/n:vBC"))),
         ("Red. Alíquota IBS / Red. Alíquota CBS", f"{gi('n:valores/n:uf/n:pRedAliqUF')} / {gi('n:valores/n:mun/n:pRedAliqMun')} / {gi('n:valores/n:fed/n:pRedAliqCBS')}"),
         ("Alíquota - IBS UF / IBS Mun", f"{gi('n:valores/n:uf/n:pIBSUF')} / {gi('n:valores/n:mun/n:pIBSMun')}")],
        [("Alíq. Efetiva Municipal - IBS", gi("n:valores/n:mun/n:pAliqEfetMun")), ("Valor Apurado Municipal - IBS", _brl(gi("n:totCIBS/n:gIBS/n:gIBSMunTot/n:vIBSMun"))),
         ("Alíq. Efetiva Estadual - IBS", gi("n:valores/n:uf/n:pAliqEfetUF")), ("Valor Apurado Estadual - IBS", _brl(gi("n:totCIBS/n:gIBS/n:gIBSUFTot/n:vIBSUF")))],
        [("Valor Total Apurado - IBS", _brl(gi("n:totCIBS/n:gIBS/n:vIBSTot"))), ("Alíquota - CBS", gi("n:valores/n:fed/n:pCBS")),
         ("Alíquota Efetiva - CBS", gi("n:valores/n:fed/n:pAliqEfetCBS")), ("Valor Total Apurado - CBS", _brl(gi("n:totCIBS/n:gCBS/n:vCBS")))],
    ])

    vserv = _t(vdps, "n:vServPrest/n:vServ", "0")
    y = g.secao(y + 0.4, "VALOR TOTAL DA NFS-e", [
        [None, ("VALOR DA OPERAÇÃO / SERVIÇO", _brl(vserv)),
         ("Desconto Incondicionado", _brl(_t(vdps, "n:vDescCondIncond/n:vDescIncond", ""))),
         ("Desconto Condicionado", _brl(_t(vdps, "n:vDescCondIncond/n:vDescCond", "")))],
        [("Total das Retenções (ISSQN / Federais)", _brl(_t(vnf, "n:vTotalRet", ""))),
         ("VALOR LÍQUIDO DA NFS-e", _brl(_t(vnf, "n:vLiq", vserv))),
         ("Total do IBS/CBS", _brl(_t(ib, "n:totCIBS/n:vTotNF", "0"))),
         ("VALOR LÍQUIDO DA NFS-e + IBS/CBS", _brl(_t(ib, "n:totCIBS/n:vTotNF", "0")), "cinza")],
    ])

    y += 0.4
    g.linha(8.5, y, 586.8)
    g.txt(COLS[0], y + 1.3, "INFORMAÇÕES COMPLEMENTARES", 7, True)
    tt = vdps.find("n:trib/n:totTrib/n:vTotTrib", NS) if vdps is not None else None
    info = ["Totais aproximados dos Tributos cfe. Lei n° 12.741/2012: "
            f"Federais: {_brl(_t(tt, 'n:vTotTribFed', '')) if tt is not None else '-'}; "
            f"Estaduais: {_brl(_t(tt, 'n:vTotTribEst', '')) if tt is not None else '-'}; "
            f"Municipais: {_brl(_t(tt, 'n:vTotTribMun', '')) if tt is not None else '-'};"]
    for extra in (_t(serv, "n:infoCompl/n:xInfComp", ""), _t(inf, "n:xOutInf", "")):
        if extra not in ("", "-"):
            info.append(extra)
    y += 21.4
    for parag in info:
        y = g.paragrafo(y, parag)

    # Canhoto
    for x0, x1 in ((8.5, 154.1), (153.1, 298.6), (297.6, 587.8)):
        g.linha(x0, 795.8, x1)
        g.linha(x0, 815.9, x1)
    for x in (9.0, 153.6, 298.1, 587.3):
        g.linha(x, 796.3, x, 815.9)
    g.txt(12.9, 797.2, "DATA CIENTIFICAÇÃO:", 6, True)
    g.txt(157.5, 797.2, "IDENTIFICAÇÃO E ASSINATURA", 6, True)
    g.txt(302.0, 797.2, "N° NFS-e / CHAVE NFS-e", 6, True)
    g.txt(302.0, 804.1, f"{_t(inf, 'n:nNFSe')} / {chave}", 7)
    c.showPage()
    c.save()
    return buf.getvalue()
