"""Comunicação com o Sistema Nacional da NFS-e: montar, assinar, validar e enviar."""
import base64
import gzip
import hashlib
import os
import re
import secrets
import ssl
import tempfile
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import pkcs12
from lxml import etree

from . import __version__

NS = "http://www.sped.fazenda.gov.br/nfse"
DS = "http://www.w3.org/2000/09/xmldsig#"
C14N = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
BRT = timezone(timedelta(hours=-3))
VER_APLIC = f"Notara-{__version__}"[:20]
OID_CNPJ = "2.16.76.1.3.3"  # ICP-Brasil: CNPJ da pessoa jurídica no certificado

URLS_PADRAO = {
    "producao": {"sefin": "https://sefin.nfse.gov.br/SefinNacional", "adn": "https://adn.nfse.gov.br"},
    "teste": {"sefin": "https://sefin.producaorestrita.nfse.gov.br/SefinNacional",
              "adn": "https://adn.producaorestrita.nfse.gov.br"},
}
SCHEMAS = Path(__file__).parent / "schemas"
HOSTS_RECEITA = {"sefin.nfse.gov.br", "adn.nfse.gov.br",
                 "sefin.producaorestrita.nfse.gov.br", "adn.producaorestrita.nfse.gov.br"}


def url_valida(url):
    from urllib.parse import urlparse
    u = urlparse(url or "")
    return u.scheme == "https" and u.hostname in HOSTS_RECEITA and not u.username and u.port in (None, 443)


def chave_valida(chave):
    return bool(re.fullmatch(r"\d{50}", str(chave or "")))


class NfseErro(Exception):
    """Erro com mensagem pronta para mostrar ao usuário."""


class SemResposta(NfseErro):
    """A Receita pode ter recebido o pedido, mas a resposta não chegou."""


def digitos(s):
    return "".join(ch for ch in str(s or "") if ch.isdigit())


def dinheiro(v):
    return str(valor_decimal(v))


def valor_decimal(v):
    try:
        return Decimal(str(v).strip()).quantize(Decimal("0.01"), ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise NfseErro("Valor inválido.")


# ---------------------------------------------------------------- certificado
class Certificado:
    def __init__(self, pfx: bytes, senha: str):
        try:
            self.key, self.cert, extras = pkcs12.load_key_and_certificates(pfx, senha.encode())
        except ValueError:
            raise NfseErro("Senha do certificado incorreta.")
        if self.key is None or self.cert is None:
            raise NfseErro("Este arquivo não contém um certificado A1 válido.")
        self.extras = extras or []

    @classmethod
    def abrir(cls, caminho, senha):
        try:
            return cls(Path(caminho).read_bytes(), senha)
        except FileNotFoundError:
            raise NfseErro("Arquivo do certificado não encontrado. Escolha-o de novo em Ajustes.")

    @property
    def validade(self):
        try:
            return self.cert.not_valid_after_utc
        except AttributeError:
            return self.cert.not_valid_after.replace(tzinfo=timezone.utc)

    @property
    def vencido(self):
        return self.validade < datetime.now(timezone.utc)

    @property
    def nome(self):
        cn = [a.value for a in self.cert.subject if a.oid == x509.NameOID.COMMON_NAME]
        return cn[0].rsplit(":", 1)[0].strip() if cn else ""

    @property
    def cnpj(self):
        try:
            san = self.cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            for nome in san:
                if isinstance(nome, x509.OtherName) and nome.type_id.dotted_string == OID_CNPJ:
                    d = digitos(nome.value.decode("latin-1"))
                    if len(d) >= 14:
                        return d[-14:]
        except (x509.ExtensionNotFound, ValueError):
            pass
        cn = [a.value for a in self.cert.subject if a.oid == x509.NameOID.COMMON_NAME]
        d = digitos(cn[0].split(":")[-1]) if cn and ":" in cn[0] else ""
        return d if len(d) == 14 else ""

    def resumo(self):
        dias = (self.validade - datetime.now(timezone.utc)).days
        return {"nome": self.nome, "cnpj": self.cnpj, "validade": self.validade.strftime("%d/%m/%Y"), "dias": dias}

    def cert_b64(self):
        return base64.b64encode(self.cert.public_bytes(serialization.Encoding.DER)).decode()

    def assinar(self, dados: bytes) -> bytes:
        return self.key.sign(dados, padding.PKCS1v15(), hashes.SHA1())

    def contexto_tls(self):
        """Contexto TLS com a chave só em memória.

        O OpenSSL só carrega chaves de arquivo: gravamos a chave cifrada com uma senha aleatória,
        carregamos e apagamos na hora. O arquivo nunca fica sem cifra no disco.
        """
        senha = secrets.token_bytes(32)
        ctx = ssl.create_default_context()
        fd, caminho = tempfile.mkstemp(prefix="notara_", suffix=".pem")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.BestAvailableEncryption(senha)))
                f.write(self.cert.public_bytes(serialization.Encoding.PEM))
                for x in self.extras:
                    f.write(x.public_bytes(serialization.Encoding.PEM))
            ctx.load_cert_chain(caminho, password=senha)
        finally:
            os.remove(caminho)
        return ctx


