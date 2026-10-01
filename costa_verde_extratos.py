import io
import re
import zipfile

import pandas as pd
import streamlit as st

# ============================================================

# SISTEMA: PROCESSADOR DE EXTRATOS BANCÁRIOS - COSTA VERDE

# ============================================================

def consultar_cnpj(cnpj_limpo):
"""Consulta Razão Social via BrasilAPI com fallback e cache."""

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

# ============================================================

# TRATAMENTO DE CNPJ

# ============================================================

def tratar_descricao_com_cnpj(
tipo_transacao,
texto_complementar=""
):

```
texto_completo = (
    f"{tipo_transacao} {texto_complementar}"
).strip()

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
    linha.strip()
    for linha in texto_completo.splitlines()
    if linha.strip()
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

for linha in linhas:

    match_data = re.match(
        r'^(\d{2}/\d{2}/\d{4})\s+(.+)$',
        linha
    )

    if not match_data:
        continue

    data = match_data.group(1)

    resto = match_data.group(2).strip()

    if any(
        termo in resto.upper()
        for termo in saldos_informativos
    ):
        continue

    match_valor = re.search(
        r'([-\+]?\s*[\d\.]+,\d{2})$',
        resto
    )

    if not match_valor:
        continue

    valor = (
        match_valor.group(1)
        .replace(" ", "")
    )

    descricao = (
        resto[:match_valor.start()]
        .strip()
    )

    if not descricao:
        continue

    descricao = tratar_descricao_com_cnpj(
        descricao
    )

    dados.append({
        "DATA": data,
        "VALOR": valor,
        "DESCRIÇÃO": descricao
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

# CORA - FUNÇÕES AUXILIARES

# ============================================================

def limpar_linha_cora(linha):

```
if not linha:
    return ""

linha = linha.replace("\xa0", " ")

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

linha = re.sub(
    r'\s+',
    ' ',
    linha
)

return linha.strip()
```

def valor_cora_para_string(numero, sinal):

```
numero = numero.replace(
    " ",
    ""
)

if sinal == "-":
    return f"-{numero}"

return numero
```

def procurar_valor_cora(linha):

```
"""
Aceita vários formatos encontrados em PDFs da Cora:

- R$ 100,00
+ R$ 100,00
- R$ 100,00
R$ - 100,00
R$ + 100,00
100,00
-100,00
+100,00
"""

padroes = [

    # - R$ 100,00
    r'([+-])\s*R\$\s*([\d\.]+,\d{2})\s*$',

    # R$ - 100,00
    r'R\$\s*([+-])\s*([\d\.]+,\d{2})\s*$',

    # R$100,00
    r'R\$\s*([\d\.]+,\d{2})\s*$',

    # -100,00 / +100,00
    r'([+-])\s*([\d\.]+,\d{2})\s*$',

    # valor sem sinal
    r'([\d\.]+,\d{2})\s*$'
]

for indice, padrao in enumerate(padroes):

    resultado = re.search(
        padrao,
        linha
    )

    if not resultado:
        continue

    if indice == 0:

        sinal = resultado.group(1)
        numero = resultado.group(2)

        return (
            resultado,
            valor_cora_para_string(
                numero,
                sinal
            )
        )

    if indice == 1:

        sinal = resultado.group(1)
        numero = resultado.group(2)

        return (
            resultado,
            valor_cora_para_string(
                numero,
                sinal
            )
        )

    if indice == 2:

        numero = resultado.group(1)

        return (
            resultado,
            numero
        )

    if indice == 3:

        sinal = resultado.group(1)
        numero = resultado.group(2)

        return (
            resultado,
            valor_cora_para_string(
                numero,
                sinal
            )
        )

    if indice == 4:

        numero = resultado.group(1)

        return (
            resultado,
            numero
        )

return None, None
```

def eh_linha_saldo_ou_total_cora(linha):

```
texto = linha.upper()

