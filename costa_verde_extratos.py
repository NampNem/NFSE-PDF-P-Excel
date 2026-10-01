import io
import re
import zipfile

import pandas as pd
import streamlit as st

# ============================================================

# SISTEMA: PROCESSADOR DE EXTRATOS BANCÁRIOS - COSTA VERDE

# ============================================================

def consultar_cnpj(cnpj_limpo):
"""Consulta Razão Social via BrasilAPI com fallback e cache local."""

```
import requests

if "cnpj_cache" not in st.session_state:
    st.session_state.cnpj_cache = {}

if cnpj_limpo in st.session_state.cnpj_cache:
    return st.session_state.cnpj_cache[cnpj_limpo]

url = f"https://brasilapi.com.br/api/cnpj/v1/{cnpj_limpo}"

try:
    response = requests.get(url, timeout=3)

    if response.status_code == 200:
        data = response.json()

        nome = (
            data.get("razao_social")
            or data.get("nome_fantasia")
            or cnpj_limpo
        )

        st.session_state.cnpj_cache[cnpj_limpo] = nome

        return nome

except Exception:
    pass

st.session_state.cnpj_cache[cnpj_limpo] = cnpj_limpo

return cnpj_limpo
```

def tratar_descricao_com_cnpj(tipo_transacao, texto_complementar=""):
"""
Localiza CNPJ e, quando houver, tenta buscar a razão social.
"""

```
texto_completo = f"{tipo_transacao} {texto_complementar}".strip()

cnpjs = re.findall(
    r'\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b',
    texto_completo
)

if cnpjs:

    cnpj_limpo = re.sub(
        r'\D',
        '',
        cnpjs[0]
    )

    nome_empresa = consultar_cnpj(
        cnpj_limpo
    )

    desc_limpa = re.sub(
        r'\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b',
        '',
        texto_completo
    )

    desc_limpa = re.sub(
        r'\s+',
        ' ',
        desc_limpa
    ).strip()

    if nome_empresa != cnpj_limpo:
        return f"{desc_limpa} - {nome_empresa}"

    return desc_limpa

texto_limpo = re.sub(
    r'[\|]',
    '',
    texto_completo
)

texto_limpo = re.sub(
    r'\s+',
    ' ',
    texto_limpo
).strip()

return texto_limpo
```

# ============================================================

# ITAÚ

# ============================================================

def parse_itau(texto_completo):

```
match_ag = re.search(
    r'Ag[êe]ncia\s*:?\s*(\d+)',
    texto_completo,
    re.IGNORECASE
)

match_cc = re.search(
    r'Conta\s*:?\s*([\d-]+)',
    texto_completo,
    re.IGNORECASE
)

agencia = (
    match_ag.group(1)
    if match_ag
    else "6081"
)

conta = (
    match_cc.group(1)
    if match_cc
    else "0098779-1"
)

linhas = [
    l.strip()
    for l in texto_completo.splitlines()
    if l.strip()
]

dados = []

saldos_informativos = [
    "SALDO ANTERIOR",
    "SALDO TOTAL",
    "SALDO MOVIMENTAÇÃO",
    "SALDO BLOQUEADO",
    "SALDO DISPONÍVEL",
    "SALDO APLIC. AUT."
]

for line in linhas:

    match_data = re.match(
        r'^(\d{2}/\d{2}/\d{4})\s+(.+)$',
        line
    )

    if not match_data:
        continue

    data = match_data.group(1)

    resto = match_data.group(2).strip()

    if any(
        term in resto.upper()
        for term in saldos_informativos
    ):
        continue

    match_valor = re.search(
        r'([-\+]?\s*[\d\.]+\,\d{2})$',
        resto
    )

    if not match_valor:
        continue

    valor_str = (
        match_valor.group(1)
        .replace(" ", "")
    )

    desc = (
        resto[:match_valor.start()]
        .strip()
    )

    if not desc:
        continue

    desc_final = tratar_descricao_com_cnpj(
        desc
    )

    dados.append({
        "DATA": data,
        "VALOR": valor_str,
        "DESCRIÇÃO": desc_final
    })

return (
    "ITAU",
    agencia,
    conta,
    pd.DataFrame(
        dados,
        columns=[
            "DATA",
            "VALOR",
            "DESCRIÇÃO"
        ]
    )
)
```

