# Notara

Emissor de NFS-e para o Sistema Nacional da Receita. Feito para emitir várias notas de uma vez, com clientes salvos.

## Para quem usa

1. Baixe `Notara-X.Y.Z-Instalar.exe` em **Releases** e abra. Não precisa ser administrador.
2. Se aparecer “O Windows protegeu o computador”, clique em **Mais informações › Executar assim mesmo**.
3. Tenha em mãos o certificado A1 da empresa (.pfx), a senha e o código do serviço (está na última nota ou com a contabilidade).
4. O Notara pede quatro coisas, uma por tela: certificado, dados da empresa, serviço e conexão.
5. Emita uma nota em **Teste** (sem validade fiscal). Depois clique em **Ligar produção**.
6. Em outro computador da mesma empresa: instale, importe o certificado e escolha a mesma pasta compartilhada.
7. As atualizações aparecem no próprio programa. As notas ficam em `Documentos\Notara` e não são apagadas ao desinstalar.

### No dia a dia

- **Clientes salvos:** busque pelo nome (`Ctrl+K`), escolha a unidade e informe o valor. Ou adicione todas as unidades com o último valor.
- **PDF ou lista:** arraste o PDF com a lista ou cole a lista (`Ctrl+V`).
- **À mão:** `Ctrl+N`.
- Confira e clique em **Emitir**. O histórico guarda PDF, XML e permite cancelar.

## O que precisa

- Windows 10 ou 11 com internet. O componente WebView2 é instalado junto, se faltar.
- Certificado digital A1 (e-CNPJ). Não existe chave de API: o certificado identifica a empresa na Receita.

## Segurança

- Certificado e senha ficam protegidos pelo Windows (DPAPI) para o usuário daquele computador. A chave nunca é gravada sem cifra.
- Opção de pedir a senha a cada envio, para computadores compartilhados.
- Comunicação direta com a Receita (`*.nfse.gov.br`, endereços fixos e verificados). O nome do cliente é consultado na BrasilAPI pelo CNPJ.
- Atualizações só são instaladas se o arquivo bater com o SHA-256 publicado no GitHub.
- A pasta compartilhada deve ter acesso só para as pessoas da empresa.

## Para quem desenvolve

```
pip install -r requirements-dev.txt
python -m pytest -q tests
python run.py
```

Para publicar: aumente a versão em `notara/__init__.py` e envie para `main`. O GitHub Actions testa, gera o instalador e publica em Releases (a tag é criada sozinha).
