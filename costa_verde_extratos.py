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
import requests

```
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

return cnpj_limpo
```

def tratar_descricao_com_cnpj(tipo_transacao, texto_complementar=""):
"""
Localiza CNPJ/CPF e, quando houver CNPJ, tenta buscar a razão social.
"""

```
texto_completo = f"{tipo_transacao} {texto_complementar}".strip()

# ========================================================
# PROCURA CNPJ
# ========================================================

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
    ).strip()

    desc_limpa = re.sub(
        r'\s+',
        ' ',
        desc_limpa
    )

    if nome_empresa != cnpj_limpo:
        return f"{desc_limpa} - {nome_empresa}"

    return desc_limpa

# ========================================================
# LIMPEZA NORMAL
# ========================================================

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
    r'Ag[êe]ncia\s+(\d+)',
    texto_completo,
    re.IGNORECASE
)

match_cc = re.search(
    r'Conta\s+([\d-]+)',
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
    for l in texto_completo.split('\n')
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

    if match_data:

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

        if match_valor:

            valor_str = (
                match_valor.group(1)
                .replace(" ", "")
            )

            desc = (
                resto[:match_valor.start()]
                .strip()
            )

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
    pd.DataFrame(dados)
)
```

# ============================================================

# CORA

# ============================================================

def parse_cora(texto_completo):
"""
Processa extratos do Banco Cora.

```
Layout identificado no PDF da Cora:
- Data aparece junto com "Saldo do dia"
- As movimentações aparecem em linhas individuais
- O valor aparece no final da linha
- As páginas podem continuar movimentações do dia anterior
"""

# ========================================================
# AGÊNCIA
# ========================================================

match_ag = re.search(
    r'Ag[êe]ncia:\s*([\d-]+)',
    texto_completo,
    re.IGNORECASE
)

# ========================================================
# CONTA
# ========================================================

match_cc = re.search(
    r'Conta:\s*([\d-]+)',
    texto_completo,
    re.IGNORECASE
)

agencia = (
    match_ag.group(1)
    if match_ag
    else "0001"
)

conta = (
    match_cc.group(1)
    if match_cc
    else "0000000-0"
)

dados = []

data_atual = None

# ========================================================
# PREPARA AS LINHAS
# ========================================================

linhas = [
    re.sub(
        r'^\s*\|\s*',
        '',
        linha
    ).strip()
    for linha in texto_completo.splitlines()
    if linha.strip()
]

# ========================================================
# PADRÃO DE DATA
# ========================================================

padrao_data = re.compile(
    r'^(\d{2}/\d{2}/\d{4})\b'
)

# ========================================================
# PADRÃO DE VALOR
#
# Exemplos:
#
# - R$ 28,00
# + R$ 10.000,00
# ========================================================

padrao_valor = re.compile(
    r'([+-])\s*R\$\s*([\d\.]+,\d{2})\s*$'
)

# ========================================================
# PROCESSAMENTO
# ========================================================

for linha in linhas:

    # ----------------------------------------------------
    # IDENTIFICA DATA
    # ----------------------------------------------------

    match_data = padrao_data.match(
        linha
    )

    if match_data:

        data_atual = match_data.group(1)

        # Se for "Saldo do dia", não é lançamento
        if re.search(
            r'\bSaldo do dia\b',
            linha,
            re.IGNORECASE
        ):
            continue

    # ----------------------------------------------------
    # SE AINDA NÃO TEM DATA, IGNORA
    # ----------------------------------------------------

    if not data_atual:
        continue

    # ----------------------------------------------------
    # PROCURA VALOR
    # ----------------------------------------------------

    match_valor = padrao_valor.search(
        linha
    )

    if not match_valor:
        continue

    # ----------------------------------------------------
    # IGNORA SALDOS / TOTAIS
    # ----------------------------------------------------

    if re.search(
        r'\bSaldo do dia\b'
        r'|\bSaldo inicial\b'
        r'|\bSaldo final\b'
        r'|\bTotal de entradas\b'
        r'|\bTotal de saídas\b',
        linha,
        re.IGNORECASE
    ):
        continue

    # ----------------------------------------------------
    # SINAL
    # ----------------------------------------------------

    sinal = match_valor.group(1)

    # ----------------------------------------------------
    # VALOR
    # ----------------------------------------------------

    valor_numero = match_valor.group(2)

    if sinal == "-":
        valor = f"-{valor_numero}"
    else:
        valor = valor_numero

    # ----------------------------------------------------
    # DESCRIÇÃO
    # ----------------------------------------------------

    descricao = (
        linha[:match_valor.start()]
        .strip()
    )

    descricao = re.sub(
        r'\s+',
        ' ',
        descricao
    ).strip()

    if not descricao:
        continue

    # ----------------------------------------------------
    # TRATA CNPJ
    # ----------------------------------------------------

    descricao_final = tratar_descricao_com_cnpj(
        descricao
    )

    # ----------------------------------------------------
    # ADICIONA LANÇAMENTO
    # ----------------------------------------------------

    dados.append({
        "DATA": data_atual,
        "VALOR": valor,
        "DESCRIÇÃO": descricao_final
    })