# ============================================================

# CORA

# ============================================================

def parse_cora(texto_completo):

```
"""
Processador do Banco Cora.

Mantém a última data encontrada mesmo quando uma
página nova começa diretamente com uma movimentação.

Exemplos aceitos:

31/08/2026 Saldo do dia R$ 13.804,06
31/08/2026 Compra no débito LOJA X - R$ 28,00
Pgto Pix recebido EMPRESA X + R$ 500,00
Transf Pix enviada EMPRESA Y - R$ 100,00
"""

# --------------------------------------------------------
# AGÊNCIA
# --------------------------------------------------------

match_ag = re.search(
    r'Ag[êe]ncia\s*:?\s*([0-9-]+)',
    texto_completo,
    re.IGNORECASE
)

agencia = (
    match_ag.group(1)
    if match_ag
    else "0001"
)

# --------------------------------------------------------
# CONTA
# --------------------------------------------------------

match_cc = re.search(
    r'Conta\s*:?\s*([0-9-]+)',
    texto_completo,
    re.IGNORECASE
)

conta = (
    match_cc.group(1)
    if match_cc
    else "0000000-0"
)

# --------------------------------------------------------
# NORMALIZAÇÃO DAS LINHAS
# --------------------------------------------------------

linhas = []

for linha in texto_completo.splitlines():

    linha = linha.strip()

    if not linha:
        continue

    # Remove barras usadas pelo PDF
    linha = re.sub(
        r'^\s*\|\s*',
        '',
        linha
    )

    linha = re.sub(
        r'\s*\|\s*$',
        '',
        linha
    )

    # Junta espaços duplicados
    linha = re.sub(
        r'\s+',
        ' ',
        linha
    ).strip()

    if linha:
        linhas.append(linha)

# --------------------------------------------------------
# PADRÕES
# --------------------------------------------------------

padrao_data = re.compile(
    r'^(\d{2}/\d{2}/\d{4})\b'
)

padrao_valor = re.compile(
    r'([+-])\s*R\$\s*([\d\.]+,\d{2})\s*$'
)

dados = []

data_atual = None

termos_ignorar = [
    "SALDO DO DIA",
    "SALDO INICIAL",
    "SALDO FINAL",
    "TOTAL DE ENTRADAS",
    "TOTAL DE SAÍDAS",
    "TOTAL DE SAIDAS",
    "SALDO ANTERIOR",
    "SALDO DISPONÍVEL",
    "SALDO DISPONIVEL"
]

# --------------------------------------------------------
# PROCESSAMENTO
# --------------------------------------------------------

for linha in linhas:

    linha_upper = linha.upper()

    # --------------------------------------------
    # Se a linha começar com uma data,
    # atualiza a data atual
    # --------------------------------------------

    match_data = padrao_data.match(
        linha
    )

    if match_data:

        data_atual = match_data.group(1)

    # Sem data ainda, não há como lançar
    if not data_atual:
        continue

    # --------------------------------------------
    # Procura valor no final da linha
    # --------------------------------------------

    match_valor = padrao_valor.search(
        linha
    )

    if not match_valor:
        continue

    # --------------------------------------------
    # Ignora saldos e totais
    # --------------------------------------------

    if any(
        termo in linha_upper
        for termo in termos_ignorar
    ):
        continue

    sinal = match_valor.group(1)

    valor_numero = match_valor.group(2)

    # --------------------------------------------
    # Monta valor
    # --------------------------------------------

    if sinal == "-":
        valor = f"-{valor_numero}"
    else:
        valor = valor_numero

    # --------------------------------------------
    # Remove o valor da descrição
    # --------------------------------------------

    descricao = (
        linha[:match_valor.start()]
        .strip()
    )

    # Remove data do início
    descricao = re.sub(
        r'^\d{2}/\d{2}/\d{4}\s*',
        '',
        descricao
    ).strip()

    # --------------------------------------------
    # Ignora descrições vazias
    # --------------------------------------------

    if not descricao:
        continue

    # --------------------------------------------
    # Limpeza
    # --------------------------------------------

    descricao = re.sub(
        r'\s+',
        ' ',
        descricao
    ).strip()

    descricao_final = tratar_descricao_com_cnpj(
        descricao
    )

    dados.append({
        "DATA": data_atual,
        "VALOR": valor,
        "DESCRIÇÃO": descricao_final
    })

df = pd.DataFrame(
    dados,
    columns=[
        "DATA",
        "VALOR",
        "DESCRIÇÃO"
    ]
)

return (
    "CORA",
    agencia,
    conta,
    df
)
```

