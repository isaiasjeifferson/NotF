"""Notara: ponte entre a interface e o núcleo de emissão.

Todo método público desta classe fica acessível ao JavaScript da janela. Por isso cada um valida
os próprios parâmetros e nunca confia em caminhos, URLs ou chaves vindos da interface ou de arquivos.
"""
import base64
import hashlib
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

import requests

from . import __version__, catalogo, correio, dados, importar, nfse
from .nfse import Bloqueio, NfseErro, SemResposta, digitos

REPO = "isaiasjeifferson/NotF"
REPOS_OK = ("isaiasjeifferson/notara", "isaiasjeifferson/notf")
ENVIO_PADRAO = {"provedor": "gmail", "servidor": "smtp.gmail.com", "porta": 587, "usuario": "", "nome_remetente": "",
                "responder_para": "", "cc_padrao": "", "assunto": correio.ASSUNTO, "mensagem": correio.MENSAGEM, "auto": False}
PDF_V = 3  # versão do desenho do PDF; ao mudar, os PDFs antigos são refeitos quando abertos
MOTIVOS = {"1": "Erro na emissão", "2": "Serviço não prestado", "9": "Outro motivo"}
CAMPOS_EMPRESA = ("nome", "apelido", "municipio_ibge", "municipio", "descricao_modelo", "pasta_notas",
                  "im", "ie", "fone", "email")

