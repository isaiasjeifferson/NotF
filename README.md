# NotF

Emissor de NFS-e para o Sistema Nacional da Receita. Feito para emitir várias notas de uma vez, com clientes salvos.

## Para quem usa

1. Baixe `NotF-X.Y.Z-Instalar.exe` em **Releases** e abra. Não precisa ser administrador.
2. Se aparecer “O Windows protegeu o computador”, clique em **Mais informações › Executar assim mesmo**.
3. Tenha em mãos o certificado A1 da empresa (.pfx), e a senha. O serviço você procura pelo nome (ex.: “lavanderia”) na lista oficial, e o NotF indica o NBS.
4. O NotF pede quatro coisas, uma por tela: certificado, dados da empresa, serviço e conexão.
5. Emita uma nota em **Teste** (sem validade fiscal). Depois clique em **Ligar produção**.
6. Em outro computador da mesma empresa: instale, importe o certificado e escolha a mesma pasta compartilhada.
7. As atualizações aparecem no próprio programa. As notas ficam em `Documentos\NotF` e não são apagadas ao desinstalar.

### No dia a dia

- **Clientes salvos:** busque pelo nome (`Ctrl+K`), escolha a unidade e informe o valor. Ou adicione todas as unidades com o último valor.
- **PDF ou lista:** arraste o PDF com a lista ou cole a lista (`Ctrl+V`).
- **À mão:** `Ctrl+N`.
- **Várias empresas:** clique no círculo com as iniciais (ou `Ctrl+Shift+E`) e troque com um clique. `Ctrl+2`, `Ctrl+3`… trocam direto.
- Confira, marque o que emitir e clique em **Emitir**. As desmarcadas ficam guardadas para depois.
- **Clientes:** cada cliente tem notas emitidas, total, valor médio, unidades e histórico.

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

### Tabela de serviços e NBS

`notara/catalogo.json` vem das planilhas oficiais da NFS-e Nacional (gov.br/nfse › Documentação técnica):
Anexo B (lista de serviços e NBS) e Anexo VIII (correlação serviço × NBS). Quando a Receita publicar uma versão nova:

    python tools/gerar_catalogo.py ANEXO_B.xlsx ANEXO_VIII.xlsx

e publique uma versão do NotF: o botão Buscar atualização entrega a tabela nova.
