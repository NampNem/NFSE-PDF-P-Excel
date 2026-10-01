import io
import re
import zipfile

import pandas as pd
import streamlit as st


# ============================================================
# SISTEMA: PROCESSADOR DE EXTRATOS BANCÁRIOS
# ============================================================
def consultar_cnpj(cnpj_limpo):
    """Consulta Razão Social via BrasilAPI com fallback e cache local."""
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
            nome = data.get("razao_social") or data.get("nome_fantasia") or cnpj_limpo
            st.session_state.cnpj_cache[cnpj_limpo] = nome
            return nome
    except Exception:
        pass
    return cnpj_limpo


def tratar_descricao_com_cnpj(tipo_transacao, texto_complementar):
    """Localiza o CNPJ e monta o padrão 'TIPO DE OPERAÇÃO - NOME ENCONTRADO'."""
    cnpjs = re.findall(r'\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b', texto_complementar)
    if cnpjs:
        cnpj_limpo = re.sub(r'\D', '', cnpjs[0])
        nome_empresa = consultar_cnpj(cnpj_limpo)
        return f"{tipo_transacao.strip()} - {nome_empresa}"

    # Se não houver CNPJ formato padrão, limpa o texto complementar e junta
    texto_limpo = re.sub(r'\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}', '', texto_complementar).strip()
    if texto_limpo:
        return f"{tipo_transacao.strip()} - {texto_limpo}"
    return tipo_transacao.strip()


def parse_itau(texto_completo):
    match_ag = re.search(r'Ag[êe]ncia\s+(\d+)', texto_completo, re.IGNORECASE)
    match_cc = re.search(r'Conta\s+([\d-]+)', texto_completo, re.IGNORECASE)
    agencia = match_ag.group(1) if match_ag else "0001"
    conta = match_cc.group(1) if match_cc else "000000"

    linhas = texto_completo.split('\n')
    dados = []

    # Expressão para identificar linha de lançamento no Itaú: DD/MM/AAAA
    for i, line in enumerate(linhas):
        match_data = re.match(r'^(\d{2}/\d{2}/\d{4})\s+(.+)$', line.strip())
        if match_data:
            data = match_data.group(1)
            resto = match_data.group(2)

            # Descarta linhas de saldos informativos
            if any(s in resto.upper() for s in ["SALDO ANTERIOR", "SALDO TOTAL", "SALDO MOVIMENTAÇÃO", "SALDO APLIC"]):
                continue

            # Tenta pegar valor no final da linha
            partes = resto.split()
            valor = partes[-1] if partes else ""
            tipo_op = " ".join(partes[:-1]) if len(partes) > 1 else resto

            # Tenta buscar complemento com CNPJ na linha imediatamente posterior
            complemento = ""
            if i + 1 < len(linhas):
                prox_linha = linhas[i + 1].strip()
                if not re.match(r'^\d{2}/\d{2}/\d{4}', prox_linha):
                    complemento = prox_linha

            desc_final = tratar_descricao_com_cnpj(tipo_op, complemento)
            dados.append({"DATA": data, "VALOR": valor, "DESCRIÇÃO": desc_final})

    return "ITAU", agencia, conta, pd.DataFrame(dados)


def parse_cora(texto_completo):
    match_ag = re.search(r'Ag[êe]ncia:\s*([\d-]+)', texto_completo)
    match_cc = re.search(r'Conta:\s*([\d-]+)', texto_completo)
    agencia = match_ag.group(1) if match_ag else "0001"
    conta = match_cc.group(1) if match_cc else "000000"

    dados = []
    linhas = [l.strip() for l in texto_completo.split('\n') if l.strip()]

    i = 0
    data_atual = None
    while i < len(linhas):
        line = linhas[i]

        # Identifica cabeçalhos de data no Cora (ex: 30/03/2026)
        if re.match(r'^\d{2}/\d{2}/\d{4}$', line):
            data_atual = line
            i += 1
            continue

        # Identifica valores do Cora (ex: - R$ 1.789,68 ou + R$ 5.000,00)
        match_val = re.search(r'([+-]\s*R\$\s*[\d\.,]+)', line)
        if match_val and data_atual:
            valor = match_val.group(1)
            tipo_op = linhas[i - 1] if i - 1 >= 0 else "TRANSAÇÃO"

            # Descarta saldos informativos do dia
            if "Saldo do dia" in line or "Saldo do dia" in tipo_op:
                i += 1
                continue

            complemento = ""
            if i + 1 < len(linhas) and not re.match(r'^\d{2}/\d{2}/\d{4}$', linhas[i + 1]):
                complemento = linhas[i + 1]

            desc_final = tratar_descricao_com_cnpj(tipo_op, complemento)
            dados.append({"DATA": data_atual, "VALOR": valor, "DESCRIÇÃO": desc_final})

        i += 1

    return "CORA", agencia, conta, pd.DataFrame(dados)