LOG = dados.APP / "notara.log"
logging.basicConfig(filename=LOG, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
log = logging.getLogger("notara")


def _cnpj_ok(c):
    return bool(re.fullmatch(r"\d{14}", str(c or "")))


def _publico(p):
    if not p:
        return None
    lista = []
    for x in dados.servicos(p):
        try:
            oficial = catalogo.servico(x["cod_nacional"])
            lista.append({**x, "descricao_servico": oficial["descricao"] if oficial else "",
                          "descricao_nbs": catalogo.nbs_descricao(x.get("nbs"))})
        except Exception:
            log.exception("catálogo")
            lista.append(x)
    logo = ""
    try:
        if p.get("logo") and Path(p["logo"]).exists():
            logo = "data:image/" + ("png" if p["logo"].endswith(".png") else "jpeg") + ";base64," + base64.b64encode(Path(p["logo"]).read_bytes()).decode()
    except OSError:
        pass
    envio = {**ENVIO_PADRAO, **(p.get("envio_email") or {})}
    envio["senha_ok"] = dados._cred(p["cnpj"], "smtp").exists()
    return {**p, "urls": p.get("urls") or nfse.URLS_PADRAO, "servicos": lista, "logo_url": logo, "envio_email": envio,
            "im_na_nota": p.get("im_na_nota", True)}


def _versao(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return (0,)


class Api:
    def __init__(self):
        self._janela = None
        self._certs = {}
        self._pendente = None
        self._envio = threading.Lock()
        self._update = None

    # ------------------------------------------------------------ apoio interno
    def _evento(self, nome, dado):
        import json
        if self._janela:
            self._janela.evaluate_js(f"app.evento({json.dumps(nome)},{json.dumps(dado, ensure_ascii=False)})")

    def _perfil(self, cnpj=None):
        p = dados.empresa(cnpj)
        if not p:
            raise NfseErro("Cadastre uma empresa primeiro.")
        return p

    def _cert(self, perfil, senha=None):
        cnpj = perfil["cnpj"]
        if senha is None and cnpj in self._certs:
            return self._certs[cnpj]
        try:
            pfx, salva = dados.ler_certificado(cnpj)
        except OSError as e:
            raise NfseErro(str(e))
        if pfx is None:
            raise NfseErro("Certificado não encontrado. Importe-o em Ajustes › Empresa.")
        senha = senha if senha is not None else salva
        if senha is None:
            raise NfseErro("SENHA")  # a interface pede a senha
        cert = nfse.Certificado(pfx, senha)
        if cert.vencido:
            raise NfseErro("O certificado venceu. Troque-o em Ajustes › Empresa.")
        if not perfil.get("pedir_senha"):
            self._certs[cnpj] = cert
        return cert

    @staticmethod
    def _receita(perfil, cert, ambiente=None):
        if (ambiente or perfil["ambiente"]) == "teste":  # modo teste: simulação local, nada vai à Receita
            return nfse.ReceitaSimulada(cert, perfil)
        return nfse.Receita(cert, (perfil.get("urls") or nfse.URLS_PADRAO)[ambiente or perfil["ambiente"]])

    @staticmethod
    def _tratar(fn, *a):
        try:
            return {"ok": True, **(fn(*a) or {})}
        except NfseErro as e:
            return {"ok": False, "erro": str(e)}
        except TimeoutError as e:
            return {"ok": False, "erro": str(e)}
        except Exception:
            log.exception("erro inesperado")
            return {"ok": False, "erro": "Algo deu errado. Os detalhes foram salvos em notara.log."}

    def _dentro_das_notas(self, caminho):
        """Só abre o que o próprio Notara salvou: PDF/XML ou pastas dentro da pasta de notas."""
        p = dados.empresa()
        if not p or not caminho:
            return None
        try:
            alvo = Path(caminho).resolve(strict=True)
            base = Path(p["pasta_notas"]).resolve()
        except (OSError, RuntimeError):
            return None
        if base not in (alvo, *alvo.parents):
            return None
        if alvo.is_dir() or alvo.suffix.lower() in (".pdf", ".xml"):
            return alvo
        return None

    # ------------------------------------------------------------ estado
    def estado(self):
        a = dados.ajustes()
        p = dados.empresa()
        cert = None
        if p:
            cert = {"validade": p.get("cert_validade", ""), "protegido": bool(p.get("pedir_senha"))}
            if p.get("cert_validade"):
                venc = datetime.strptime(p["cert_validade"], "%d/%m/%Y")
                cert["dias"] = (venc - datetime.now()).days
            if not (dados.APP / "credenciais" / f"{p['cnpj']}.pfx").exists():
                cert["erro"] = "Certificado não encontrado neste computador."
        hoje = datetime.now()
        try:
            historico = dados.historico(p)[:500] if p else []
        except Exception:
            log.exception("histórico")
            historico = []
        return {
            "versao": __version__,
            "mes": f"{dados.MESES[hoje.month - 1]} de {hoje.year}",
            "empresas": [{"cnpj": c, "nome": e.get("apelido") or e["nome"], "razao": e["nome"],
                          "ambiente": e.get("ambiente", "teste")} for c, e in a["empresas"].items()],
            "empresa": _publico(p),
            "certificado": cert,
            "historico": historico,
            "motivos": MOTIVOS,
            "ocupado": self._envio.locked(),
            "tema": a.get("tema", "claro"),
        }

    def definir_tema(self, tema):
        if tema in ("auto", "claro", "escuro"):
            dados.alterar_ajustes(lambda a: a.__setitem__("tema", tema))
        return {"ok": True}

    def trocar_empresa(self, cnpj):
        if self._envio.locked():
            return {"ok": False, "erro": "Aguarde o envio terminar."}
        def ativar(a):
            if cnpj in a["empresas"]:
                a["ativa"] = cnpj
        dados.alterar_ajustes(ativar)
        return {"ok": True, "estado": self.estado()}

    # ------------------------------------------------------------ cadastro
    def ler_certificado(self, b64, senha):
        """Etapa 1: abre o .pfx, lê CNPJ e validade, e busca os dados públicos da empresa."""
        def f():
            try:
                pfx = base64.b64decode(b64 or "", validate=True)
            except ValueError:
                raise NfseErro("Arquivo inválido.")
            cert = nfse.Certificado(pfx, senha or "")
            if cert.vencido:
                raise NfseErro(f"Este certificado venceu em {cert.validade:%d/%m/%Y}.")
            r = cert.resumo()
            if not r["cnpj"]:
                raise NfseErro("Este certificado não é de empresa (e-CNPJ).")
            self._pendente = {"pfx": pfx, "senha": senha, "cnpj": r["cnpj"], "validade": r["validade"]}
            existente = dados.empresa(r["cnpj"])
            return {"certificado": r, "existente": bool(existente),
                    "empresa": self._consultar_cnpj(r["cnpj"]) or {"nome": r["nome"]}}
        return self._tratar(f)

    def descartar_cadastro(self):
        self._pendente = None
        return {"ok": True}

    def ler_compartilhado(self, pasta):
        """Etapa 4: se a pasta já tem os dados desta empresa, o computador herda a configuração."""
        if not self._pendente or not pasta:
            return {"ok": True, "empresa": None}
        return {"ok": True, "empresa": dados.perfil_compartilhado(pasta, self._pendente["cnpj"])}

    @staticmethod
    def _consultar_cnpj(cnpj):
        try:
            r = requests.get(f"https://brasilapi.com.br/api/cnpj/v1/{cnpj}", timeout=8)
            if not r.ok:
                return None
            d = r.json()
            mei, simples = d.get("opcao_pelo_mei"), d.get("opcao_pelo_simples")
            fone = digitos(d.get("ddd_telefone_1"))
            return {"nome": str(d.get("razao_social") or "")[:150],
                    "cep": digitos(d.get("cep"))[:8], "logradouro": " ".join(x for x in (str(d.get("descricao_tipo_de_logradouro") or ""), str(d.get("logradouro") or "")) if x).strip()[:255],
                    "numero": str(d.get("numero") or "")[:60], "complemento": str(d.get("complemento") or "")[:150],
                    "bairro": str(d.get("bairro") or "")[:60], "cmun": str(d.get("codigo_municipio_ibge") or "")[:7],
                    "email": str(d.get("email") or "").lower()[:80] if "@" in str(d.get("email") or "") else "",
                    "fone": fone if 10 <= len(fone) <= 11 else "",
                    "apelido": str(d.get("nome_fantasia") or "")[:60],
                    "municipio_ibge": str(d.get("codigo_municipio_ibge") or "")[:7],
                    "municipio": f"{str(d.get('municipio') or '').title()}/{d.get('uf') or ''}".strip("/")[:60],
                    "op_simples": 2 if mei else 3 if simples or simples is None else 1}
        except (requests.RequestException, ValueError, AttributeError):
            return None

    def salvar_empresa(self, p, novo=False, senha=None):
        """Grava a empresa (cadastro ou Ajustes). Com 'novo', guarda o certificado lido na etapa 1."""
        def f():
            cnpj = str(p.get("cnpj", ""))
            if not _cnpj_ok(cnpj):
                raise NfseErro("CNPJ inválido.")
            atual = dados.empresa(cnpj)
            if not atual and not novo:
                raise NfseErro("Empresa não encontrada.")
            perfil = atual or dados.perfil_novo(cnpj, str(p.get("nome", "")))
            anterior_comp = perfil.get("pasta_compartilhada", "")

            for k in CAMPOS_EMPRESA:
                if k in p:
                    perfil[k] = str(p[k] or "").strip()
            if "op_simples" in p:
                if int(p["op_simples"]) not in (1, 2, 3):
                    raise NfseErro("Regime inválido.")
                perfil["op_simples"] = int(p["op_simples"])
            if "aliquota_simples" in p:
                try:
                    perfil["aliquota_simples"] = float(str(p["aliquota_simples"]).replace(",", ".") or 0)
                except ValueError:
                    raise NfseErro("Alíquota inválida.")
            if "servico" in p:
                s = {**perfil["servico"], **{k: v for k, v in p["servico"].items() if k in dados.SERVICO_VAZIO}}
                s["iss_retido"] = bool(s.get("iss_retido"))
                perfil["servico"] = s
                if "servicos" not in p:
                    lista = dados.servicos({**perfil, "servico": {}})
                    perfil["servicos"] = [s] + [x for x in lista if not dados._mesmo(x, s)]
            if "servicos" in p:
                lista = []
                for x in p["servicos"] or []:
                    x = {**dados.SERVICO_VAZIO, **{k: v for k, v in (x or {}).items() if k in dados.SERVICO_VAZIO}}
                    x["iss_retido"] = bool(x.get("iss_retido"))
                    if x["cod_nacional"] and not any(dados._mesmo(x, y) for y in lista):
                        lista.append(x)
                if not lista:
                    raise NfseErro("Deixe pelo menos um serviço cadastrado.")
                perfil["servicos"] = lista
                perfil["servico"] = lista[0]
            if "im" in p:
                perfil["im_na_nota"] = True  # IM nova ou corrigida: volta a ir na nota
            if "urls" in p:
                urls = p["urls"] or None
                if urls and not all(nfse.url_valida(urls.get(a, {}).get(k)) for a in ("producao", "teste") for k in ("sefin", "adn")):
                    raise NfseErro("Use apenas os endereços oficiais da Receita (nfse.gov.br).")
                perfil["urls"] = None if urls == nfse.URLS_PADRAO else urls
            if "pasta_compartilhada" in p:
                perfil["pasta_compartilhada"] = str(p["pasta_compartilhada"] or "")
            if "ambiente" in p:
                if p["ambiente"] not in ("teste", "producao"):
                    raise NfseErro("Ambiente inválido.")
                if p["ambiente"] == "producao" and not perfil.get("teste_ok") and not p.get("sem_teste"):
                    raise NfseErro("Emita uma nota de teste antes de ligar a produção.")
                perfil["ambiente"] = p["ambiente"]

            self._validar_perfil(perfil)

            if novo:
                pend = self._pendente
                if not pend or pend["cnpj"] != cnpj:
                    raise NfseErro("Leia o certificado de novo.")
                perfil["pedir_senha"] = bool(p.get("pedir_senha"))
                perfil["cert_validade"] = pend["validade"]
                dados.guardar_certificado(cnpj, pend["pfx"], None if perfil["pedir_senha"] else pend["senha"])
                self._pendente = None
            elif "pedir_senha" in p and bool(p["pedir_senha"]) != bool(perfil.get("pedir_senha")):
                pfx, _ = dados.ler_certificado(cnpj)
                if p["pedir_senha"]:
                    dados.guardar_certificado(cnpj, pfx, None)
                else:
                    if not senha:
                        raise NfseErro("SENHA")
                    nfse.Certificado(pfx, senha)  # confere a senha antes de guardar
                    dados.guardar_certificado(cnpj, pfx, senha)
                perfil["pedir_senha"] = bool(p["pedir_senha"])

            if perfil.get("pasta_compartilhada") != anterior_comp and atual:
                origem = dados.pasta_dados({**perfil, "pasta_compartilhada": anterior_comp})
                dados.migrar_dados(origem, dados.pasta_dados(perfil))

            self._certs.pop(cnpj, None)
            dados.salvar_empresa(perfil, ativar=bool(novo) or dados.ajustes().get("ativa") is None)
            dados.publicar_perfil(perfil)
            return {"estado": self.estado()}
        return self._tratar(f)

    @staticmethod
    def _validar_perfil(perfil):
        if not perfil.get("nome"):
            raise NfseErro("Informe a razão social.")
        if not re.fullmatch(r"\d{7}", str(perfil.get("municipio_ibge", ""))):
            raise NfseErro("O código do município tem 7 números.")
        if perfil["op_simples"] == 3 and not 0 < float(perfil["aliquota_simples"]) < 100:
            raise NfseErro("Informe a alíquota do Simples (entre 0 e 100%).")
        for s in dados.servicos(perfil) or [perfil["servico"]]:
            if len(digitos(s.get("cod_nacional"))) != 6:
                raise NfseErro("O código do serviço tem 6 números. Ex.: 14.10.01")
            if s.get("cod_municipal") and not re.fullmatch(r"\d{3}", digitos(s["cod_municipal"])):
                raise NfseErro("O código municipal tem 3 números. Ex.: 001")
            if s.get("nbs") and not re.fullmatch(r"\d{9}", digitos(s["nbs"])):
                raise NfseErro("O código NBS tem 9 números.")
        if not perfil.get("descricao_modelo"):
            raise NfseErro("Informe o modelo da descrição.")

    # ---- tabela oficial de serviços e NBS ----

    # ---- logo da empresa ----

    def definir_logo(self, b64):
        def f():
            perfil = self._perfil()
            if not b64:
                if perfil.get("logo"):
                    Path(perfil["logo"]).unlink(missing_ok=True)
                dados.salvar_empresa({"cnpj": perfil["cnpj"], "logo": ""})
                return {"estado": self.estado()}
            conteudo = base64.b64decode(b64, validate=True)
            if len(conteudo) > 2_000_000:
                raise NfseErro("Use uma imagem de até 2 MB.")
            ext = ".png" if conteudo[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg" if conteudo[:3] == b"\xff\xd8\xff" else None
            if not ext:
                raise NfseErro("Use uma imagem PNG ou JPG.")
            pasta = dados.APP / "logos"
            pasta.mkdir(parents=True, exist_ok=True)
            for velho in pasta.glob(f"{perfil['cnpj']}.*"):
                velho.unlink()
            destino = pasta / f"{perfil['cnpj']}{ext}"
            destino.write_bytes(conteudo)
            dados.salvar_empresa({"cnpj": perfil["cnpj"], "logo": str(destino)})
            return {"estado": self.estado()}
        return self._tratar(f)

    # ---- envio por e-mail ----

    def salvar_envio_email(self, cfg, senha=None):
        def f():
            perfil = self._perfil()
            atual = {**ENVIO_PADRAO, **(perfil.get("envio_email") or {})}
            for k in ENVIO_PADRAO:
                if k in (cfg or {}):
                    atual[k] = cfg[k]
            if cfg and cfg.get("provedor") in correio.PROVEDORES:
                atual["servidor"], atual["porta"] = correio.PROVEDORES[cfg["provedor"]]
            try:
                atual["porta"] = int(atual.get("porta") or 587)
                if atual.get("usuario"):
                    correio.emails(atual["usuario"])
                correio.emails(atual.get("responder_para"))
                correio.emails(atual.get("cc_padrao"))
            except (ValueError, correio.ErroEmail) as e:
                raise NfseErro(str(e) if isinstance(e, correio.ErroEmail) else "Porta inválida.")
            atual["auto"] = bool(atual.get("auto"))
            if senha:
                dados.guardar_senha_email(perfil["cnpj"], str(senha).replace(" ", ""))
            dados.salvar_empresa({"cnpj": perfil["cnpj"], "envio_email": atual})
            return {"estado": self.estado()}
        return self._tratar(f)

    def testar_envio_email(self):
        def f():
            perfil = self._perfil()
            try:
                correio.testar({**ENVIO_PADRAO, **(perfil.get("envio_email") or {})}, dados.ler_senha_email(perfil["cnpj"]))
            except correio.ErroEmail as e:
                raise NfseErro(str(e))
            return {}
        return self._tratar(f)

    def _campos_email(self, perfil, item):
        emp = perfil.get("apelido") or perfil["nome"]
        valor = f"R$ {item['valor']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return {"numero": item.get("numero", ""), "empresa": emp, "cliente": item.get("nome", ""), "valor": valor,
                "unidade": f" ({item['unidade']})" if item.get("unidade") else "", "chave": item.get("chave", ""),
                "mes": dados.MESES[int(item["data"][3:5]) - 1] if item.get("data") else ""}

    def dados_envio(self, chave):
        def f():
            perfil = self._perfil()
            item = self._item(perfil, chave)
            cfg = {**ENVIO_PADRAO, **(perfil.get("envio_email") or {})}
            cli = dados.clientes(perfil).get(item.get("cnpj"), {})
            campos = self._campos_email(perfil, item)
            cc = ", ".join(x for x in (cli.get("email_cc", ""), cfg.get("cc_padrao", "")) if x)
            return {"para_nome": item.get("nome", ""), "para": cli.get("email", ""), "cc": cc,
                    "assunto": correio.preencher(cfg["assunto"], campos), "mensagem": correio.preencher(cfg["mensagem"], campos),
                    "nome_remetente": cfg.get("nome_remetente") or campos["empresa"], "responder_para": cfg.get("responder_para", ""),
                    "configurado": bool(cfg.get("usuario")) and dados._cred(perfil["cnpj"], "smtp").exists(),
                    "enviado_em": item.get("enviado_em", ""), "enviado_para": item.get("enviado_para", "")}
        return self._tratar(f)

    def _enviar(self, perfil, item, para, cc, assunto, mensagem, nome_remetente="", responder_para="", senha=None):
        cfg = {**ENVIO_PADRAO, **(perfil.get("envio_email") or {})}
        pdf = self._pdf_atual(perfil, item, senha)
        anexos = [pdf] + ([Path(item["xml"])] if item.get("xml") and Path(item["xml"]).exists() else [])
        try:
            destinos = correio.enviar(cfg, dados.ler_senha_email(perfil["cnpj"]), para, cc, assunto, mensagem, anexos,
                                      perfil.get("logo"), nome_remetente, responder_para)
        except correio.ErroEmail as e:
            raise NfseErro(str(e))
        dados.atualizar_historico(perfil, item["chave"], enviado_em=datetime.now().strftime("%d/%m/%Y %H:%M"),
                                  enviado_para=", ".join(destinos))
        return destinos

    def enviar_email(self, chave, c, senha=None):
        def f():
            perfil = self._perfil()
            item = self._item(perfil, chave)
            destinos = self._enviar(perfil, item, c.get("para"), c.get("cc"), str(c.get("assunto") or "").strip() or "NFS-e",
                                    str(c.get("mensagem") or ""), c.get("nome_remetente", ""), c.get("responder_para", ""), senha)
            cli = dados.clientes(perfil).get(item.get("cnpj"))
            if cli is not None and c.get("lembrar"):  # guarda os destinatários no cadastro do cliente
                dados.salvar_cliente(perfil, item["cnpj"], cli.get("nome", ""), extra={"email": c.get("para", ""), "email_cc": c.get("cc", "")})
            return {"destinos": destinos, "estado": self.estado()}
        return self._tratar(f)

    def _email_automatico(self, perfil, item):
        cfg = {**ENVIO_PADRAO, **(perfil.get("envio_email") or {})}
        if not cfg.get("auto") or item.get("ambiente") != "producao":
            return
        cli = dados.clientes(perfil).get(item.get("cnpj"), {})
        if not cli.get("email"):
            return

        def mandar():
            try:
                campos = self._campos_email(perfil, item)
                cc = ", ".join(x for x in (cli.get("email_cc", ""), cfg.get("cc_padrao", "")) if x)
                self._enviar(perfil, item, cli["email"], cc, correio.preencher(cfg["assunto"], campos),
                             correio.preencher(cfg["mensagem"], campos), cfg.get("nome_remetente") or campos["empresa"])
                self._evento("email", {"ok": True, "numero": item.get("numero"), "para": cli["email"]})
            except Exception as e:
                log.exception("e-mail automático")
                self._evento("email", {"ok": False, "numero": item.get("numero"), "erro": str(e)})
        threading.Thread(target=mandar, daemon=True).start()

    def catalogo_servicos(self, q=""):
        return self._tratar(lambda: {"itens": catalogo.servicos(q)})

    def catalogo_nbs(self, q="", cod_servico=""):
        return self._tratar(lambda: {"itens": catalogo.nbs(q, cod_servico)})

    def catalogo_info(self, cod_servico="", nbs=""):
        def f():
            s = catalogo.servico(cod_servico) if digitos(cod_servico) else None
            return {"servico": s["descricao"] if s else "", "nbs": catalogo.nbs_descricao(nbs), "fonte": catalogo.fonte()}
        return self._tratar(f)

    def trocar_certificado(self, b64, senha):
        def f():
            perfil = self._perfil()
            pfx = base64.b64decode(b64 or "", validate=True)
            cert = nfse.Certificado(pfx, senha or "")
            if cert.cnpj != perfil["cnpj"]:
                raise NfseErro("Este certificado é de outro CNPJ.")
            if cert.vencido:
                raise NfseErro("Este certificado está vencido.")
            dados.guardar_certificado(perfil["cnpj"], pfx, None if perfil.get("pedir_senha") else senha)
            dados.salvar_empresa({"cnpj": perfil["cnpj"], "cert_validade": cert.resumo()["validade"]})
            self._certs.pop(perfil["cnpj"], None)
            return {"estado": self.estado()}
        return self._tratar(f)

    def remover_empresa(self, cnpj):
        if self._envio.locked():
            return {"ok": False, "erro": "Aguarde o envio terminar."}
        if not _cnpj_ok(cnpj):
            return {"ok": False, "erro": "CNPJ inválido."}
        dados.remover_empresa(cnpj)
        self._certs.pop(cnpj, None)
        return {"ok": True, "estado": self.estado()}

    def testar_conexao(self, senha=None, ambiente=None):
        """Testa a conexão no ambiente pedido; sem ambiente, testa teste e produção (só leitura)."""
        def f():
            perfil = self._perfil()
            cert = self._cert(perfil, senha)
            log.info("teste: certificado %s, CNPJ %s, validade %s, %d certificado(s) da cadeia no arquivo",
                     cert.nome, cert.cnpj, cert.resumo()["validade"], len(cert.extras))
            res = {}
            for amb in ([ambiente] if ambiente else ["teste", "producao"]):
                try:
                    res[amb] = {"ok": True, **self._receita(perfil, cert, amb).testar(perfil["municipio_ibge"])}
                except NfseErro as e:
                    res[amb] = {"ok": False, "erro": str(e)}
            alvo = res.get(ambiente or perfil["ambiente"]) or next(iter(res.values()))
            if not alvo["ok"] and not any(r["ok"] for r in res.values()):
                raise NfseErro(alvo["erro"])
            return {"ambientes": res, "municipio_ativo": alvo.get("municipio_ativo", False)}
        return self._tratar(f)

    def registro(self, linhas=40):
        """Últimas linhas do registro, para mostrar dentro do programa."""
        try:
            texto = LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-int(linhas):]
        except FileNotFoundError:
            texto = []
        return {"ok": True, "texto": "\n".join(texto), "versao": __version__}

    # ------------------------------------------------------------ lista de notas
    def importar_pdf(self, b64):
        fd, p = tempfile.mkstemp(suffix=".pdf")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(base64.b64decode(b64 or ""))
            return {"ok": True, **self._preparar(*importar.ler(importar.texto_pdf(p)))}
        except Exception:
            log.exception("importar pdf")
            return {"ok": False, "erro": "Não foi possível ler esse PDF."}
        finally:
            os.remove(p)

    def importar_texto(self, texto):
        return self._tratar(lambda: self._preparar(*importar.ler(str(texto or "")[:200000])))

    def _preparar(self, notas, ignoradas):
        p = dados.empresa()
        cli = dados.clientes(p) if p else {}
        for n in notas:
            n["cnpj"] = digitos(n["cnpj"])
            n["nome"] = cli.get(n["cnpj"], {}).get("nome", "")
            n["descricao"] = n["descricao"] or (dados.descricao(p, n["unidade"], cliente=n["nome"]) if p else n["unidade"])
        return {"notas": notas, "ignoradas": ignoradas}

    def clientes_salvos(self):
        """Clientes já usados, do mais recente para o mais antigo, com as unidades e o último valor."""
        p = dados.empresa()
        if not p:
            return []
        lista = [{"cnpj": d, "nome": c["nome"], "usado": c.get("usado", ""),
                  "unidades": [{"nome": u, **v} for u, v in sorted(c.get("unidades", {}).items())]}
                 for d, c in dados.clientes(p).items()]
        return sorted(lista, key=lambda c: c["usado"], reverse=True)

    def clientes_resumo(self):
        """Todos os clientes já usados, com totais calculados a partir do histórico."""
        p = dados.empresa()
        if not p:
            return []
        salvos = {c["cnpj"]: c for c in self.clientes_salvos()}
        res = {}
        for x in dados.historico(p):
            doc = x.get("cnpj")
            if not doc:
                continue
            r = res.setdefault(doc, {"cnpj": doc, "nome": x.get("nome", ""), "notas": 0, "total": 0.0, "ultima": "", "testes": 0})
            if x.get("ambiente") == "teste":
                r["testes"] += 1
                continue
            if x.get("situacao") == "emitida":
                r["notas"] += 1
                r["total"] += float(x.get("valor") or 0)
                if not r["ultima"]:
                    r["ultima"] = x.get("data", "")
        for doc, c in salvos.items():
            r = res.setdefault(doc, {"cnpj": doc, "nome": c["nome"], "notas": 0, "total": 0.0, "ultima": "", "testes": 0})
            r["nome"] = c["nome"] or r["nome"]
            r["unidades"] = c["unidades"]
        for r in res.values():
            r.setdefault("unidades", [])
        return sorted(res.values(), key=lambda r: (r["notas"], r["total"]), reverse=True)

    def descricao_cliente(self, doc, unidade):
        """Repete a descrição usada da última vez, trocando o mês e o ano pelos atuais."""
        p = dados.empresa()
        if not p:
            return ""
        cad = dados.clientes(p).get(digitos(doc), {})
        anterior = cad.get("unidades", {}).get(unidade or "", {}).get("descricao") or ""
        if not anterior:
            return dados.descricao(p, unidade or "", cliente=cad.get("nome", ""))
        hoje = datetime.now()
        meses = "|".join(dados.MESES)
        novo, n = re.subn(rf"\b({meses})(\s+de\s+)\d{{4}}\b", lambda m: f"{dados.MESES[hoje.month - 1]}{m.group(2)}{hoje.year}",
                          anterior, flags=re.IGNORECASE)
        return novo if n else dados.descricao(p, unidade or "", cliente=cad.get("nome", ""))

    def remover_cliente(self, doc, unidade=None):
        p = dados.empresa()
        if p and len(digitos(doc)) in (11, 14):
            dados.remover_cliente(p, digitos(doc), unidade)
        return {"ok": True}

    def cliente(self, doc):
        p = dados.empresa()
        doc = digitos(doc)
        if not p or len(doc) not in (11, 14):
            return {"ok": False}
        c = dados.clientes(p).get(doc, {})
        return {"ok": True, "cliente": {"cnpj": doc, "nome": c.get("nome", ""),
                                        **{k: c.get(k, "") for k in dados.CAMPOS_CLIENTE}}}

    def salvar_dados_cliente(self, doc, campos):
        """Cadastro do cliente: o que estiver preenchido vai automaticamente nas notas dele."""
        p = dados.empresa()
        doc = digitos(doc)
        if not p or len(doc) not in (11, 14) or not isinstance(campos, dict):
            return {"ok": False, "erro": "Cliente inválido."}
        nome = str(campos.get("nome") or "").strip()
        cep = digitos(campos.get("cep"))
        if cep and len(cep) != 8:
            return {"ok": False, "erro": "O CEP tem 8 números.", "campo": "cep"}
        if campos.get("email") and "@" not in str(campos["email"]):
            return {"ok": False, "erro": "E-mail inválido.", "campo": "email"}
        extra = {k: campos.get(k, "") for k in dados.CAMPOS_CLIENTE if k in campos}
        preenchidos = {}
        if "cep" in extra:
            extra["cep"] = cep
            anterior = dados.clientes(p).get(doc, {}).get("cep", "")
            achado = self._consultar_cep(cep) if cep and cep != anterior else None
            if achado:  # CEP novo: rua, bairro, cidade e UF vêm dele; número e complemento ficam
                preenchidos = {k: v for k, v in achado.items() if v}
                extra.update(preenchidos)
        if "municipio" in extra or "uf" in extra:
            extra["municipio"] = str(extra.get("municipio", "")).strip()[:60]
        dados.salvar_cliente(p, doc, nome, extra=extra)
        return {"ok": True, "preenchidos": preenchidos}

    def consultar_cep(self, cep):
        cep = digitos(cep)
        if len(cep) != 8:
            return {"ok": False, "erro": "O CEP tem 8 números."}
        achado = self._consultar_cep(cep)
        return {"ok": True, "dados": achado} if achado else {"ok": False, "erro": "CEP não encontrado. Preencha o endereço à mão."}

    @staticmethod
    def _consultar_cep(cep):
        """Endereço e código IBGE do município pelo CEP (ViaCEP; BrasilAPI como reserva)."""
        try:
            r = requests.get(f"https://viacep.com.br/ws/{cep}/json/", timeout=8)
            d = r.json() if r.ok else {}
            if d and not d.get("erro"):
                return {"logradouro": str(d.get("logradouro") or "")[:255], "bairro": str(d.get("bairro") or "")[:60],
                        "municipio": str(d.get("localidade") or "")[:60], "uf": str(d.get("uf") or "")[:2],
                        "cmun": digitos(d.get("ibge"))[:7]}
        except (requests.RequestException, ValueError):
            pass
        try:
            r = requests.get(f"https://brasilapi.com.br/api/cep/v1/{cep}", timeout=8)
            d = r.json() if r.ok else {}
            if d.get("city"):
                return {"logradouro": str(d.get("street") or "")[:255], "bairro": str(d.get("neighborhood") or "")[:60],
                        "municipio": str(d.get("city") or "")[:60], "uf": str(d.get("state") or "")[:2]}
        except (requests.RequestException, ValueError):
            pass
        return None

    def completar_cliente_pelo_cnpj(self, doc):
        info = self._consultar_cnpj(digitos(doc)) if len(digitos(doc)) == 14 else None
        if not info:
            return {"ok": False, "erro": "Não foi possível consultar o CNPJ agora."}
        cidade, _, uf = str(info.get("municipio") or "").partition("/")
        info = {**info, "municipio": cidade, "uf": uf}
        return {"ok": True, "dados": {k: info.get(k, "") for k in ("nome", *dados.CAMPOS_CLIENTE) if k not in ("info", "servico")}}

    def renomear_cliente(self, doc, nome):
        p = dados.empresa()
        nome = str(nome or "").strip()[:150]
        if not p or len(digitos(doc)) not in (11, 14) or not nome:
            return {"ok": False, "erro": "Informe o nome."}
        dados.salvar_cliente(p, digitos(doc), nome)
        return {"ok": True}

    def rascunho(self):
        """Lista em espera desta empresa neste computador (sobrevive ao fechar o programa)."""
        p = dados.empresa()
        return dados.rascunho(p) if p else []

    def salvar_rascunho(self, notas):
        p = dados.empresa()
        if not p or not isinstance(notas, list):
            return {"ok": False}
        limpas = []
        for n in notas[:2000]:
            if isinstance(n, dict):
                limpas.append({k: n.get(k) for k in ("cnpj", "nome", "unidade", "descricao", "valor", "sel")})
        try:
            dados.salvar_rascunho(p, limpas)
        except Exception:
            log.exception("salvar lista")
            return {"ok": False}
        return {"ok": True}

    def descricao(self, unidade, cliente=""):
        p = dados.empresa()
        return dados.descricao(p, str(unidade or ""), cliente=str(cliente or "")) if p else ""

    def buscar_nome(self, doc, _perfil=None):
        doc = digitos(doc)
        p = _perfil or dados.empresa()
        try:
            cli = dados.clientes(p) if p else {}
            if doc in cli:
                return cli[doc]["nome"]
            if len(doc) != 14:
                return ""
            info = self._consultar_cnpj(doc) or {}
            nome = info.get("nome", "")
            if nome and p:
                cidade, _, uf = str(info.get("municipio") or "").partition("/")
                info = {**info, "municipio": cidade, "uf": uf}
                extra = {k: info.get(k, "") for k in dados.CAMPOS_CLIENTE if k not in ("info", "servico")}
                dados.salvar_cliente(p, doc, nome, extra=extra)
            return nome
        except Exception:
            log.exception("buscar nome")
            return ""

    # ------------------------------------------------------------ emissão
    def emitir(self, notas, senha=None):
        if not isinstance(notas, list) or not notas:
            return {"ok": False, "erro": "A lista está vazia."}
        if not self._envio.acquire(blocking=False):
            return {"ok": False, "erro": "Já existe um envio em andamento."}
        try:
            perfil = self._perfil()
            cert = self._cert(perfil, senha)
            receita = self._receita(perfil, cert)
        except Exception as e:
            self._envio.release()
            return {"ok": False, "erro": str(e) if isinstance(e, NfseErro) else "Não foi possível iniciar o envio."}
        threading.Thread(target=self._lote, args=(perfil, cert, receita, notas), daemon=True).start()
        return {"ok": True}

    def _lote(self, perfil, cert, receita, notas):
        ok = 0
        bloqueio = None
        try:
            # Checagem rápida antes de enviar: se o acesso estiver bloqueado, nenhuma nota é tentada.
            self._evento("verificando", {})
            try:
                receita.testar(perfil["municipio_ibge"])
            except Bloqueio as e:
                bloqueio = e
            except NfseErro:
                pass
            for i, nota in enumerate(notas):
                if bloqueio:
                    self._evento("progresso", {"i": i, "status": "parado"})
                    continue
                self._evento("progresso", {"i": i, "status": "enviando", "etapa": 0})
                try:
                    item = self._uma(perfil, cert, receita, nota, lambda e, i=i: self._evento("etapa", {"i": i, "etapa": e}))
                    ok += 1
                    self._evento("progresso", {"i": i, "status": "ok", "item": item})
                    self._email_automatico(perfil, item)
                except Bloqueio as e:
                    bloqueio = e
                    self._evento("progresso", {"i": i, "status": "parado"})
                except SemResposta as e:
                    self._evento("progresso", {"i": i, "status": "incerto", "msg": str(e)})
                except (NfseErro, TimeoutError) as e:
                    self._evento("progresso", {"i": i, "status": "erro", "msg": str(e)})
                except Exception:
                    log.exception("emissão")
                    self._evento("progresso", {"i": i, "status": "erro", "msg": "Algo deu errado. Detalhes em notara.log."})
            if ok and not perfil.get("teste_ok"):  # emissão bem-sucedida (teste ou produção) comprova a configuração
                try:
                    dados.salvar_empresa({"cnpj": perfil["cnpj"], "teste_ok": True})
                    dados.publicar_perfil({**perfil, "teste_ok": True})
                except Exception:
                    log.exception("teste_ok")
        finally:
            self._envio.release()
            try:
                est = self.estado()
            except Exception:
                log.exception("estado")
                est = None
            fim = {"ok": ok, "total": len(notas), "estado": est}
            if bloqueio:
                fim["bloqueio"] = {"msg": str(bloqueio), "tipo": bloqueio.tipo, "ambiente": perfil["ambiente"]}
            self._evento("fim", fim)

    def _uma(self, perfil, cert, receita, nota, etapa=lambda e: None):
        doc = digitos(nota.get("cnpj"))
        if len(doc) not in (11, 14):
            raise NfseErro("CNPJ ou CPF do cliente inválido.")
        valor = nfse.valor_decimal(nota.get("valor"))
        if valor <= 0:
            raise NfseErro("Informe o valor.")
        nome = str(nota.get("nome") or "").strip() or self.buscar_nome(doc, perfil)
        if not nome:
            raise NfseErro("Informe o nome do cliente.")
        desc = str(nota.get("descricao") or "").strip()
        if not desc:
            raise NfseErro("Informe a descrição.")
        unidade = str(nota.get("unidade") or "").strip()
        cad = dados.clientes(perfil).get(doc, {})
        info = str(nota.get("info") or cad.get("info") or "").strip()
        srv = self._servico_da_nota(perfil, nota, cad)
        perfil = {**perfil, "servico": srv}
        nota = {"cnpj": doc, "nome": nome, "descricao": desc, "valor": str(valor), "cliente": cad, "info": info,
                "servico": digitos(srv.get("cod_nacional")) + "|" + digitos(srv.get("nbs"))}

        quando = nfse.agora()
        amb = perfil["ambiente"]
        # Valida antes de reservar número: erro de dados não gasta numeração.
        root, _ = nfse.montar_dps({**perfil, "serie": 1}, nota, 1, amb, quando)
        nfse.validar(nfse.assinar(root, cert))

        etapa(1)
        for _ in range(20):
            serie, numero = dados.serie_e_numero(perfil, amb)
            root, ident = nfse.montar_dps({**perfil, "serie": serie}, nota, numero, amb, quando)
            if not receita.dps_existe(ident):
                break
        else:
            raise NfseErro("Não há número livre na série deste computador. Fale com o suporte.")
        xml = nfse.assinar(root, cert)

        etapa(2)
        aviso = None
        try:
            try:
                chave, xml_nfse = receita.emitir(xml)
            except NfseErro as e:
                if "E0120" not in str(e) or isinstance(e, (SemResposta, Bloqueio)) or not perfil.get("im_na_nota", True):
                    raise
                # A IM não está no cadastro nacional (CNC) do município: a Receita só aceita a nota sem ela.
                self._im_fora_da_nota(perfil)
                perfil = {**perfil, "im_na_nota": False}
                root, ident = nfse.montar_dps({**perfil, "serie": serie}, nota, numero, amb, quando)
                xml = nfse.assinar(root, cert)
                chave, xml_nfse = receita.emitir(xml)
                aviso = ("A Inscrição Municipal foi tirada da nota: ela não está no cadastro nacional da NFS-e. "
                         "Ela continua salva em Ajustes.")
        except SemResposta:
            try:
                chave = receita.chave_da_dps(ident)
            except SemResposta:
                chave = None
            if not chave:
                self._registrar_pendente(perfil, nota, unidade, ident, amb)
                raise SemResposta("Sem confirmação da Receita. A nota ficou no Histórico como “A confirmar”: "
                                  "confira por lá antes de enviar de novo.")
            xml_nfse = receita.baixar_xml(chave)
        etapa(3)
        item = self._concluir(perfil, receita, nota, unidade, chave, xml_nfse, amb, quando)
        if aviso:
            item["aviso"] = (item.get("aviso", "") + " " + aviso).strip()
        return item

    @staticmethod
    def _im_fora_da_nota(perfil):
        try:
            dados.salvar_empresa({"cnpj": perfil["cnpj"], "im_na_nota": False})
            dados.publicar_perfil({**perfil, "im_na_nota": False})
        except Exception:
            log.exception("im_na_nota")

    @staticmethod
    def _servico_da_nota(perfil, nota, cad):
        """Serviço escolhido na nota; senão o último usado para o cliente; senão o padrão da empresa."""
        lista = dados.servicos(perfil) or [perfil["servico"]]
        for chave in (nota.get("servico"), cad.get("servico")):
            if chave:
                cod, _, nbs = str(chave).partition("|")
                achado = next((x for x in lista if digitos(x["cod_nacional"]) == digitos(cod)
                               and (not nbs or digitos(x.get("nbs")) == digitos(nbs))), None)
                if achado:
                    return achado
        return lista[0]

    def _registrar_pendente(self, perfil, nota, unidade, ident, amb):
        try:
            dados.registrar(perfil, {"data": datetime.now().strftime("%d/%m/%Y %H:%M"), "numero": "", "chave": "",
                                     "ident": ident, "cnpj": nota["cnpj"], "nome": nota["nome"], "unidade": unidade,
                                     "descricao": nota["descricao"], "valor": float(nota["valor"]), "ambiente": amb,
                                     "pdf": None, "xml": None, "maquina": dados.MAQUINA, "situacao": "pendente"})
        except Exception:
            log.exception("registrar pendente %s", ident)

    def _concluir(self, perfil, receita, nota, unidade, chave, xml_nfse, amb, quando):
        """A nota existe na Receita: registrar primeiro; arquivos são o melhor esforço."""
        numero = nfse.numero_nfse(xml_nfse)
        item = {"data": datetime.now().strftime("%d/%m/%Y %H:%M"), "numero": numero, "chave": chave,
                "cnpj": nota["cnpj"], "nome": nota["nome"], "unidade": unidade, "descricao": nota["descricao"],
                "valor": float(nota["valor"]), "ambiente": amb, "pdf": None, "xml": None,
                "maquina": dados.MAQUINA, "situacao": "emitida"}
        try:
            dados.registrar(perfil, item)
        except Exception:
            log.exception("registrar nota %s", chave)
            item["aviso"] = "Nota emitida, mas não foi possível gravar no histórico."
        try:
            dados.salvar_cliente(perfil, nota["cnpj"], nota["nome"], unidade, nota["valor"], nota["descricao"],
                                 {"servico": nota["servico"]} if nota.get("servico") else None)
        except Exception:
            log.exception("salvar cliente")
        try:
            pasta = dados.pasta_notas(perfil, amb, quando)
            nome = dados.nome_arquivo(numero, unidade or nota["nome"])
            if xml_nfse:
                (pasta / f"{nome}.xml").write_bytes(xml_nfse)
                item["xml"] = str(pasta / f"{nome}.xml")
            conteudo = self._pdf_local(xml_nfse) or receita.danfse(chave)
            if conteudo:
                (pasta / f"{nome}.pdf").write_bytes(conteudo)
                item["pdf"] = str(pasta / f"{nome}.pdf")
            dados.atualizar_historico(perfil, chave, pdf=item["pdf"], xml=item["xml"], pdf_v=PDF_V if item["pdf"] else None)
        except Exception:
            log.exception("arquivos da nota %s", chave)
        if not item["pdf"]:
            self._pdf_depois(perfil, receita, item)
        return item

    @staticmethod
    def _pdf_local(xml_nfse):
        """PDF gerado a partir do XML autorizado: na hora, sem depender do download da Receita."""
        if not xml_nfse:
            return None
        try:
            from . import danfse  # carregado só quando precisa: um problema no PDF nunca impede o programa de abrir
            return danfse.gerar(xml_nfse)
        except Exception:
            log.exception("gerar PDF local")
            return None

    def _pdf_depois(self, perfil, receita, item):
        """A Receita às vezes leva alguns minutos para liberar o PDF: tenta de novo em segundo plano."""
        def tentar():
            import time
            for espera in (30, 90, 240, 600):
                time.sleep(espera)
                try:
                    conteudo = receita.danfse(item["chave"])
                except Exception:
                    conteudo = None
                if conteudo:
                    quando = datetime.strptime(item["data"], "%d/%m/%Y %H:%M")
                    p = dados.pasta_notas(perfil, item["ambiente"], quando) / (dados.nome_arquivo(item["numero"], item["unidade"] or item["nome"]) + ".pdf")
                    p.write_bytes(conteudo)
                    dados.atualizar_historico(perfil, item["chave"], pdf=str(p))
                    self._evento("pdf", {"chave": item["chave"], "pdf": str(p), "estado": self.estado()})
                    return
            log.info("PDF da nota %s ainda indisponível após as tentativas automáticas", item["chave"])
        threading.Thread(target=tentar, daemon=True).start()

    def confirmar_pendente(self, ident, senha=None):
        """Para notas 'A confirmar': pergunta à Receita se a DPS virou nota."""
        def f():
            perfil = self._perfil()
            item = next((x for x in dados.historico(perfil) if x.get("ident") == ident and x.get("situacao") == "pendente"), None)
            if not item:
                raise NfseErro("Nota não encontrada.")
            receita = self._receita(perfil, self._cert(perfil, senha), item["ambiente"])
            try:
                chave = receita.chave_da_dps(ident, tentativas=1)  # None só quando a Receita confirma que não existe
            except SemResposta:
                raise NfseErro("A Receita não respondeu. A nota continua “A confirmar”; tente de novo em alguns minutos.")
            if chave:
                nota = {k: item[k] for k in ("cnpj", "nome", "descricao")} | {"valor": str(item["valor"])}
                self._concluir(perfil, receita, nota, item["unidade"], chave, receita.baixar_xml(chave),
                               item["ambiente"], nfse.agora())
            pasta = dados.pasta_dados(perfil)
            with dados.trava(pasta):  # só sai da lista depois de confirmado (inexistente, ou registrada acima)
                h = dados.ler_json(pasta / "historico.json", [])
                h = [x for x in h if x.get("ident") != ident or x.get("situacao") != "pendente"]
                dados.gravar_json(pasta / "historico.json", h)
            return {"estado": self.estado(), "emitida": bool(chave)}
        return self._tratar(f)

    def _item(self, perfil, chave):
        if not nfse.chave_valida(chave):
            raise NfseErro("Nota inválida.")
        item = next((x for x in dados.historico(perfil) if x.get("chave") == chave), None)
        if not item:
            raise NfseErro("Nota não encontrada.")
        return item

    def _gerar_pdf(self, perfil, item, senha=None):
        """PDF da nota a partir do XML salvo (ou baixado de novo); fica gravado na pasta de notas."""
        chave = item["chave"]
        xml = b""
        arq = self._dentro_das_notas(item.get("xml"))
        if arq and arq.suffix.lower() == ".xml":
            xml = arq.read_bytes()
        conteudo = self._pdf_local(xml)
        if not conteudo:
            receita = self._receita(perfil, self._cert(perfil, senha), item["ambiente"])
            xml = receita.baixar_xml(chave)
            conteudo = self._pdf_local(xml) or receita.danfse(chave)
        if not conteudo:
            raise NfseErro("Não foi possível gerar o PDF desta nota agora.")
        quando = datetime.strptime(item["data"], "%d/%m/%Y %H:%M")
        p = dados.pasta_notas(perfil, item["ambiente"], quando) / (dados.nome_arquivo(item["numero"], item["unidade"] or item["nome"]) + ".pdf")
        p.write_bytes(conteudo)
        dados.atualizar_historico(perfil, chave, pdf=str(p), pdf_v=PDF_V)
        return p

    def _pdf_atual(self, perfil, item, senha=None):
        """PDF no modelo atual: notas antigas são redesenhadas a partir do XML guardado."""
        atual = self._dentro_das_notas(item.get("pdf"))
        if atual and atual.suffix.lower() == ".pdf" and item.get("pdf_v") == PDF_V:
            return atual
        try:
            return self._gerar_pdf(perfil, item, senha)
        except NfseErro:
            if atual and atual.suffix.lower() == ".pdf":
                return atual
            raise

    def salvar_pdfs(self, chaves, senha=None):
        """Salva de uma vez os PDFs das notas escolhidas numa pasta escolhida pelo usuário."""
        def f():
            perfil = self._perfil()
            destino = self.escolher_pasta()
            if not destino:
                return {"cancelado": True}
            import shutil
            ok, falhas = 0, 0
            for chave in chaves or []:
                try:
                    p = self._gerar_pdf(perfil, self._item(perfil, chave), senha)
                    shutil.copy2(p, Path(destino) / p.name)
                    ok += 1
                except NfseErro as e:
                    if str(e) == "SENHA":
                        raise
                    falhas += 1
                except Exception:
                    log.exception("salvar pdf %s", chave)
                    falhas += 1
            self._abrir_direto(Path(destino))
            return {"salvos": ok, "falhas": falhas, "estado": self.estado()}
        return self._tratar(f)

    def baixar_pdf(self, chave, senha=None):
        def f():
            perfil = self._perfil()
            item = self._item(perfil, chave)
            xml = b""
            if item.get("xml") and Path(item["xml"]).exists():
                xml = Path(item["xml"]).read_bytes()
            conteudo = self._pdf_local(xml)
            if not conteudo:
                receita = self._receita(perfil, self._cert(perfil, senha), item["ambiente"])
                xml = receita.baixar_xml(chave)
                conteudo = self._pdf_local(xml) or receita.danfse(chave)
            if not conteudo:
                raise NfseErro("A Receita ainda não liberou o PDF desta nota. O NotF tenta de novo sozinho; "
                               "enquanto isso, a nota pode ser vista em Notas Emitidas no site do Emissor Nacional.")
            quando = datetime.strptime(item["data"], "%d/%m/%Y %H:%M")
            p = dados.pasta_notas(perfil, item["ambiente"], quando) / (dados.nome_arquivo(item["numero"], item["unidade"] or item["nome"]) + ".pdf")
            p.write_bytes(conteudo)
            dados.atualizar_historico(perfil, chave, pdf=str(p))
            self._abrir(p)
            return {"estado": self.estado()}
        return self._tratar(f)

    def cancelar(self, chave, motivo, texto, senha=None):
        def f():
            if str(motivo) not in MOTIVOS:
                raise NfseErro("Escolha o motivo.")
            texto_ok = str(texto or "").strip()
            if not 15 <= len(texto_ok) <= 255:
                raise NfseErro("Explique o motivo em 15 a 255 caracteres.")
            perfil = self._perfil()
            item = self._item(perfil, chave)
            if item.get("situacao") == "cancelada":
                raise NfseErro("Esta nota já está cancelada.")
            cert = self._cert(perfil, senha)
            xml = nfse.assinar(nfse.montar_cancelamento(chave, perfil["cnpj"], item["ambiente"], int(motivo), texto_ok), cert)
            nfse.validar(xml, "pedRegEvento_v1.01.xsd")
            self._receita(perfil, cert, item["ambiente"]).cancelar(chave, xml)
            dados.atualizar_historico(perfil, chave, situacao="cancelada")
            return {"estado": self.estado()}
        return self._tratar(f)

    # ------------------------------------------------------------ backup
    def exportar_backup(self):
        def f():
            from . import backup
            perfil = self._perfil()
            arq = backup.exportar(perfil, Path(perfil.get("pasta_notas") or Path.home() / "Documents" / "NotF") / "Backups")
            self._mostrar_arquivo(arq)
            return {"arquivo": arq.name}
        return self._tratar(f)

    def importar_backup(self, b64):
        def f():
            from . import backup
            if self._envio.locked():
                raise NfseErro("Aguarde a emissão terminar.")
            try:
                r = backup.importar(self._perfil(), base64.b64decode(b64))
            except ValueError as e:
                raise NfseErro(str(e))
            return {**r, "estado": self.estado()}
        return self._tratar(f)

    # ------------------------------------------------------------ arquivos
    def escolher_pasta(self):
        import webview
        try:
            tipo = getattr(webview, "FOLDER_DIALOG", None) or webview.FileDialog.FOLDER
            r = self._janela.create_file_dialog(tipo)
            return r[0] if r else ""
        except Exception:
            log.exception("escolher pasta")
            return ""

    def abrir_nota(self, chave, tipo):
        """Abre o PDF, o XML ou a pasta de uma nota do histórico."""
        p = dados.empresa()
        if not p or not nfse.chave_valida(chave) or tipo not in ("pdf", "xml", "pasta"):
            return {"ok": False}
        item = next((x for x in dados.historico(p) if x.get("chave") == chave), None)
        if not item:
            return {"ok": False}
        if tipo == "pdf" and item.get("xml") and Path(item["xml"]).exists():
            try:
                return self._abrir(self._pdf_atual(p, item))
            except Exception:
                log.exception("refazer PDF")
        alvo = item.get(tipo) if tipo != "pasta" else str(Path(item.get("pdf") or item.get("xml") or "").parent)
        return self._abrir(alvo)

    def enviar_nota(self, chave, via, senha=None):
        """Abre o e-mail ou o WhatsApp com a mensagem pronta; o PDF fica selecionado na pasta para anexar."""
        def f():
            import webbrowser
            from urllib.parse import quote
            perfil = self._perfil()
            item = self._item(perfil, chave)
            pdf = self._pdf_atual(perfil, item, senha)
            cli = dados.clientes(perfil).get(item.get("cnpj"), {})
            emp = perfil.get("apelido") or perfil["nome"]
            assunto = f"NFS-e nº {item['numero']} - {emp}"
            texto = (f"Olá! Segue a NFS-e nº {item['numero']} de {emp}"
                     + (f" ({item['unidade']})" if item.get("unidade") else "")
                     + f", no valor de R$ {item['valor']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                     + f".\n\nChave de acesso: {chave}\nConsulta: https://www.nfse.gov.br/ConsultaPublica/?tpc=1&chave={chave}")
            if via == "email":
                email = str(cli.get("email") or "").strip()
                webbrowser.open(f"mailto:{quote(email)}?subject={quote(assunto)}&body={quote(texto)}")
            elif via == "whatsapp":
                fone = digitos(cli.get("fone"))
                if fone and not fone.startswith("55"):
                    fone = "55" + fone
                webbrowser.open(f"https://wa.me/{fone}?text={quote(texto)}")
            else:
                raise NfseErro("Opção inválida.")
            self._mostrar_arquivo(pdf)
            return {"estado": self.estado()}
        return self._tratar(f)

    def enviar_varias(self, chaves, senha=None):
        """Junta os PDFs escolhidos numa pasta e abre o e-mail com a lista das notas, pronto para anexar."""
        def f():
            import shutil
            import webbrowser
            from urllib.parse import quote
            perfil = self._perfil()
            itens = [self._item(perfil, c) for c in chaves or []]
            if not itens:
                raise NfseErro("Selecione ao menos uma nota.")
            pasta = Path(perfil.get("pasta_notas") or Path.home() / "Documents" / "NotF") / "Para enviar" / datetime.now().strftime("%Y-%m-%d %H-%M")
            pasta.mkdir(parents=True, exist_ok=True)
            for it in itens:
                pdf = self._pdf_atual(perfil, it, senha)
                shutil.copy2(pdf, pasta / pdf.name)
            emp = perfil.get("apelido") or perfil["nome"]
            docs = {it.get("cnpj") for it in itens}
            cli = dados.clientes(perfil).get(next(iter(docs)), {}) if len(docs) == 1 else {}
            linhas = "\n".join(f"- NFS-e nº {it['numero']}" + (f" ({it['unidade']})" if it.get("unidade") else "")
                               + f": R$ {it['valor']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") for it in itens)
            texto = f"Olá! Seguem em anexo as notas fiscais de serviço de {emp}:\n\n{linhas}\n\nQualquer dúvida, estamos à disposição."
            para = ",".join(x for x in (cli.get("email", ""), cli.get("email_cc", "")) if x)
            webbrowser.open(f"mailto:{quote(para, safe='@,')}?subject={quote(f'Notas fiscais - {emp}')}&body={quote(texto)}")
            self._abrir_direto(pasta)
            return {"total": len(itens), "estado": self.estado()}
        return self._tratar(f)

    def imprimir_varias(self, chaves, senha=None):
        def f():
            perfil = self._perfil()
            n = 0
            for c in chaves or []:
                pdf = self._pdf_atual(perfil, self._item(perfil, c), senha)
                if sys.platform == "win32":
                    os.startfile(str(pdf), "print")
                n += 1
            return {"total": n, "estado": self.estado()}
        return self._tratar(f)

    def imprimir_nota(self, chave, senha=None):
        def f():
            perfil = self._perfil()
            item = self._item(perfil, chave)
            pdf = self._pdf_atual(perfil, item, senha)
            if sys.platform == "win32":
                os.startfile(str(pdf), "print")
            else:
                self._abrir_direto(pdf)
            return {"estado": self.estado()}
        return self._tratar(f)

    @staticmethod
    def _mostrar_arquivo(p):
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(p)])

    def consultar_no_site(self):
        """Abre a consulta pública oficial da NFS-e (endereço fixo)."""
        import webbrowser
        webbrowser.open("https://www.nfse.gov.br/ConsultaPublica")
        return {"ok": True}

    def abrir_pasta_notas(self):
        p = dados.empresa()
        return self._abrir(dados.pasta_notas(p, p["ambiente"])) if p else {"ok": False}

    def abrir_log(self):
        return self._abrir_direto(LOG) if LOG.exists() else {"ok": False}

    def _abrir(self, caminho):
        alvo = self._dentro_das_notas(caminho)
        if not alvo:
            return {"ok": False, "erro": "Arquivo não encontrado."}
        return self._abrir_direto(alvo)

    @staticmethod
    def _abrir_direto(alvo):
        if sys.platform == "win32":
            os.startfile(str(alvo))
        else:
            subprocess.Popen(["xdg-open", str(alvo)])
        return {"ok": True}

    # ------------------------------------------------------------ atualização
    def verificar_atualizacao(self):
        try:
            r = requests.get(f"https://api.github.com/repos/{REPO}/releases/latest", timeout=6)
            if r.status_code == 404:
                return {"erro": "Página de atualizações indisponível. Baixe a versão nova com quem distribui o NotF."}
            if not r.ok:
                return None
            d = r.json()
            versao = str(d.get("tag_name", "")).lstrip("v")
            if _versao(versao) <= _versao(__version__):
                return {"atual": True}
            nomes = (f"NotF-{versao}-Instalar.exe", f"Notara-{versao}-Instalar.exe")
            asset = next((a for n in nomes for a in d.get("assets", []) if a.get("name") == n), None)
            digest = str((asset or {}).get("digest") or "")
            if not asset or not digest.startswith("sha256:"):
                return {"atual": True}
            self._update = {"versao": versao, "url": asset["browser_download_url"], "sha256": digest[7:].lower(),
                            "tamanho": int(asset.get("size") or 0)}
            return {"versao": versao}
        except (requests.RequestException, ValueError, KeyError, TypeError):
            return None

    def instalar_atualizacao(self):
        u = self._update
        if not u:
            return {"ok": False, "erro": "Nenhuma atualização disponível."}
        if self._envio.locked():
            return {"ok": False, "erro": "Aguarde o envio terminar."}
        if not any(u["url"].lower().startswith(f"https://github.com/{r}/releases/download/") for r in REPOS_OK):
            return {"ok": False, "erro": "Endereço de atualização inválido."}
        try:
            fd, destino = tempfile.mkstemp(prefix="NotF-", suffix=".exe")
            h = hashlib.sha256()
            with os.fdopen(fd, "wb") as f, requests.get(u["url"], stream=True, timeout=60) as r:
                r.raise_for_status()
                for parte in r.iter_content(1 << 16):
                    f.write(parte)
                    h.update(parte)
            if h.hexdigest() != u["sha256"] or (u["tamanho"] and os.path.getsize(destino) != u["tamanho"]):
                os.remove(destino)
                return {"ok": False, "erro": "A atualização baixada não confere. Tente mais tarde."}
            # Atualização sem janela: o Notara mostra o andamento e o instalador roda em silêncio.
            subprocess.Popen([destino, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], close_fds=True)
            threading.Timer(1.5, lambda: os._exit(0)).start()
            return {"ok": True}
        except (requests.RequestException, OSError):
            log.exception("atualização")
            return {"ok": False, "erro": "Não foi possível baixar a atualização. Tente mais tarde."}
