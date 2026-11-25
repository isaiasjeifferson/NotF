"""Gera o DANFSe (PDF da nota) a partir do XML da NFS-e autorizada pela Receita.

O XML é o documento fiscal; o DANFSe é só a representação impressa dele. Gerar o PDF localmente
evita depender do download no site da Receita. O layout segue o DANFSe v2.0 do Emissor Nacional.
"""
from datetime import datetime
from io import BytesIO

from lxml import etree
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import HexColor, black
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

NS = {"n": "http://www.sped.fazenda.gov.br/nfse"}
CONSULTA = "https://www.nfse.gov.br/ConsultaPublica/?tpc=1&chave={chave}"
CINZA = HexColor("#EDEDED")
LINHA = HexColor("#8A8A8A")

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


class _Folha:
    def __init__(self, c):
        self.c, self.x0, self.w, self.y = c, 22, A4[0] - 44, A4[1] - 22

    def texto(self, x, y, s, tam=7, negrito=False, largura=None):
        fonte = "Helvetica-Bold" if negrito else "Helvetica"
        s = str(s)
        if largura:
            while s and stringWidth(s, fonte, tam) > largura:
                s = s[:-2]
        self.c.setFont(fonte, tam)
        self.c.drawString(x, y, s)

    def quebrar(self, s, largura, tam=7.5):
        linhas, atual = [], ""
        for palavra in str(s).split():
            teste = (atual + " " + palavra).strip()
            if stringWidth(teste, "Helvetica", tam) <= largura:
                atual = teste
            else:
                linhas.append(atual)
                atual = palavra
        linhas.append(atual)
        return [l for l in linhas if l] or ["-"]

    def secao(self, titulo, linhas, rotulo_col0=True):
        """linhas: lista de listas de (rótulo, valor) em 4 colunas. A 1ª célula da 1ª linha é o título."""
        col = self.w / 4
        self.c.setStrokeColor(LINHA)
        self.c.line(self.x0, self.y, self.x0 + self.w, self.y)
        self.c.setFillColor(CINZA)
        self.c.rect(self.x0, self.y - 12, col - 6, 12, stroke=0, fill=1)
        self.c.setFillColor(black)
        self.texto(self.x0 + 3, self.y - 9, titulo, 8, True)
        for i, linha in enumerate(linhas):
            for j, cel in enumerate(linha):
                if cel is None or (i == 0 and j == 0 and rotulo_col0):
                    continue
                rot, val = cel
                vaos = 1
                while j + vaos < len(linha) and linha[j + vaos] is None:
                    vaos += 1  # a célula ocupa as colunas vazias seguintes
                x, larg = self.x0 + j * col + 2, col * vaos - 6
                self.texto(x, self.y - 7, rot, 6, True, larg)
                self.texto(x, self.y - 15, val, 7.5, False, larg)
            self.y -= 20
        self.y -= 2

    def bloco(self, titulo, valor):
        self.texto(self.x0 + 2, self.y - 7, titulo, 6, True)
        linhas = self.quebrar(valor, self.w - 6)
        for k, l in enumerate(linhas):
            self.texto(self.x0 + 2, self.y - 15 - k * 9, l, 7.5)
        self.y -= 12 + 9 * len(linhas)


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

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"NFS-e {_t(inf, 'n:nNFSe')}")
    f = _Folha(c)

    # Cabeçalho
    c.setFont("Helvetica-Bold", 16)
    c.setFillColor(HexColor("#0B6A3A"))
    c.drawString(f.x0, f.y - 22, "NFS-e")
    c.setFillColor(black)
    c.setFont("Helvetica", 6.5)
    c.drawString(f.x0, f.y - 31, "Nota Fiscal de Serviço eletrônica")
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(A4[0] / 2, f.y - 14, "DANFSe v2.0")
    c.drawCentredString(A4[0] / 2, f.y - 26, "Documento Auxiliar da NFS-e")
    f.texto(A4[0] - 200, f.y - 12, f"Município: {_t(inf, 'n:xLocEmi')}", 8.5)
    f.texto(A4[0] - 200, f.y - 21, f"Ambiente Gerador: {_t(inf, 'n:ambGer')}", 6.5)
    f.texto(A4[0] - 200, f.y - 29, f"Tipo de Ambiente: {_t(dps, 'n:tpAmb')}", 6.5)
    f.y -= 40

    # QR code
    qr = QrCodeWidget(CONSULTA.format(chave=chave))
    b = qr.getBounds()
    tam = 64
    d = Drawing(tam, tam, transform=[tam / (b[2] - b[0]), 0, 0, tam / (b[3] - b[1]), 0, 0])
    d.add(qr)
    renderPDF.draw(d, c, A4[0] - 22 - 150, f.y - 64)
    for k, l in enumerate(["A autenticidade desta NFS-e pode ser verificada pela leitura",
                           "deste código QR ou pela consulta da chave de acesso no",
                           "portal nacional da NFS-e"]):
        f.texto(A4[0] - 22 - 150, f.y - 71 - k * 6.5, l, 5.6)

    col = (f.w - 160) / 3
    f.texto(f.x0 + 2, f.y - 7, "CHAVE DE ACESSO DA NFS-e", 6, True)
    f.texto(f.x0 + 2, f.y - 15, chave, 7.5)
    f.y -= 20
    for linha in ([("NÚMERO DA NFS-e", _t(inf, "n:nNFSe")), ("COMPETÊNCIA DA NFS-e", _data(_t(dps, "n:dCompet"), False)),
                   ("DATA E HORA DA EMISSÃO DA NFS-e", _data(_t(inf, "n:dhProc")))],
                  [("NÚMERO DA DPS", _t(dps, "n:nDPS")), ("SÉRIE DA DPS", _t(dps, "n:serie")),
                   ("DATA E HORA DA EMISSÃO DA DPS", _data(_t(dps, "n:dhEmi")))],
                  [("EMITENTE DA NFS-e", {"1": "Prestador", "2": "Tomador", "3": "Intermediário"}.get(_t(dps, "n:tpEmit"), "-")),
                   ("SITUAÇÃO DA NFS-e", "NFS-e Gerada" if _t(inf, "n:cStat") == "100" else _t(inf, "n:cStat")),
                   ("FINALIDADE", "-")]):
        for j, (rot, val) in enumerate(linha):
            f.texto(f.x0 + 2 + j * col, f.y - 7, rot, 6, True)
            f.texto(f.x0 + 2 + j * col, f.y - 15, val, 7.5)
        f.y -= 20
    f.y -= 10

    end_emit = emit.find("n:enderNac", NS) if emit is not None else None
    f.secao("PRESTADOR / FORNECEDOR", [
        [None, ("CNPJ / CPF / NIF", _doc(_t(emit, "n:CNPJ|n:CPF", ""))), ("Indicador Municipal (Inscrição)", _t(emit, "n:IM")),
         ("Telefone", _t(emit, "n:fone"))],
        [("Nome / Nome Empresarial", _t(emit, "n:xNome")), None, ("Município / Sigla UF", f"{_t(inf, 'n:xLocEmi')} / {_t(end_emit, 'n:UF')}"),
         ("Código IBGE / CEP", f"{_t(end_emit, 'n:cMun')} / {_t(end_emit, 'n:CEP')}")],
        [("Endereço", _endereco(end_emit)), None, ("E-mail", _t(emit, "n:email")), None],
        [("Simples Nacional na Data de Competência", OP_SN.get(_t(prest, "n:regTrib/n:opSimpNac"), "-")),
         ("Regime de Apuração Tributária pelo SN", REG_SN.get(_t(prest, "n:regTrib/n:regApTribSN"), "-")), None, None],
    ])

    end_t = toma.find("n:end", NS) if toma is not None else None
    end_tn = end_t.find("n:endNac", NS) if end_t is not None else None
    f.secao("TOMADOR / ADQUIRENTE", [
        [None, ("CNPJ / CPF / NIF", _doc(_t(toma, "n:CNPJ|n:CPF", ""))), ("Indicador Municipal (Inscrição)", _t(toma, "n:IM")),
         ("Telefone", _t(toma, "n:fone"))],
        [("Nome / Nome Empresarial", _t(toma, "n:xNome")), None, ("Município / Sigla UF", "-"),
         ("Código IBGE / CEP", f"{_t(end_tn, 'n:cMun')} / {_t(end_tn, 'n:CEP')}" if end_tn is not None else "-")],
        [("Endereço", _endereco(end_t)), None, ("E-mail", _t(toma, "n:email")), None],
    ])

    f.c.setStrokeColor(LINHA)
    f.c.line(f.x0, f.y, f.x0 + f.w, f.y)
    f.c.setFont("Helvetica", 7)
    f.c.drawCentredString(A4[0] / 2, f.y - 8, "INTERMEDIÁRIO DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e")
    f.y -= 12

    ctn = _t(serv, "n:cServ/n:cTribNac", "")
    cod = f"{ctn[:2]}.{ctn[2:4]}.{ctn[4:]}" if len(ctn) == 6 else ctn
    nbs = _t(serv, "n:cServ/n:cNBS", "")
    f.secao("SERVIÇO PRESTADO", [
        [None, ("Código de Tributação Nacional/Municipal", f"{cod} / {_t(serv, 'n:cServ/n:cTribMun')}"),
         ("Código da NBS", f"{nbs[0]}.{nbs[1:5]}.{nbs[5:7]}.{nbs[7:]}" if len(nbs) == 9 else (nbs or "-")),
         ("Local da Prestação / Sigla UF / País", f"{_t(inf, 'n:xLocPrestacao')}")],
    ])
    f.texto(f.x0 + 2, f.y - 4, _t(inf, "n:xTribNac", ""), 7.5)
    f.y -= 8
    f.bloco("Descrição do Serviço", _t(serv, "n:cServ/n:xDescServ"))

    tm = vdps.find("n:trib/n:tribMun", NS) if vdps is not None else None
    f.secao("TRIBUTAÇÃO MUNICIPAL (ISSQN)", [
        [None, ("Tipo de Tributação do ISSQN", TRIB_ISS.get(_t(tm, "n:tribISSQN"), "-")),
         ("Município / Sigla UF / País de Incidência do ISSQN", _t(inf, "n:xLocIncid")), None],
        [("BC ISSQN", _brl(_t(vnf, "n:vBC", ""))), ("Alíquota Aplicada", (_t(vnf, "n:pAliqAplic") + "%") if _t(vnf, "n:pAliqAplic") != "-" else "-"),
         ("Retenção do ISSQN", RET_ISS.get(_t(tm, "n:tpRetISSQN"), "-")), ("ISSQN Apurado", _brl(_t(vnf, "n:vISSQN", "")))],
    ])

    vserv = _t(vdps, "n:vServPrest/n:vServ", "0")
    f.secao("VALOR TOTAL DA NFS-e", [
        [None, ("VALOR DA OPERAÇÃO / SERVIÇO", _brl(vserv)),
         ("Desconto Incondicionado", _brl(_t(vdps, "n:vDescCondIncond/n:vDescIncond", ""))),
         ("Desconto Condicionado", _brl(_t(vdps, "n:vDescCondIncond/n:vDescCond", "")))],
        [("Total das Retenções (ISSQN / Federais)", _brl(_t(vnf, "n:vTotalRet", ""))),
         ("VALOR LÍQUIDO DA NFS-e", _brl(_t(vnf, "n:vLiq", vserv))), None, None],
    ])

    ptot = _t(vdps, "n:trib/n:totTrib/n:pTotTribSN", "")
    info = [f"Totais aproximados dos Tributos cfe. Lei n° 12.741/2012: "
            + (f"{ptot.replace('.', ',')}% (alíquota do Simples Nacional)" if ptot not in ("", "-") else "Federais: -; Estaduais: -; Municipais: -;")]
    extra = _t(serv, "n:infoCompl/n:xInfComp", "")
    if extra not in ("", "-"):
        info.append(extra)
    outras = _t(inf, "n:xOutInf", "")
    if outras not in ("", "-"):
        info.append(outras)
    f.c.setStrokeColor(LINHA)
    f.c.line(f.x0, f.y, f.x0 + f.w, f.y)
    f.texto(f.x0 + 3, f.y - 9, "INFORMAÇÕES COMPLEMENTARES", 8, True)
    f.y -= 14
    for parag in info:
        f.bloco("", parag)

    # Canhoto
    col = f.w / 4
    yb = 50
    f.c.setStrokeColor(LINHA)
    f.c.rect(f.x0, yb - 20, f.w, 34, stroke=1, fill=0)
    f.texto(f.x0 + 4, yb + 6, "DATA CIENTIFICAÇÃO:", 6, True)
    f.texto(f.x0 + col + 4, yb + 6, "IDENTIFICAÇÃO E ASSINATURA", 6, True)
    f.texto(f.x0 + 2 * col + 4, yb + 6, "N° NFS-e / CHAVE NFS-e", 6, True)
    f.texto(f.x0 + 2 * col + 4, yb - 3, f"{_t(inf, 'n:nNFSe')} / {chave}", 6.5)
    f.texto(f.x0, 18, "Representação gráfica gerada pelo Notara a partir do XML da NFS-e autorizada. "
                      "Confira a autenticidade pela chave de acesso no portal nacional da NFS-e.", 5.5)
    c.showPage()
    c.save()
    return buf.getvalue()
