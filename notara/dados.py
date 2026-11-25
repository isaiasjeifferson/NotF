"""Armazenamento local: empresas, certificados protegidos, clientes, histórico e numeração."""
import base64
import json
import os
import re
import socket
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

APP = Path(os.environ.get("APPDATA") or Path.home() / ".config") / "Notara"
ENTROPIA = b"Notara/credenciais/v1"


def _id_maquina():
    """Identificador estável deste computador (nome + código único salvo na primeira execução)."""
    APP.mkdir(parents=True, exist_ok=True)
    p = APP / "computador.id"
    try:
        uid = p.read_text().strip()
    except FileNotFoundError:
        uid = uuid.uuid4().hex[:8]
        p.write_text(uid)
    nome = re.sub(r"[^\w-]", "", socket.gethostname())[:30] or "PC"
    return f"{nome}-{uid}"


MAQUINA = _id_maquina()
MESES = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto",
         "Setembro", "Outubro", "Novembro", "Dezembro"]

SERVICO_VAZIO = {"cod_nacional": "", "cod_municipal": "", "nbs": "", "nome": "", "iss_retido": False}


def perfil_novo(cnpj, nome):
    return {
        "cnpj": cnpj, "nome": nome, "municipio_ibge": "", "municipio": "",
        "op_simples": 3, "aliquota_simples": 0.0,
        "servico": dict(SERVICO_VAZIO),
        "apelido": "", "im": "", "ie": "", "fone": "", "email": "", "descricao_modelo": "Prestação de serviço - {mes} de {ano} - {unidade}",
        "ambiente": "teste", "teste_ok": False, "pedir_senha": False, "cert_validade": "",
        "urls": None, "pasta_notas": str(Path.home() / "Documents" / "Notara"),
    }


