"""Testes do núcleo: XML no padrão da Receita, assinatura, leitura de listas e fluxo de emissão."""
import base64
import datetime as dt
import hashlib
import sys
import types
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from lxml import etree

AQUI = Path(__file__).parent


@pytest.fixture()
def ambiente(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("HOME", str(tmp_path))
    for m in [m for m in sys.modules if m.startswith("notara")]:
        del sys.modules[m]
    sys.modules.setdefault("webview", types.SimpleNamespace(FOLDER_DIALOG=2))
    import notara.app as app_mod
    import notara.dados as dados
    import notara.nfse as nfse
    return types.SimpleNamespace(app=app_mod, dados=dados, nfse=nfse, tmp=tmp_path)


def pfx(cnpj="11444777000161", dias=365):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"EMPRESA TESTE LTDA:{cnpj}")])
    agora = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(nome).issuer_name(nome).public_key(key.public_key())
            .serial_number(1).not_valid_before(agora - dt.timedelta(days=2))
            .not_valid_after(agora + dt.timedelta(days=dias)).sign(key, hashes.SHA256()))
    return pkcs12.serialize_key_and_certificates(b"t", key, cert, None, serialization.BestAvailableEncryption(b"1234"))


PERFIL = {"cnpj": "11444777000161", "nome": "EMPRESA TESTE", "municipio_ibge": "3550308", "serie": 7,
          "op_simples": 3, "aliquota_simples": 10.88,
          "servico": {"cod_nacional": "14.10.01", "cod_municipal": "001", "nbs": "126011000", "iss_retido": False}}
NOTA = {"cnpj": "11.222.333/0001-81", "nome": "CLIENTE & CIA", "descricao": "Serviço de lavanderia - Novembro", "valor": 9173.8}


def test_dps_valida_e_assinada(ambiente):
    n = ambiente.nfse
    cert = n.Certificado(pfx(), "1234")
    assert cert.cnpj == "11444777000161"
    root, ident = n.montar_dps(PERFIL, NOTA, 15, "teste")
    assert ident == "DPS3550308211444777000161" + "00007" + "15".zfill(15)
    xml = n.assinar(root, cert)
    n.validar(xml)
    doc = etree.fromstring(xml)
    ds = "{http://www.w3.org/2000/09/xmldsig#}"
    si = doc.find(f"{ds}Signature/{ds}SignedInfo")
    inf = doc.find(f"{{{n.NS}}}infDPS")
    assert base64.b64encode(hashlib.sha1(n._c14n(inf)).digest()).decode() == si.find(f".//{ds}DigestValue").text
    cert.cert.public_key().verify(base64.b64decode(doc.find(f"{ds}Signature/{ds}SignatureValue").text),
                                  n._c14n(si), padding.PKCS1v15(), hashes.SHA1())
    assert doc.find(f".//{{{n.NS}}}vServ").text == "9173.80"
    assert doc.find(f".//{{{n.NS}}}pTotTribSN").text == "10.88"


def test_regimes_e_cpf(ambiente):
    n = ambiente.nfse
    cert = n.Certificado(pfx(), "1234")
    for reg in (1, 2):
        root, _ = n.montar_dps({**PERFIL, "op_simples": reg}, {**NOTA, "cnpj": "529.982.247-25"}, 1, "producao")
        n.validar(n.assinar(root, cert))


def test_cancelamento_valido(ambiente):
    n = ambiente.nfse
    cert = n.Certificado(pfx(), "1234")
    chave = "3" * 50
    xml = n.assinar(n.montar_cancelamento(chave, PERFIL["cnpj"], "teste", 1, "Valor digitado errado na nota"), cert)
    n.validar(xml, "pedRegEvento_v1.01.xsd")


def test_senha_errada_e_vencido(ambiente):
    n = ambiente.nfse
    with pytest.raises(n.NfseErro, match="Senha"):
        n.Certificado(pfx(), "errada")
    assert n.Certificado(pfx(dias=-1), "1234").vencido


