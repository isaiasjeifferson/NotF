"""Cópia de segurança de uma empresa: ajustes, clientes, histórico, numeração, logo e XML das notas.
O certificado e as senhas não vão: ficam protegidos pelo Windows de cada computador."""
import io
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path

from . import __version__, dados

FORA = {"pasta_notas", "pasta_compartilhada", "logo", "cert_validade", "pedir_senha"}


def exportar(perfil, destino: Path) -> Path:
    pasta = dados.pasta_dados(perfil)
    nome = re.sub(r"[^\w-]+", "-", perfil.get("apelido") or perfil["nome"]).strip("-")[:40]
    arq = destino / f"NotF-backup-{nome}-{datetime.now():%Y-%m-%d-%H%M}.zip"
    destino.mkdir(parents=True, exist_ok=True)
    historico = dados.ler_json(pasta / "historico.json", [])
    with zipfile.ZipFile(arq, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("notf-backup.json", json.dumps({"versao": __version__, "criado": datetime.now().isoformat(timespec="seconds"),
                                                   "maquina": dados.MAQUINA, "cnpj": perfil["cnpj"]}, ensure_ascii=False))
        z.writestr("perfil.json", json.dumps({k: v for k, v in perfil.items() if k not in FORA}, ensure_ascii=False, indent=1))
        for n in ("historico.json", "clientes.json", "numeracao.json"):
            if (pasta / n).exists():
                z.write(pasta / n, n)
        if perfil.get("logo") and Path(perfil["logo"]).exists():
            z.write(perfil["logo"], "logo" + Path(perfil["logo"]).suffix)
        for it in historico:
            x = it.get("xml")
            if it.get("chave") and x and Path(x).exists():
                z.write(x, f"notas/{it['chave']}.xml")
    return arq


def importar(perfil, conteudo: bytes):
    """Junta o backup à empresa aberta (mesmo CNPJ). Nada do que já existe aqui é apagado."""
    try:
        z = zipfile.ZipFile(io.BytesIO(conteudo))
        info = json.loads(z.read("notf-backup.json"))
    except (zipfile.BadZipFile, KeyError, ValueError):
        raise ValueError("Este arquivo não é um backup do NotF.")
    if info.get("cnpj") != perfil["cnpj"]:
        raise ValueError(f"Este backup é de outro CNPJ ({info.get('cnpj')}). Abra essa empresa antes de restaurar.")
    nomes = set(z.namelist())
    origem = dados.APP / "restaurar" / perfil["cnpj"]
    origem.mkdir(parents=True, exist_ok=True)
    hist = json.loads(z.read("historico.json")) if "historico.json" in nomes else []
    for it in hist:  # os XML vão para a pasta de notas deste computador
        it.pop("pdf", None), it.pop("pdf_v", None)
        if f"notas/{it.get('chave')}.xml" in nomes:
            quando = datetime.strptime(it["data"], "%d/%m/%Y %H:%M")
            p = dados.pasta_notas(perfil, it.get("ambiente", "producao"), quando) / (
                dados.nome_arquivo(it.get("numero", ""), it.get("unidade") or it.get("nome", "")) + ".xml")
            if not p.exists():
                p.write_bytes(z.read(f"notas/{it['chave']}.xml"))
            it["xml"] = str(p)
        else:
            it.pop("xml", None)
    dados.gravar_json(origem / "historico.json", hist)
    for n in ("clientes.json", "numeracao.json"):
        dados.gravar_json(origem / n, json.loads(z.read(n)) if n in nomes else ({} if n == "clientes.json" else {"series": {}, "proximo": {}}))
    antes = len(dados.historico(perfil))
    dados.migrar_dados(origem, dados.pasta_dados(perfil))
    # preferências da empresa que ainda não foram mexidas aqui
    salvo = json.loads(z.read("perfil.json")) if "perfil.json" in nomes else {}
    atual = dados.empresa() or perfil
    novos = {k: v for k, v in salvo.items() if k not in FORA and k != "cnpj" and not atual.get(k)}
    logo = next((n for n in nomes if n.startswith("logo.")), None)
    if logo and not atual.get("logo"):
        destino = dados.APP / "logos" / f"{perfil['cnpj']}{Path(logo).suffix}"
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(z.read(logo))
        novos["logo"] = str(destino)
    if novos:
        dados.salvar_empresa({"cnpj": perfil["cnpj"], **novos})
    return {"notas": len(dados.historico(perfil)) - antes, "clientes": len(json.loads(z.read("clientes.json"))) if "clientes.json" in nomes else 0}
