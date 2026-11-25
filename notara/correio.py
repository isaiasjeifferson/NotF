"""Envio da nota por e-mail pela conta da própria empresa (SMTP), com PDF e XML anexados."""
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

PROVEDORES = {
    "gmail": ("smtp.gmail.com", 587),
    "outlook": ("smtp.office365.com", 587),
    "yahoo": ("smtp.mail.yahoo.com", 587),
}
ASSUNTO = "NFS-e nº {numero} - {empresa}"
MENSAGEM = ("Olá,\n\nSegue em anexo a NFS-e nº {numero}{unidade}, no valor de {valor}.\n\n"
            "Chave de acesso: {chave}\nConsulta: https://www.nfse.gov.br/ConsultaPublica/?tpc=1&chave={chave}\n\n"
            "Qualquer dúvida, estamos à disposição.\n{empresa}")


class ErroEmail(Exception):
    pass


def emails(texto):
    """Aceita vários e-mails separados por vírgula, ponto e vírgula ou espaço."""
    lista = [e.strip() for e in re.split(r"[,;\s]+", str(texto or "")) if e.strip()]
    for e in lista:
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e):
            raise ErroEmail(f"E-mail inválido: {e}")
    return lista


def preencher(modelo, campos):
    try:
        return str(modelo).format(**campos)
    except (KeyError, IndexError, ValueError):
        return str(modelo)


def _conectar(cfg, senha):
    servidor, porta = cfg.get("servidor", ""), int(cfg.get("porta") or 587)
    if not servidor or not cfg.get("usuario") or not senha:
        raise ErroEmail("Configure o envio de e-mail em Ajustes › E-mail.")
    try:
        ctx = ssl.create_default_context()
        if porta == 465:
            s = smtplib.SMTP_SSL(servidor, porta, timeout=20, context=ctx)
        else:
            s = smtplib.SMTP(servidor, porta, timeout=20)
            s.starttls(context=ctx)
        s.login(cfg["usuario"], senha)
        return s
    except smtplib.SMTPAuthenticationError:
        raise ErroEmail("O provedor recusou o usuário ou a senha. No Gmail e no Outlook, use uma senha de app.")
    except (OSError, smtplib.SMTPException) as e:
        raise ErroEmail(f"Não foi possível conectar ao servidor de e-mail ({e.__class__.__name__}).")


def testar(cfg, senha):
    _conectar(cfg, senha).quit()


def enviar(cfg, senha, para, cc, assunto, texto, anexos, logo=None, nome_remetente="", responder_para=""):
    para, cc = emails(para), emails(cc)
    if not para:
        raise ErroEmail("Informe o e-mail de quem vai receber.")
    msg = EmailMessage()
    msg["From"] = formataddr((nome_remetente or cfg.get("nome_remetente") or "", cfg["usuario"]))
    msg["To"] = ", ".join(para)
    if cc:
        msg["Cc"] = ", ".join(cc)
    resp = responder_para or cfg.get("responder_para")
    if resp:
        msg["Reply-To"] = ", ".join(emails(resp))
    msg["Subject"] = assunto
    msg.set_content(texto)
    if logo and Path(logo).exists():
        cid = make_msgid()
        corpo = "".join(f"<p>{l}</p>" if l else "<br>" for l in (x.replace("&", "&amp;").replace("<", "&lt;") for x in texto.split("\n")))
        msg.add_alternative(f'<div style="font-family:Arial,sans-serif;font-size:14px">{corpo}'
                            f'<p><img src="cid:{cid[1:-1]}" alt="" style="max-height:60px"></p></div>', subtype="html")
        msg.get_payload()[1].add_related(Path(logo).read_bytes(), "image", "png", cid=cid)
    for a in anexos:
        a = Path(a)
        tipo = ("application", "pdf") if a.suffix.lower() == ".pdf" else ("application", "xml")
        msg.add_attachment(a.read_bytes(), maintype=tipo[0], subtype=tipo[1], filename=a.name)
    s = _conectar(cfg, senha)
    try:
        s.send_message(msg)
    finally:
        try:
            s.quit()
        except Exception:
            pass
    return para + cc