def test_importar_pdf_exemplo(ambiente):
    from notara import importar
    notas, ignoradas = importar.ler(importar.texto_pdf(AQUI / "exemplo_lista.pdf"))
    assert len(notas) == 3 and round(sum(x["valor"] for x in notas), 2) == 5214.66
    assert ignoradas == ["Unidade Leste"]
    assert notas[1]["descricao"].endswith("rua: Exemplo, 100")


def test_importar_texto_colado(ambiente):
    from notara import importar
    notas, _ = importar.ler("Hotel Bela Vista\t12.345.678/0001-90\tR$ 1.850,00\nPadaria; 11222333000181; 99,90\nlinha sem dados")
    assert [(x["unidade"], x["valor"]) for x in notas] == [("Hotel Bela Vista", 1850.0), ("Padaria", 99.9)]


def test_numeracao_por_maquina_e_ambiente(ambiente, monkeypatch):
    d = ambiente.dados
    p = d.perfil_novo("11444777000161", "X")
    assert d.serie_e_numero(p, "teste") == (1, 1)
    assert d.serie_e_numero(p, "teste") == (1, 2)
    assert d.serie_e_numero(p, "producao") == (1, 1)  # teste não consome a numeração real
    monkeypatch.setattr(d, "MAQUINA", "OUTRO-PC")
    assert d.serie_e_numero(p, "producao") == (2, 1)


def test_enderecos_e_chave(ambiente):
    n = ambiente.nfse
    assert n.url_valida("https://sefin.nfse.gov.br/SefinNacional")
    for ruim in ("http://sefin.nfse.gov.br", "https://sefin.nfse.gov.br.evil.com", "https://evil.gov.br",
                 "https://x@sefin.nfse.gov.br", "https://sefin.nfse.gov.br:8443"):
        assert not n.url_valida(ruim)
    assert n.chave_valida("1" * 50) and not n.chave_valida("x');alert(1)//")


def _cadastrar(ambiente, **extra):
    api = ambiente.app.Api()
    r = api.ler_certificado(base64.b64encode(pfx()).decode(), "1234")
    assert r["ok"] and r["certificado"]["cnpj"] == "11444777000161"
    r = api.salvar_empresa({"cnpj": "11444777000161", "nome": "EMPRESA TESTE", "municipio_ibge": "3550308",
                            "municipio": "São Paulo/SP", "op_simples": 3, "aliquota_simples": 10.88,
                            "servico": PERFIL["servico"], "descricao_modelo": "Lavanderia - {mes} de {ano} - {unidade}",
                            "pedir_senha": False, "pasta_notas": str(ambiente.tmp / "Notas"), **extra}, True)
    assert r["ok"], r
    return api


class ReceitaFalsa:
    falhar_em = {2}
    sem_resposta = False

    def __init__(self, *a):
        self.n = 0

    bloquear = False

    def testar(self, cmun):
        if self.bloquear:
            from notara import nfse
            raise nfse.Bloqueio("A Receita recusou o acesso", "acesso")
        return {"municipio_ativo": True}

    def dps_existe(self, i):
        return i.endswith("1")  # número 1 já usado na Receita

    def emitir(self, xml):
        from notara import nfse
        nfse.validar(xml)
        self.n += 1
        if self.sem_resposta:
            raise nfse.SemResposta("sem resposta")
        if self.n in self.falhar_em:
            raise nfse.NfseErro("E0001 recusada")
        return str(self.n) * 50, b'<NFSe xmlns="http://www.sped.fazenda.gov.br/nfse"><nNFSe>70%d</nNFSe></NFSe>' % self.n

    def chave_da_dps(self, i, tentativas=3, espera=5):
        return None

    def baixar_xml(self, c):
        return b""

    def danfse(self, c):
        return b"%PDF-1.4"