def parse_xp(texto_completo):
    match_cc = re.search(r'Conta:\s*(\d+)', texto_completo)
    conta = match_cc.group(1) if match_cc else "000000"
    agencia = "0001"

    dados = []
    # Tratamento de linhas de lançamentos XP quando houverem movimentações
    linhas = texto_completo.split('\n')
    for line in linhas:
        match_linha = re.match(r'^(\d{2}/\d{2}/\d{4})\s+(.+?)\s+([-\+]?\s*\d+[\d\.,]*)$', line.strip())
        if match_linha:
            data, desc, valor = match_linha.groups()
            desc_final = tratar_descricao_com_cnpj(desc, "")
            dados.append({"DATA": data, "VALOR": valor, "DESCRIÇÃO": desc_final})

    return "XP", agencia, conta, pd.DataFrame(dados)


def extrair_dados_pdf(file_bytes):
    import pdfplumber

    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        texto_completo = ""
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                texto_completo += t + "\n"

    # Roteamento pelo identificador do banco no texto
    texto_upper = texto_completo.upper()
    if "ITAÚ" in texto_upper or "ITAU" in texto_upper:
        return parse_itau(texto_completo)
    elif "CORA" in texto_upper:
        return parse_cora(texto_completo)
    elif "XP INVESTIMENTOS" in texto_upper or "XPI" in texto_upper:
        return parse_xp(texto_completo)
    else:
        return "DESCONHECIDO", "0000", "00000", pd.DataFrame()


def pagina_extratos():
    st.title("📊 Extratos - Costa Verde")
    st.caption("Processador e Padronizador de Extratos Bancários")

    # Confere se as bibliotecas necessárias estão instaladas
    try:
        import requests  # noqa: F401
        import pdfplumber  # noqa: F401
    except ImportError:
        st.error(
            "Faltam bibliotecas para este sistema. Adicione `requests` e `pdfplumber` "
            "no arquivo requirements.txt e reinicie o programa."
        )
        return

    uploaded_files = st.file_uploader(
        "Arraste os extratos em PDF aqui",
        type=["pdf"],
        accept_multiple_files=True,
        key="upload_extratos",
    )

    if uploaded_files and st.button("🚀 Processar e Gerar Planilhas", key="processar_extratos"):
        arquivos_gerados = {}

        for file in uploaded_files:
            banco, agencia, conta, df = extrair_dados_pdf(file.read())
            nome_chave = f"{banco} - {agencia} - {conta}"

            if df.empty:
                # Cria DataFrame padrão vazio para contas sem movimentação no período
                df = pd.DataFrame(columns=["DATA", "VALOR", "DESCRIÇÃO"])
                st.info(f"ℹ️ {nome_chave}: Nenhuma movimentação no período. Gerando planilha vazia no layout.")

            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                df[["DATA", "VALOR", "DESCRIÇÃO"]].to_excel(writer, index=False, sheet_name="Extrato")

            arquivos_gerados[f"{nome_chave}.xlsx"] = buffer.getvalue()
            st.success(f"✅ Processado: **{nome_chave}.xlsx** ({len(df)} lançamentos)")

        if arquivos_gerados:
            st.markdown("---")
            st.subheader("📥 Download dos Arquivos Separados")

            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w") as zip_file:
                for nome_arq, dados_arq in arquivos_gerados.items():
                    zip_file.writestr(nome_arq, dados_arq)

            st.download_button(
                label="📦 Baixar Todos em ZIP",
                data=zip_buffer.getvalue(),
                file_name="Extratos_Formatados.zip",
                mime="application/zip",
            )