termos = [

    "SALDO DO DIA",
    "SALDO INICIAL",
    "SALDO FINAL",
    "SALDO ANTERIOR",
    "SALDO DISPONÍVEL",
    "SALDO DISPONIVEL",

    "TOTAL DE ENTRADAS",
    "TOTAL DE SAÍDAS",
    "TOTAL DE SAIDAS",

    "MOVIMENTAÇÃO DO DIA",
    "MOVIMENTACAO DO DIA"
]

return any(
    termo in texto
    for termo in termos
)
```

def extrair_data_cora(linha):

```
resultado = re.search(
    r'\b(\d{2}/\d{2}/\d{4})\b',
    linha
)

if resultado:
    return resultado.group(1)

return None
```

# ============================================================

# CORA - MÉTODO PRINCIPAL

# ============================================================

def parse_cora(texto_completo):

```
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
# LINHAS
# --------------------------------------------------------

linhas = []

for linha in texto_completo.splitlines():

    linha = limpar_linha_cora(
        linha
    )

    if linha:
        linhas.append(
            linha
        )

# --------------------------------------------------------
# PROCESSAMENTO
# --------------------------------------------------------

dados = []

data_atual = None

i = 0

while i < len(linhas):

    linha = linhas[i]

    # ----------------------------------------------------
    # ATUALIZA DATA
    # ----------------------------------------------------

    nova_data = extrair_data_cora(
        linha
    )

    if nova_data:
        data_atual = nova_data

    # ----------------------------------------------------
    # SEM DATA AINDA
    # ----------------------------------------------------

    if not data_atual:
        i += 1
        continue

    # ----------------------------------------------------
    # IGNORA SALDOS / TOTAIS
    # ----------------------------------------------------

    if eh_linha_saldo_ou_total_cora(
        linha
    ):
        i += 1
        continue

    # ----------------------------------------------------
    # TENTA ENCONTRAR VALOR NA PRÓPRIA LINHA
    # ----------------------------------------------------

    match_valor, valor = procurar_valor_cora(
        linha
    )

    if match_valor:

        descricao = (
            linha[:match_valor.start()]
            .strip()
        )

        descricao = re.sub(
            r'^\d{2}/\d{2}/\d{4}\s*',
            '',
            descricao
        ).strip()

        if descricao:

            descricao = tratar_descricao_com_cnpj(
                descricao
            )

            dados.append({
                "DATA": data_atual,
                "VALOR": valor,
                "DESCRIÇÃO": descricao
            })

        i += 1
        continue

    # ----------------------------------------------------
    # TENTA JUNTAR COM A PRÓXIMA LINHA
    #
    # Alguns PDFs quebram a movimentação em duas linhas.
    # Exemplo:
    #
    # Transf Pix enviada EMPRESA
    # 01.234.567/0001-00 - R$ 100,00
    # ----------------------------------------------------

    if i + 1 < len(linhas):

        proxima = linhas[i + 1]

        if not eh_linha_saldo_ou_total_cora(
            proxima
        ):

            texto_junto = (
                linha + " " + proxima
            )

            match_valor_junto, valor_junto = (
                procurar_valor_cora(
                    texto_junto
                )
            )

            if match_valor_junto:

                descricao = (
                    texto_junto[
                        :match_valor_junto.start()
                    ].strip()
                )

                descricao = re.sub(
                    r'^\d{2}/\d{2}/\d{4}\s*',
                    '',
                    descricao
                ).strip()

                if descricao:

                    descricao = (
                        tratar_descricao_com_cnpj(
                            descricao
                        )
                    )

                    dados.append({
                        "DATA": data_atual,
                        "VALOR": valor_junto,
                        "DESCRIÇÃO": descricao
                    })

                    i += 2
                    continue

    i += 1

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

# CORA - SEGUNDO MÉTODO DE EXTRAÇÃO

# ============================================================

def parse_cora_layout_true(texto_paginas):

```
"""
Segundo método da Cora.

Recebe as páginas individualmente e tenta reconstruir
o texto usando layout=True.
"""

textos = []

for texto in texto_paginas:

    if texto:
        textos.append(
            texto
        )

texto_completo = "\n".join(
    textos
)

return parse_cora(
    texto_completo
)
```