# ---------------------------------------------------------------- XML
def _el(pai, tag, texto=None):
    e = etree.SubElement(pai, f"{{{NS}}}{tag}")
    if texto is not None:
        e.text = str(texto)
    return e


def id_dps(cmun, cnpj, serie, numero):
    return f"DPS{cmun}2{digitos(cnpj).zfill(14)}{str(serie).zfill(5)}{str(numero).zfill(15)}"


def agora():
    return datetime.now(BRT) - timedelta(minutes=1)  # a Receita recusa horário no futuro


def montar_dps(empresa, nota, numero, ambiente, quando=None):
    """empresa: perfil salvo. nota: {cnpj, nome, descricao, valor}."""
    t = quando or agora()
    cmun = empresa["municipio_ibge"]
    ident = id_dps(cmun, empresa["cnpj"], empresa["serie"], numero)
    s = empresa["servico"]

    root = etree.Element(f"{{{NS}}}DPS", nsmap={None: NS}, versao="1.01")
    inf = _el(root, "infDPS")
    inf.set("Id", ident)
    _el(inf, "tpAmb", 1 if ambiente == "producao" else 2)
    _el(inf, "dhEmi", t.strftime("%Y-%m-%dT%H:%M:%S-03:00"))
    _el(inf, "verAplic", VER_APLIC)
    _el(inf, "serie", empresa["serie"])
    _el(inf, "nDPS", numero)
    _el(inf, "dCompet", t.strftime("%Y-%m-%d"))
    _el(inf, "tpEmit", 1)
    _el(inf, "cLocEmi", cmun)

    prest = _el(inf, "prest")
    _el(prest, "CNPJ", digitos(empresa["cnpj"]))
    rt = _el(prest, "regTrib")
    _el(rt, "opSimpNac", empresa["op_simples"])
    if int(empresa["op_simples"]) == 3:
        _el(rt, "regApTribSN", 1)
    _el(rt, "regEspTrib", 0)

    toma = _el(inf, "toma")
    doc = digitos(nota["cnpj"])
    _el(toma, "CNPJ" if len(doc) == 14 else "CPF", doc)
    _el(toma, "xNome", nota["nome"].strip()[:300])

    serv = _el(inf, "serv")
    _el(_el(serv, "locPrest"), "cLocPrestacao", cmun)
    cs = _el(serv, "cServ")
    _el(cs, "cTribNac", digitos(s["cod_nacional"]))
    if s.get("cod_municipal"):
        _el(cs, "cTribMun", digitos(s["cod_municipal"]))
    _el(cs, "xDescServ", nota["descricao"].strip()[:2000])
    if s.get("nbs"):
        _el(cs, "cNBS", digitos(s["nbs"]))

    val = _el(inf, "valores")
    _el(_el(val, "vServPrest"), "vServ", dinheiro(nota["valor"]))
    trib = _el(val, "trib")
    tm = _el(trib, "tribMun")
    _el(tm, "tribISSQN", 1)
    _el(tm, "tpRetISSQN", 2 if s.get("iss_retido") else 1)
    tt = _el(trib, "totTrib")
    if int(empresa["op_simples"]) == 3:
        _el(tt, "pTotTribSN", dinheiro(empresa["aliquota_simples"]))
    else:
        _el(tt, "indTotTrib", 0)
    return root, ident