# ---------------------------------------------------------------- proteção (Windows DPAPI)
def _dpapi(dados: bytes, proteger: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(dados, len(dados))
    ebuf = ctypes.create_string_buffer(ENTROPIA, len(ENTROPIA))
    ent, sai, extra = Blob(len(dados), buf), Blob(), Blob(len(ENTROPIA), ebuf)
    fn = ctypes.windll.crypt32.CryptProtectData if proteger else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(ent), None, ctypes.byref(extra), None, None, 0x1, ctypes.byref(sai)):  # 0x1: sem janela
        raise OSError("Não foi possível acessar o cofre do Windows.")
    try:
        return ctypes.string_at(sai.pbData, sai.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(sai.pbData)


def proteger(dados: bytes) -> bytes:
    return b"W1" + _dpapi(dados, True) if sys.platform == "win32" else b"X1" + base64.b64encode(dados)


def desproteger(dados: bytes) -> bytes:
    if dados[:2] == b"W1":
        return _dpapi(dados[2:], False)
    if dados[:2] == b"X1" and sys.platform != "win32":  # apenas para testes fora do Windows
        return base64.b64decode(dados[2:])
    raise OSError("Credencial salva em outro computador. Importe o certificado de novo.")


# ---------------------------------------------------------------- JSON com trava
def ler_json(p: Path, padrao):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return padrao


def gravar_json(p: Path, dados):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    for tentativa in range(10):  # OneDrive e antivírus seguram o arquivo por instantes
        try:
            os.replace(tmp, p)
            return
        except PermissionError:
            if tentativa == 9:
                raise
            time.sleep(0.3)


@contextmanager
def trava(pasta: Path, tempo=20):
    """Trava por arquivo; funciona em pasta de rede. Só quem criou a trava pode removê-la."""
    pasta.mkdir(parents=True, exist_ok=True)
    lock = pasta / ".trava"
    meu = f"{MAQUINA}:{uuid.uuid4().hex}:{time.time():.0f}"
    fim = time.time() + tempo
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, meu.encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                criada = float(lock.read_text().rsplit(":", 1)[-1])
            except (OSError, ValueError):
                criada = time.time()
            if time.time() - criada > 120:  # trava abandonada: renomeia antes de apagar (atômico)
                try:
                    lixo = lock.with_name(f".trava.{uuid.uuid4().hex}")
                    os.replace(lock, lixo)
                    lixo.unlink()
                except OSError:
                    pass
                continue
            if time.time() > fim:
                raise TimeoutError("Outro computador está gravando os dados. Tente de novo em instantes.")
            time.sleep(0.25)
    try:
        yield
    finally:
        try:
            if lock.read_text() == meu:
                lock.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------- ajustes gerais
def ajustes():
    APP.mkdir(parents=True, exist_ok=True)
    return ler_json(APP / "ajustes.json", {"empresas": {}, "ativa": None})


def salvar_ajustes(a):
    gravar_json(APP / "ajustes.json", a)


def empresa(cnpj=None):
    a = ajustes()
    cnpj = cnpj or a.get("ativa")
    return a["empresas"].get(cnpj) if cnpj else None


def salvar_empresa(perfil, ativar=False):
    a = ajustes()
    atual = a["empresas"].get(perfil["cnpj"], {})
    atual.update(perfil)
    a["empresas"][perfil["cnpj"]] = atual
    if ativar:
        a["ativa"] = perfil["cnpj"]
    salvar_ajustes(a)
    return atual


def remover_empresa(cnpj):
    if not re.fullmatch(r"\d{14}", str(cnpj)):
        return
    a = ajustes()
    a["empresas"].pop(cnpj, None)
    if a.get("ativa") == cnpj:
        a["ativa"] = next(iter(a["empresas"]), None)
    salvar_ajustes(a)
    for f in (APP / "credenciais").glob(f"{cnpj}.*"):
        f.unlink()


# ---------------------------------------------------------------- certificado e senha
def _cred(cnpj, tipo):
    return APP / "credenciais" / f"{cnpj}.{tipo}"


def guardar_certificado(cnpj, pfx: bytes, senha: str | None):
    _cred(cnpj, "pfx").parent.mkdir(parents=True, exist_ok=True)
    _cred(cnpj, "pfx").write_bytes(proteger(pfx))
    if senha is None:
        _cred(cnpj, "senha").unlink(missing_ok=True)
    else:
        _cred(cnpj, "senha").write_bytes(proteger(senha.encode()))


def ler_certificado(cnpj):
    p = _cred(cnpj, "pfx")
    if not p.exists():
        return None, None
    s = _cred(cnpj, "senha")
    return desproteger(p.read_bytes()), (desproteger(s.read_bytes()).decode() if s.exists() else None)


# ---------------------------------------------------------------- dados da empresa (local ou compartilhado)
def pasta_dados(perfil):
    base = perfil.get("pasta_compartilhada") or str(APP / "empresas")
    return Path(base) / perfil["cnpj"]


COMPARTILHADO = ("servico", "descricao_modelo", "op_simples", "aliquota_simples", "teste_ok",
                 "municipio_ibge", "municipio", "nome", "apelido")


def publicar_perfil(perfil):
    """Na pasta compartilhada, outros computadores herdam a configuração da empresa."""
    if perfil.get("pasta_compartilhada"):
        pasta = pasta_dados(perfil)
        with trava(pasta):
            gravar_json(pasta / "empresa.json", {k: perfil.get(k) for k in COMPARTILHADO})


def perfil_compartilhado(pasta_compartilhada, cnpj):
    return ler_json(Path(pasta_compartilhada) / cnpj / "empresa.json", None)


def migrar_dados(origem: Path, destino: Path):
    """Ao ligar ou trocar a pasta compartilhada, leva histórico, clientes e numeração sem perder nada."""
    if origem.resolve() == destino.resolve():
        return
    with trava(destino):
        h_dest = ler_json(destino / "historico.json", [])
        chaves = {x.get("chave") for x in h_dest}
        h = h_dest + [x for x in ler_json(origem / "historico.json", []) if x.get("chave") not in chaves]
        h.sort(key=lambda x: datetime.strptime(x.get("data", "01/01/2000 00:00"), "%d/%m/%Y %H:%M"), reverse=True)
        gravar_json(destino / "historico.json", h)
        gravar_json(destino / "clientes.json", {**ler_json(origem / "clientes.json", {}),
                                                **ler_json(destino / "clientes.json", {})})
        n_o = ler_json(origem / "numeracao.json", {"series": {}, "proximo": {}})
        n_d = ler_json(destino / "numeracao.json", {"series": {}, "proximo": {}})
        for maq, serie in n_o["series"].items():
            n_d["series"].setdefault(maq, serie)
        for k, v in n_o["proximo"].items():
            n_d["proximo"][k] = max(int(v), int(n_d["proximo"].get(k, 1)))
        gravar_json(destino / "numeracao.json", n_d)


def clientes(perfil):
    """{documento: {"nome", "unidades": {unidade: {"valor", "descricao"}}, "usado"}}. Aceita o formato antigo."""
    bruto = ler_json(pasta_dados(perfil) / "clientes.json", {})
    return {doc: (c if isinstance(c, dict) else {"nome": c, "unidades": {}, "usado": ""}) for doc, c in bruto.items()}


CAMPOS_CLIENTE = ("im", "email", "fone", "cep", "logradouro", "numero", "complemento", "bairro",
                  "cmun", "municipio", "info")


def salvar_cliente(perfil, doc, nome, unidade=None, valor=None, descricao=None, extra=None):
    pasta = pasta_dados(perfil)
    with trava(pasta):
        todos = {d: (c if isinstance(c, dict) else {"nome": c, "unidades": {}, "usado": ""})
                 for d, c in ler_json(pasta / "clientes.json", {}).items()}
        c = todos.setdefault(doc, {"nome": nome, "unidades": {}, "usado": ""})
        c["nome"] = nome or c["nome"]
        for k, v in (extra or {}).items():
            if k in CAMPOS_CLIENTE:
                c[k] = str(v or "").strip()
        if valor is not None:
            c["usado"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            c["unidades"][unidade or ""] = {"valor": float(valor), "descricao": descricao or ""}
        gravar_json(pasta / "clientes.json", todos)


def remover_cliente(perfil, doc, unidade=None):
    pasta = pasta_dados(perfil)
    with trava(pasta):
        todos = ler_json(pasta / "clientes.json", {})
        if unidade is None:
            todos.pop(doc, None)
        elif isinstance(todos.get(doc), dict):
            todos[doc]["unidades"].pop(unidade, None)
        gravar_json(pasta / "clientes.json", todos)


def rascunho(perfil):
    return ler_json(pasta_dados(perfil) / f"lista-{MAQUINA}.json", [])


def salvar_rascunho(perfil, notas):
    gravar_json(pasta_dados(perfil) / f"lista-{MAQUINA}.json", notas)


def historico(perfil):
    return ler_json(pasta_dados(perfil) / "historico.json", [])


def registrar(perfil, item):
    pasta = pasta_dados(perfil)
    with trava(pasta):
        h = ler_json(pasta / "historico.json", [])
        h.insert(0, item)
        gravar_json(pasta / "historico.json", h)


def atualizar_historico(perfil, chave, **campos):
    pasta = pasta_dados(perfil)
    with trava(pasta):
        h = ler_json(pasta / "historico.json", [])
        for item in h:
            if item.get("chave") == chave:
                item.update(campos)
        gravar_json(pasta / "historico.json", h)


def serie_e_numero(perfil, ambiente):
    """Cada computador usa uma série própria; o número nunca se repete. Teste e produção contam à parte."""
    pasta = pasta_dados(perfil)
    with trava(pasta):
        n = ler_json(pasta / "numeracao.json", {"series": {}, "proximo": {}})
        series = n["series"]
        if MAQUINA not in series:
            usadas = set(series.values())
            series[MAQUINA] = next(s for s in range(1, 50000) if s not in usadas)
        serie = series[MAQUINA]
        k = f"{ambiente}:{serie}"
        num = int(n["proximo"].get(k, 1))
        n["proximo"][k] = num + 1
        gravar_json(pasta / "numeracao.json", n)
        return serie, num


def pasta_notas(perfil, ambiente, quando=None):
    hoje = quando or datetime.now()
    nome_empresa = re.sub(r'[\\/:*?"<>|.]+', " ", perfil["nome"]).strip()[:60] or perfil["cnpj"]
    p = Path(perfil["pasta_notas"]) / nome_empresa / f"{hoje:%Y-%m} {MESES[hoje.month - 1]}"
    if ambiente != "producao":
        p = p / "Testes"
    p.mkdir(parents=True, exist_ok=True)
    return p


def nome_arquivo(numero, rotulo):
    limpo = re.sub(r'[\\/:*?"<>|]+', " ", rotulo or "").strip()[:60]
    return f"NFS-e {numero} - {limpo}" if numero else f"NFS-e - {limpo}"


def descricao(perfil, unidade, quando=None):
    q = quando or datetime.now()
    texto = (perfil["descricao_modelo"].replace("{mes}", MESES[q.month - 1]).replace("{ano}", str(q.year))
             .replace("{unidade}", (unidade or "").strip()))
    return re.sub(r"(\s*-\s*)+$", "", texto).strip()