def test_validacoes_do_cadastro(ambiente):
    api = _cadastrar(ambiente)
    base = {"cnpj": "11444777000161"}
    assert not api.salvar_empresa({**base, "ambiente": "producao"})["ok"]
    assert not api.salvar_empresa({**base, "servico": {"cod_municipal": "1234"}})["ok"]
    assert not api.salvar_empresa({**base, "aliquota_simples": 0})["ok"]
    assert not api.salvar_empresa({**base, "urls": {"producao": {"sefin": "https://evil.com", "adn": "https://adn.nfse.gov.br"},
                                                    "teste": ambiente.nfse.URLS_PADRAO["teste"]}})["ok"]
    assert not api.remover_empresa("*")["ok"]


def test_fluxo_completo_com_receita_simulada(ambiente):
    a, d, n = ambiente.app, ambiente.dados, ambiente.nfse
    api = _cadastrar(ambiente)
    eventos = []
    api._evento = lambda nome, x: eventos.append((nome, x))
    perfil = d.empresa()
    receita = ReceitaFalsa()
    api._envio.acquire()
    api._lote(perfil, api._cert(perfil), receita, [{**NOTA, "unidade": "Unidade Centro"}, {**NOTA, "unidade": "B"}])
    assert not api._envio.locked()
    status = [x["status"] for nome, x in eventos if nome == "progresso"]
    assert status == ["enviando", "ok", "enviando", "erro"]
    assert eventos[-1][0] == "fim" and eventos[-1][1]["ok"] == 1
    item = d.historico(perfil)[0]
    pasta = Path(item["pdf"]).parent
    assert sorted(p.name for p in pasta.iterdir()) == ["NFS-e 701 - Unidade Centro.pdf", "NFS-e 701 - Unidade Centro.xml"]
    assert d.empresa()["teste_ok"] is True
    # cliente lembrado com unidade e último valor
    cli = api.clientes_salvos()
    assert cli[0]["nome"] == NOTA["nome"] and cli[0]["unidades"][0] == {
        "nome": "Unidade Centro", "valor": 9173.8, "descricao": NOTA["descricao"]}
    # abrir só dentro da pasta de notas
    assert api.abrir_nota(item["chave"], "pdf")["ok"] is not None
    assert api._dentro_das_notas("/etc/passwd") is None
    assert api.salvar_empresa({"cnpj": "11444777000161", "ambiente": "producao"})["ok"]


def test_sem_resposta_vira_pendente_e_nao_duplica(ambiente):
    d = ambiente.dados
    api = _cadastrar(ambiente)
    eventos = []
    api._evento = lambda nome, x: eventos.append((nome, x))
    perfil = d.empresa()
    r = ReceitaFalsa()
    r.sem_resposta = True
    api._envio.acquire()
    api._lote(perfil, api._cert(perfil), r, [NOTA])
    assert [x["status"] for nome, x in eventos if nome == "progresso"][-1] == "incerto"
    h = d.historico(perfil)
    assert h[0]["situacao"] == "pendente" and h[0]["ident"].startswith("DPS3550308")


def test_envio_nao_roda_em_dobro(ambiente):
    api = _cadastrar(ambiente)
    api._envio.acquire()
    assert api.emitir([NOTA])["erro"] == "Já existe um envio em andamento."


def test_pasta_compartilhada_migra_dados(ambiente):
    d = ambiente.dados
    api = _cadastrar(ambiente)
    perfil = d.empresa()
    d.registrar(perfil, {"data": "01/11/2026 10:00", "chave": "9" * 50, "valor": 1})
    d.salvar_cliente(perfil, "11222333000181", "CLIENTE", "Centro", 10, "x")
    d.serie_e_numero(perfil, "producao")
    comp = ambiente.tmp / "rede"
    assert api.salvar_empresa({"cnpj": perfil["cnpj"], "pasta_compartilhada": str(comp)})["ok"]
    novo = d.empresa()
    assert d.historico(novo)[0]["chave"] == "9" * 50
    assert "11222333000181" in d.clientes(novo)
    assert d.serie_e_numero(novo, "producao") == (1, 2)
    assert (comp / perfil["cnpj"] / "empresa.json").exists()