def montar_cancelamento(chave, cnpj, ambiente, motivo_cod, motivo_txt):
    root = etree.Element(f"{{{NS}}}pedRegEvento", nsmap={None: NS}, versao="1.01")
    inf = _el(root, "infPedReg")
    inf.set("Id", f"PRE{chave}101101")
    _el(inf, "tpAmb", 1 if ambiente == "producao" else 2)
    _el(inf, "verAplic", VER_APLIC)
    _el(inf, "dhEvento", agora().strftime("%Y-%m-%dT%H:%M:%S-03:00"))
    _el(inf, "CNPJAutor", digitos(cnpj))
    _el(inf, "chNFSe", chave)
    ev = _el(inf, "e101101")
    _el(ev, "xDesc", "Cancelamento de NFS-e")
    _el(ev, "cMotivo", motivo_cod)
    _el(ev, "xMotivo", motivo_txt.strip()[:255])
    return root


def assinar(root, cert: Certificado):
    inf = root[0]
    digest = base64.b64encode(hashlib.sha1(etree.tostring(inf, method="c14n")).digest()).decode()
    sig = etree.SubElement(root, f"{{{DS}}}Signature", nsmap={None: DS})
    si = etree.SubElement(sig, f"{{{DS}}}SignedInfo")
    etree.SubElement(si, f"{{{DS}}}CanonicalizationMethod", Algorithm=C14N)
    etree.SubElement(si, f"{{{DS}}}SignatureMethod", Algorithm=DS + "rsa-sha1")
    ref = etree.SubElement(si, f"{{{DS}}}Reference", URI="#" + inf.get("Id"))
    tr = etree.SubElement(ref, f"{{{DS}}}Transforms")
    etree.SubElement(tr, f"{{{DS}}}Transform", Algorithm=DS + "enveloped-signature")
    etree.SubElement(tr, f"{{{DS}}}Transform", Algorithm=C14N)
    etree.SubElement(ref, f"{{{DS}}}DigestMethod", Algorithm=DS + "sha1")
    etree.SubElement(ref, f"{{{DS}}}DigestValue").text = digest
    valor = cert.assinar(etree.tostring(si, method="c14n"))
    etree.SubElement(sig, f"{{{DS}}}SignatureValue").text = base64.b64encode(valor).decode()
    x509d = etree.SubElement(etree.SubElement(sig, f"{{{DS}}}KeyInfo"), f"{{{DS}}}X509Data")
    etree.SubElement(x509d, f"{{{DS}}}X509Certificate").text = cert.cert_b64()
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


_schemas = {}


def validar(xml: bytes, xsd="DPS_v1.01.xsd"):
    if xsd not in _schemas:
        _schemas[xsd] = etree.XMLSchema(etree.parse(str(SCHEMAS / xsd)))
    sch = _schemas[xsd]
    if not sch.validate(etree.fromstring(xml)):
        erro = sch.error_log.last_error
        raise NfseErro(f"Dado fora do padrão da Receita: {erro.message if erro else ''}"[:300])


def numero_nfse(xml_nfse: bytes):
    try:
        el = etree.fromstring(xml_nfse).find(f".//{{{NS}}}nNFSe")
        return el.text if el is not None else ""
    except etree.XMLSyntaxError:
        return ""


def _desempacotar(b64):
    try:
        return gzip.decompress(base64.b64decode(b64)) if b64 else b""
    except (ValueError, OSError, EOFError):
        return b""


def _gz64(xml: bytes):
    return base64.b64encode(gzip.compress(xml)).decode()


def _mensagens(dados):
    erros = dados.get("erros") or dados.get("Erros") or []
    if isinstance(erros, dict):
        erros = [erros]
    out = []
    for e in erros:
        if isinstance(e, dict):
            cod = e.get("Codigo") or e.get("codigo") or ""
            desc = e.get("Descricao") or e.get("descricao") or e.get("mensagem") or ""
            out.append(f"{cod} {desc}".strip())
    return "; ".join(out)


# ---------------------------------------------------------------- conexão
class _AdaptadorTLS(HTTPAdapter):
    def __init__(self, ctx, **kw):
        self._ctx = ctx
        super().__init__(**kw)

    def init_poolmanager(self, *a, **kw):
        kw["ssl_context"] = self._ctx
        return super().init_poolmanager(*a, **kw)

    def proxy_manager_for(self, proxy, **kw):  # redes com proxy também enviam o certificado
        kw["ssl_context"] = self._ctx
        return super().proxy_manager_for(proxy, **kw)