# ============================================================

# XP

# ============================================================

def parse_xp(texto_completo):

```
match_cc = re.search(
    r'Conta\s*:?\s*(\d+)',
    texto_completo,
    re.IGNORECASE
)

conta = (
    match_cc.group(1)
    if match_cc
    else "000000"
)

agencia = "0001"

dados = []

linhas = [
    l.strip()
    for l in texto_completo.splitlines()
    if l.strip()
]

for line in linhas:

    partes = [
        p.strip()
        for p in line.split('|')
        if p.strip()
    ]

    if len(partes) < 3:
        continue

    if not re.match(
        r'^\d{2}/\d{2}/\d{4}$',
        partes[0]
    ):
        continue

    data = partes[0]

    if (
        len(partes) >= 2
        and re.match(
            r'^\d{2}/\d{2}/\d{4}$',
            partes[1]
        )
    ):

        desc = (
            partes[2]
            if len(partes) > 2
            else ""
        )

        valor_raw = (
            partes[3]
            if len(partes) > 3
            else ""
        )

    else:

        desc = partes[1]

        valor_raw = (
            partes[2]
            if len(partes) > 2
            else ""
        )

    match_val = re.search(
        r'([-\+]?\s*R\$\s*[\d\.,]+|[-\+]?\d+[\d\.,]*)',
        valor_raw
    )

    if not match_val:
        continue

    val_str = (
        match_val.group(1)
        .replace("R$", "")
        .replace(" ", "")
        .strip()
    )

    desc_final = tratar_descricao_com_cnpj(
        desc
    )

    dados.append({
        "DATA": data,
        "VALOR": val_str,
        "DESCRIÇÃO": desc_final
    })

return (
    "XP",
    agencia,
    conta,
    pd.DataFrame(
        dados,
        columns=[
            "DATA",
            "VALOR",
            "DESCRIÇÃO"
        ]
    )
)
```

# ============================================================

# EXTRAÇÃO DO TEXTO DO PDF

# ============================================================

def extrair_texto_pdf(file_bytes):

```
import pdfplumber

texto_paginas = []

with pdfplumber.open(
    io.BytesIO(file_bytes)
) as pdf:

    for page in pdf.pages:

        try:
            texto = page.extract_text(
                layout=False
            )
        except Exception:
            texto = None

        if texto:
            texto_paginas.append(
                texto
            )

return "\n".join(
    texto_paginas
)
```

# ============================================================

# DETECÇÃO DO BANCO

# ============================================================

def detectar_banco(texto_completo):

```
texto_upper = texto_completo.upper()

# Cora primeiro para evitar conflito
if (
    "CORA" in texto_upper
    or "CORA SCFI" in texto_upper
):
    return "CORA"

if (
    "ITAÚ" in texto_upper
    or "ITAU" in texto_upper
):
    return "ITAU"

if (
    "XP INVESTIMENTOS" in texto_upper
    or "XPI" in texto_upper
):
    return "XP"

return "DESCONHECIDO"
```

# ============================================================

# EXTRAÇÃO COMPLETA

# ============================================================

def extrair_dados_pdf(file_bytes):

```
texto_completo = extrair_texto_pdf(
    file_bytes
)

if not texto_completo.strip():

    return (
        "DESCONHECIDO",
        "0000",
        "00000",
        pd.DataFrame(
            columns=[
                "DATA",
                "VALOR",
                "DESCRIÇÃO"
            ]
        )
    )

banco = detectar_banco(
    texto_completo
)

if banco == "CORA":

    return parse_cora(
        texto_completo
    )

if banco == "ITAU":

    return parse_itau(
        texto_completo
    )

if banco == "XP":

    return parse_xp(
        texto_completo
    )

return (
    "DESCONHECIDO",
    "0000",
    "00000",
    pd.DataFrame(
        columns=[
            "DATA",
            "VALOR",
            "DESCRIÇÃO"
        ]
    )
)
```