def test_descricao_do_cliente_atualiza_mes(ambiente):
    d = ambiente.dados
    api = _cadastrar(ambiente)
    d.salvar_cliente(d.empresa(), "11222333000181", "CLIENTE", "Centro", 10,
                     "Lavanderia - prestação de serviço de Janeiro de 2020 - Centro - rua: X, 1")
    desc = api.descricao_cliente("11222333000181", "Centro")
    assert "Janeiro de 2020" not in desc and desc.endswith("Centro - rua: X, 1")


def test_bloqueio_para_o_lote_sem_tentar_as_notas(ambiente):
    d = ambiente.dados
    api = _cadastrar(ambiente)
    eventos = []
    api._evento = lambda nome, x: eventos.append((nome, x))
    perfil = d.empresa()
    r = ReceitaFalsa()
    r.bloquear = True
    api._envio.acquire()
    api._lote(perfil, api._cert(perfil), r, [NOTA, NOTA, NOTA])
    assert r.n == 0  # nenhuma nota foi enviada
    assert [x["status"] for nome, x in eventos if nome == "progresso"] == ["parado"] * 3
    fim = eventos[-1][1]
    assert fim["ok"] == 0 and fim["bloqueio"]["tipo"] == "acesso"
    assert not api._envio.locked()


def test_assinatura_confere_em_verificador_independente(ambiente, tmp_path):
    """Regressão do erro E0714: a assinatura precisa validar na implementação XMLDSig do Java,
    que não tem nada em comum com o lxml/libxml2 usado para gerar."""
    import shutil
    import subprocess
    if not shutil.which("java") or not shutil.which("javac"):
        pytest.skip("Java não disponível")
    n = ambiente.nfse
    cert = n.Certificado(pfx(), "1234")
    subprocess.run(["javac", "-d", str(tmp_path), str(AQUI / "Verifica.java")], check=True, capture_output=True)
    dps = n.assinar(n.montar_dps(PERFIL, {**NOTA, "descricao": "Serviço de lavanderia - Novembro de 2026 - Unidade"}, 3, "producao")[0], cert)
    canc = n.assinar(n.montar_cancelamento("3" * 50, PERFIL["cnpj"], "producao", 1, "Valor digitado errado na nota"), cert)
    for nome, xml in (("dps.xml", dps), ("canc.xml", canc)):
        assert b'xmlns=""' not in xml
        (tmp_path / nome).write_bytes(xml)
        r = subprocess.run(["java", "-cp", str(tmp_path), "Verifica", str(tmp_path / nome)], capture_output=True, text=True)
        assert "=> VALIDA" in r.stdout, r.stdout + r.stderr


def test_dados_completos_do_cliente_e_da_empresa_na_nota(ambiente):
    """Cadastro completo vai para a nota e o XML continua no padrão da Receita."""
    n = ambiente.nfse
    cert = n.Certificado(pfx(), "1234")
    emp = {**PERFIL, "im": "6.414.179-9", "fone": "(11) 98181-9330", "email": "contato@empresa.com.br"}
    cli = {"im": "3.631.921-0", "email": "cliente@x.com", "fone": "1133334444", "cep": "08246-061",
           "logradouro": "R JUCUPEMA", "numero": "794", "bairro": "PARADA XV DE NOVEMBRO", "cmun": "3550308"}
    nota = {**NOTA, "cliente": cli, "info": "Contrato 123/2026"}
    xml = n.assinar(n.montar_dps(emp, nota, 1, "producao")[0], cert)
    n.validar(xml)
    for trecho in (b"<IM>64141799</IM>", b"<IM>36319210</IM>", b"<CEP>08246061</CEP>", b"<xInfComp>Contrato 123/2026</xInfComp>",
                   b"<email>cliente@x.com</email>", b"<cLocPrestacao>3550308</cLocPrestacao>"):
        assert trecho in xml, trecho


def test_pdf_gerado_a_partir_do_xml_da_nfse(ambiente):
    from notara import danfse
    pdf = danfse.gerar((AQUI / "nfse_exemplo.xml").read_bytes())
    assert pdf[:4] == b"%PDF" and len(pdf) > 3000