# ============================================================

# EXTRAÇÃO DE TEXTO DO PDF

# ============================================================

def extrair_textos_pdf(file_bytes):

```
import pdfplumber

textos_layout_false = []
textos_layout_true = []

with pdfplumber.open(
    io.BytesIO(file_bytes)
) as pdf:

    for page in pdf.pages:

        # --------------------------------------------
        # MÉTODO 1 - layout=False
        # --------------------------------------------

        try:

            texto_false = page.extract_text(
                layout=False
            )

        except Exception:

            texto_false = None

        if texto_false:
            textos_layout_false.append(
                texto_false
            )

        # --------------------------------------------
        # MÉTODO 2 - layout=True
        # --------------------------------------------

        try:

            texto_true = page.extract_text(
                layout=True
            )

        except Exception:

            texto_true = None

        if texto_true:
            textos_layout_true.append(
                texto_true
            )

return (
    textos_layout_false,
    textos_layout_true
)
```

# ============================================================

# DETECÇÃO DO BANCO

# ============================================================

def detectar_banco(texto_completo):

```
texto_upper = (
    texto_completo
    .upper()
)

# Cora primeiro
if (
    "CORA" in texto_upper
    or "CORA SCFI" in texto_upper
    or "BANCO CORA" in texto_upper
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
textos_false, textos_true = (
    extrair_textos_pdf(
        file_bytes
    )
)

texto_false = "\n".join(
    textos_false
)

texto_true = "\n".join(
    textos_true
)

# --------------------------------------------------------
# DETECTA BANCO USANDO OS DOIS MÉTODOS
# --------------------------------------------------------

banco_false = detectar_banco(
    texto_false
)

banco_true = detectar_banco(
    texto_true
)

if banco_false == "CORA":

    banco, agencia, conta, df = parse_cora(
        texto_false
    )

    # Se encontrou lançamentos, usa esse resultado
    if not df.empty:

        return (
            banco,
            agencia,
            conta,
            df
        )

    # Caso contrário tenta layout=True
    banco2, agencia2, conta2, df2 = (
        parse_cora_layout_true(
            textos_true
        )
    )

    if not df2.empty:

        return (
            banco2,
            agencia2,
            conta2,
            df2
        )

    return (
        banco,
        agencia,
        conta,
        df
    )

# --------------------------------------------------------
# CASO O PRIMEIRO MÉTODO NÃO IDENTIFIQUE CORA,
# MAS O SEGUNDO IDENTIFIQUE
# --------------------------------------------------------

if banco_true == "CORA":

    banco, agencia, conta, df = (
        parse_cora_layout_true(
            textos_true
        )
    )

    return (
        banco,
        agencia,
        conta,
        df
    )

# --------------------------------------------------------
# ITAÚ
# --------------------------------------------------------

if banco_false == "ITAU":

    return parse_itau(
        texto_false
    )

if banco_true == "ITAU":

    return parse_itau(
        texto_true
    )

# --------------------------------------------------------
# XP
# --------------------------------------------------------

if banco_false == "XP":

    return parse_xp(
        texto_false
    )

if banco_true == "XP":

    return parse_xp(
        texto_true
    )

# --------------------------------------------------------
# DESCONHECIDO
# --------------------------------------------------------

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

# --------------------------------------------------------
# BIBLIOTECAS
# --------------------------------------------------------

try:

    import requests
    import pdfplumber
    import openpyxl

    _ = requests
    _ = pdfplumber
    _ = openpyxl

except ImportError as e:

    st.error(
        f"❌ Biblioteca faltando: {e}"
    )

    st.info(
        "Verifique o requirements.txt."
    )

    return

# --------------------------------------------------------
# UPLOAD
# --------------------------------------------------------

uploaded_files = st.file_uploader(
    "Arraste os extratos em PDF aqui",
    type=["pdf"],
    accept_multiple_files=True,
    key="upload_extratos_costa_verde"
)

# --------------------------------------------------------
# PROCESSAR
# --------------------------------------------------------

if uploaded_files and st.button(
    "🚀 Processar e Gerar Planilhas",
    key="processar_extratos_costa_verde"
):

    arquivos_gerados = {}

    for file in uploaded_files:

        try:

            file_bytes = file.read()

            banco, agencia, conta, df = (
                extrair_dados_pdf(
                    file_bytes
                )
            )

            nome_chave = (
                f"{banco} - {agencia} - {conta}"
            )

            # ------------------------------------------------
            # RESULTADO
            # ------------------------------------------------

            if df.empty:

                st.error(
                    f"❌ {file.name}: "
                    f"0 lançamentos encontrados."
                )

            else:

                st.success(
                    f"✅ {file.name}: "
                    f"{len(df)} lançamentos encontrados."
                )

                # --------------------------------------------
                # TOTALIZADORES
                # --------------------------------------------

                entradas = 0.0
                saidas = 0.0

                for valor in df["VALOR"]:

                    try:

                        numero = str(
                            valor
                        ).replace(
                            ".",
                            ""
                        ).replace(
                            ",",
                            "."
                        )

                        numero = float(
                            numero
                        )

                        if numero >= 0:
                            entradas += numero
                        else:
                            saidas += abs(numero)

                    except Exception:
                        pass

                col1, col2, col3 = st.columns(3)

                with col1:
                    st.metric(
                        "Lançamentos",
                        len(df)
                    )

                with col2:
                    st.metric(
                        "Entradas",
                        f"R$ {entradas:,.2f}".replace(
                            ",",
                            "X"
                        ).replace(
                            ".",
                            ","
                        ).replace(
                            "X",
                            "."
                        )
                    )

                with col3:
                    st.metric(
                        "Saídas",
                        f"R$ {saidas:,.2f}".replace(
                            ",",
                            "X"
                        ).replace(
                            ".",
                            ","
                        ).replace(
                            "X",
                            "."
                        )
                    )

                # --------------------------------------------
                # PRÉVIA
                # --------------------------------------------

                with st.expander(
                    f"👁️ Ver lançamentos - {file.name}",
                    expanded=True
                ):

                    st.dataframe(
                        df,
                        use_container_width=True,
                        hide_index=True
                    )

            # ------------------------------------------------
            # DIAGNÓSTICO
            # ------------------------------------------------

            if df.empty:

                with st.expander(
                    f"🔎 Diagnóstico - {file.name}",
                    expanded=True
                ):

                    try:

                        textos_false, textos_true = (
                            extrair_textos_pdf(
                                file_bytes
                            )
                        )

                        texto_false = "\n".join(
                            textos_false
                        )

                        texto_true = "\n".join(
                            textos_true
                        )

                        st.write(
                            "Banco detectado - layout normal:",
                            detectar_banco(
                                texto_false
                            )
                        )

                        st.write(
                            "Banco detectado - layout verdadeiro:",
                            detectar_banco(
                                texto_true
                            )
                        )

                        st.write(
                            "Caracteres extraídos - layout normal:",
                            len(texto_false)
                        )

                        st.write(
                            "Caracteres extraídos - layout verdadeiro:",
                            len(texto_true)
                        )

                        st.write(
                            "Quantidade de páginas/textos:",
                            len(textos_false)
                        )

                        st.write(
                            "Primeiros 5.000 caracteres "
                            "do layout normal:"
                        )

                        st.text(
                            texto_false[:5000]
                        )

                        st.write(
                            "Primeiros 5.000 caracteres "
                            "do layout verdadeiro:"
                        )

                        st.text(
                            texto_true[:5000]
                        )

                    except Exception as erro_debug:

                        st.exception(
                            erro_debug
                        )

            # ------------------------------------------------
            # EXCEL
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

            st.exception(
                e
            )

    # --------------------------------------------------------
    # DOWNLOAD ZIP
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

            for nome_arq, dados_arq in (
                arquivos_gerados.items()
            ):

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

```
pagina_costa_verde_extratos()
```

# ============================================================

# EXECUÇÃO DIRETA

# ============================================================

if **name** == "**main**":

```
pagina_costa_verde_extratos()
```