class Receita:
    def __init__(self, cert: Certificado, urls: dict, timeout=60):
        if not (url_valida(urls.get("sefin")) and url_valida(urls.get("adn"))):
            raise NfseErro("Endereço da Receita inválido. Restaure o padrão em Ajustes.")
        self.urls, self.timeout = urls, timeout
        self.sessao = requests.Session()
        self.sessao.mount("https://", _AdaptadorTLS(cert.contexto_tls()))

    def _req(self, metodo, url, **kw):
        try:
            return self.sessao.request(metodo, url, timeout=self.timeout, allow_redirects=False, **kw)
        except requests.exceptions.SSLError:
            raise NfseErro("A Receita não aceitou o certificado. Confira se ele é o e-CNPJ da empresa e está válido.")
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            raise SemResposta("Sem resposta da Receita. Verifique a internet.") from e
        except requests.exceptions.RequestException as e:
            raise SemResposta("Falha de comunicação com a Receita.") from e

    def testar(self, cmun):
        r = self._req("GET", f"{self.urls['sefin']}/parametros_municipais/{cmun}/convenio")
        if r.status_code in (401, 403, 495, 496):
            raise NfseErro(f"A Receita recusou a conexão (código {r.status_code}). Faça uma nota de teste para ver o motivo.")
        if r.status_code >= 500:
            raise NfseErro("O sistema da Receita está fora do ar no momento.")
        return {"municipio_ativo": r.status_code == 200}

    def dps_existe(self, ident):
        r = self._req("HEAD", f"{self.urls['sefin']}/dps/{ident}")
        if r.status_code >= 500:
            raise SemResposta("O sistema da Receita está instável. Tente em instantes.")
        return r.status_code == 200

    def chave_da_dps(self, ident, tentativas=3, espera=5):
        """Descobre se uma DPS já virou nota. Usado quando a resposta do envio se perdeu."""
        for i in range(tentativas):
            try:
                r = self._req("GET", f"{self.urls['sefin']}/dps/{ident}")
                if r.status_code == 200:
                    chave = (r.json() or {}).get("chaveAcesso")
                    if chave_valida(chave):
                        return chave
                elif r.status_code == 404 and i == tentativas - 1:
                    return None
            except (NfseErro, ValueError):
                pass
            if i < tentativas - 1:
                time.sleep(espera)
        return None

    def emitir(self, xml: bytes):
        r = self._req("POST", f"{self.urls['sefin']}/nfse", json={"dpsXmlGZipB64": _gz64(xml)})
        try:
            dados = r.json()
        except ValueError:
            if r.status_code >= 500:
                raise SemResposta(f"A Receita não confirmou o envio (código {r.status_code}).")
            raise NfseErro(f"Resposta inesperada da Receita (código {r.status_code}).")
        chave = dados.get("chaveAcesso")
        if r.status_code in (200, 201) and chave_valida(chave):
            return chave, _desempacotar(dados.get("nfseXmlGZipB64"))
        if r.status_code >= 500:
            raise SemResposta(_mensagens(dados) or f"A Receita não confirmou o envio (código {r.status_code}).")
        raise NfseErro(_mensagens(dados) or f"Nota recusada pela Receita (código {r.status_code}).")

    def baixar_xml(self, chave):
        try:
            r = self._req("GET", f"{self.urls['sefin']}/nfse/{chave}")
            return _desempacotar(r.json().get("nfseXmlGZipB64")) if r.status_code == 200 else b""
        except (NfseErro, ValueError):
            return b""

    def danfse(self, chave):
        r = self._req("GET", f"{self.urls['adn']}/danfse/{chave}")
        return r.content if r.status_code == 200 and r.content[:4] == b"%PDF" else None

    def cancelar(self, chave, xml: bytes):
        r = self._req("POST", f"{self.urls['sefin']}/nfse/{chave}/eventos",
                      json={"pedidoRegistroEventoXmlGZipB64": _gz64(xml)})
        try:
            dados = r.json()
        except ValueError:
            dados = {}
        if r.status_code not in (200, 201):
            raise NfseErro(_mensagens(dados) or f"Cancelamento recusado (código {r.status_code}).")
        return True