# ========================================================
# DATAFRAME
# ========================================================

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
    r'Conta:\s*(\d+)',
    texto_completo
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
    for l in texto_completo.split('\n')
    if l.strip()
]

for line in linhas:

    partes = [
        p.strip()
        for p in line.split('|')
        if p.strip()
    ]

    if len(partes) >= 3:

        if re.match(
            r'^\d{2}/\d{2}/\d{4}$',
            partes[0]
        ):

            data = partes[0]

            if re.match(
                r'^\d{2}/\d{2}/\d{4}$',
                partes[1]
            ):

                desc = partes[2]

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

            if match_val:

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
    pd.DataFrame(dados)
)
```

# ============================================================

# EXTRAÇÃO DO PDF

# ============================================================

def extrair_dados_pdf(file_bytes):

```
import pdfplumber

with pdfplumber.open(
    io.BytesIO(file_bytes)
) as pdf:

    texto_completo = ""

    for page in pdf.pages:

        t = page.extract_text(
            layout=False
        )

        if t:
            texto_completo += t + "\n"

texto_upper = texto_completo.upper()

# ========================================================
# IDENTIFICA BANCO
# ========================================================

if (
    "ITAÚ" in texto_upper
    or "ITAU" in texto_upper
):

    return parse_itau(
        texto_completo
    )

elif "CORA" in texto_upper:

    return parse_cora(
        texto_completo
    )

elif (
    "XP INVESTIMENTOS" in texto_upper
    or "XPI" in texto_upper
):

    return parse_xp(
        texto_completo
    )

else:

    return (
        "DESCONHECIDO",
        "0000",
        "00000",
        pd.DataFrame()
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

# ========================================================
# VERIFICA BIBLIOTECAS
# ========================================================

try:

    import requests  # noqa: F401
    import pdfplumber  # noqa: F401

except ImportError:

    st.error(
        "Faltam bibliotecas para este sistema. "
        "Adicione `requests` e `pdfplumber` "
        "no arquivo requirements.txt e "
        "reinicie o programa."
    )

    return

# ========================================================
# UPLOAD
# ========================================================

uploaded_files = st.file_uploader(
    "Arraste os extratos em PDF aqui",
    type=["pdf"],
    accept_multiple_files=True,
    key="upload_extratos_costa_verde",
)

# ========================================================
# BOTÃO PROCESSAR
# ========================================================

if uploaded_files and st.button(
    "🚀 Processar e Gerar Planilhas",
    key="processar_extratos_costa_verde",
):

    arquivos_gerados = {}

    # ====================================================
    # PROCESSA CADA PDF
    # ====================================================

    for file in uploaded_files:

        try:

            banco, agencia, conta, df = extrair_dados_pdf(
                file.read()
            )

            nome_chave = (
                f"{banco} - {agencia} - {conta}"
            )

            # ============================================
            # SEM DADOS
            # ============================================

            if df.empty:

                df = pd.DataFrame(
                    columns=[
                        "DATA",
                        "VALOR",
                        "DESCRIÇÃO"
                    ]
                )

                st.warning(
                    f"⚠️ {file.name}: "
                    f"Nenhuma movimentação capturada "
                    f"ou PDF sem lançamentos."
                )

            # ============================================
            # COM DADOS
            # ============================================

            else:

                st.success(
                    f"✅ Processado: "
                    f"**{nome_chave}.xlsx** "
                    f"({len(df)} lançamentos)"
                )

            # ============================================
            # EXCEL
            # ============================================

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

    # ====================================================
    # GERA ZIP
    # ====================================================

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

        # ================================================
        # DOWNLOAD
        # ================================================

        st.download_button(
            label="📦 Baixar Todos em ZIP",
            data=zip_buffer.getvalue(),
            file_name="Costa_Verde_Extratos_Formatados.zip",
            mime="application/zip",
        )
```

# ============================================================

# EXECUÇÃO

# ============================================================

if **name** == "**main**":
pagina_costa_verde_extratos()