# ============================================================

# INTERFACE STREAMLIT

# ============================================================

def pagina_costa_verde_extratos():

```
st.title(
    "📊 Extratos - Costa Verde"
)

st.caption(
    "Processador e Padronizador de Extratos Bancários"
)

try:

    import requests
    import pdfplumber
    import openpyxl

    _ = requests
    _ = pdfplumber
    _ = openpyxl

except ImportError as e:

    st.error(
        f"❌ Falta uma biblioteca: {e}"
    )

    st.info(
        "Verifique se requests, pdfplumber, "
        "pandas e openpyxl estão no requirements.txt."
    )

    return

uploaded_files = st.file_uploader(
    "Arraste os extratos em PDF aqui",
    type=["pdf"],
    accept_multiple_files=True,
    key="upload_extratos_costa_verde"
)

if uploaded_files and st.button(
    "🚀 Processar e Gerar Planilhas",
    key="processar_extratos_costa_verde"
):

    arquivos_gerados = {}

    for file in uploaded_files:

        try:

            file_bytes = file.read()

            banco, agencia, conta, df = extrair_dados_pdf(
                file_bytes
            )

            nome_chave = (
                f"{banco} - {agencia} - {conta}"
            )

            if df.empty:

                st.warning(
                    f"⚠️ {file.name}: "
                    f"Nenhuma movimentação capturada."
                )

                # Mostra diagnóstico somente quando
                # nenhum lançamento foi encontrado
                with st.expander(
                    f"🔎 Diagnóstico - {file.name}"
                ):

                    try:

                        texto_debug = extrair_texto_pdf(
                            file_bytes
                        )

                        st.write(
                            "Banco detectado:",
                            detectar_banco(
                                texto_debug
                            )
                        )

                        st.write(
                            "Quantidade de caracteres extraídos:",
                            len(texto_debug)
                        )

                        st.text(
                            texto_debug[:5000]
                        )

                    except Exception as erro_debug:

                        st.error(
                            f"Erro no diagnóstico: {erro_debug}"
                        )

            else:

                st.success(
                    f"✅ Processado: "
                    f"**{nome_chave}.xlsx** "
                    f"({len(df)} lançamentos)"
                )

                # Visualização para conferência
                with st.expander(
                    f"👁️ Conferir dados - {file.name}"
                ):

                    st.dataframe(
                        df,
                        use_container_width=True,
                        hide_index=True
                    )

            # ------------------------------------------------
            # GERA EXCEL
            # ------------------------------------------------

            buffer = io.BytesIO()

            with pd.ExcelWriter(
                buffer,
                engine="openpyxl"
            ) as writer:

                df[
                    [
                        "DATA",
                        "VALOR",
                        "DESCRIÇÃO"
                    ]
                ].to_excel(
                    writer,
                    index=False,
                    sheet_name="Extrato"
                )

            arquivos_gerados[
                f"{nome_chave}.xlsx"
            ] = buffer.getvalue()

        except Exception as e:

            st.error(
                f"❌ Erro ao processar "
                f"**{file.name}**: {e}"
            )

    # --------------------------------------------------------
    # DOWNLOAD
    # --------------------------------------------------------

    if arquivos_gerados:

        st.markdown("---")

        st.subheader(
            "📥 Download dos Arquivos Separados"
        )

        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(
            zip_buffer,
            "w",
            compression=zipfile.ZIP_DEFLATED
        ) as zip_file:

            for nome_arq, dados_arq in arquivos_gerados.items():

                zip_file.writestr(
                    nome_arq,
                    dados_arq
                )

        st.download_button(
            label="📦 Baixar Todos em ZIP",
            data=zip_buffer.getvalue(),
            file_name="Costa_Verde_Extratos_Formatados.zip",
            mime="application/zip",
            key="download_zip_costa_verde"
        )
```

# ============================================================

# COMPATIBILIDADE

# ============================================================

def pagina_extratos():
pagina_costa_verde_extratos()

if **name** == "**main**":
pagina_costa_verde_extratos()
