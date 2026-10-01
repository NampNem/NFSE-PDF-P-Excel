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


def tratar_descricao_com_cnpj(tipo_transacao, texto_complementar=""):
    """Localiza o CNPJ/CPF ou limpa a descrição."""
    texto_completo = f"{tipo_transacao} {texto_complementar}".strip()
    
    # Busca CNPJ
    cnpjs = re.findall(r'\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b', texto_completo)
    if cnpjs:
        cnpj_limpo = re.sub(r'\D', '', cnpjs[0])
        nome_empresa = consultar_cnpj(cnpj_limpo)
        desc_limpa = re.sub(r'\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b', '', texto_completo).strip()
        desc_limpa = re.sub(r'\s+', ' ', desc_limpa)
        return f"{desc_limpa} - {nome_empresa}" if nome_empresa != cnpj_limpo else desc_limpa

    # Limpa caracteres de tabela '|' e espaços excessivos
    texto_limpo = re.sub(r'[\|]', '', texto_completo)
    texto_limpo = re.sub(r'\s+', ' ', texto_limpo).strip()
    return texto_limpo


def parse_itau(texto_completo):
    match_ag = re.search(r'Ag[êe]ncia\s+(\d+)', texto_completo, re.IGNORECASE)
    match_cc = re.search(r'Conta\s+([\d-]+)', texto_completo, re.IGNORECASE)
    agencia = match_ag.group(1) if match_ag else "6081"
    conta = match_cc.group(1) if match_cc else "0098779-1"

    linhas = [l.strip() for l in texto_completo.split('\n') if l.strip()]
    dados = []

    # Descarta APENAS linhas de posições e saldos de controle (não movimentações)
    saldos_informativos = [
        "SALDO ANTERIOR", "SALDO TOTAL", "SALDO MOVIMENTAÇÃO", 
        "SALDO BLOQUEADO", "SALDO DISPONÍVEL", "SALDO APLIC. AUT."
    ]

    for line in linhas:
        match_data = re.match(r'^(\d{2}/\d{2}/\d{4})\s+(.+)$', line)
        if match_data:
            data = match_data.group(1)
            resto = match_data.group(2).strip()

            # Descarta se for apenas informação de saldo acumulado
            if any(term in resto.upper() for term in saldos_informativos):
                continue

            # Extrai o valor do final da linha
            match_valor = re.search(r'([-\+]?\s*[\d\.]+\,\d{2})$', resto)
            if match_valor:
                valor_str = match_valor.group(1).replace(" ", "")
                desc = resto[:match_valor.start()].strip()
                desc_final = tratar_descricao_com_cnpj(desc)
                dados.append({"DATA": data, "VALOR": valor_str, "DESCRIÇÃO": desc_final})

    return "ITAU", agencia, conta, pd.DataFrame(dados)


def parse_cora(texto_completo):
    match_ag = re.search(r'Ag[êe]ncia:\s*([\d-]+)', texto_completo)
    match_cc = re.search(r'Conta:\s*([\d-]+)', texto_completo)
    agencia = match_ag.group(1) if match_ag else "0001"
    conta = match_cc.group(1) if match_cc else "3715423-5"

    dados = []
    linhas = [re.sub(r'^\s*\|\s*', '', l).strip() for l in texto_completo.split('\n') if l.strip()]

    i = 0
    data_atual = None
    while i < len(linhas):
        line = linhas[i]

        if re.match(r'^\d{2}/\d{2}/\d{4}$', line):
            data_atual = line
            i += 1
            continue

        match_val = re.search(r'([+-])\s*R\$\s*([\d\.,]+)', line)
        if match_val and data_atual:
            sinal = match_val.group(1)
            val_num = match_val.group(2)
            valor_str = f"-{val_num}" if sinal == "-" else val_num

            bloco_desc = []
            j = i - 1
            while j >= 0:
                prev_line = linhas[j]
                if re.match(r'^\d{2}/\d{2}/\d{4}$', prev_line) or "Saldo do dia" in prev_line or re.search(r'R\$\s*[\d\.,]+', prev_line):
                    break
                bloco_desc.insert(0, prev_line)
                j -= 1

            desc_bruta = " ".join(bloco_desc).strip()

            if "Saldo do dia" not in line and "Saldo do dia" not in desc_bruta:
                desc_final = tratar_descricao_com_cnpj(desc_bruta)
                dados.append({"DATA": data_atual, "VALOR": valor_str, "DESCRIÇÃO": desc_final})

        i += 1

    return "CORA", agencia, conta, pd.DataFrame(dados)


def parse_xp(texto_completo):
    match_cc = re.search(r'Conta:\s*(\d+)', texto_completo)
    conta = match_cc.group(1) if match_cc else "000000"
    agencia = "0001"

    dados = []
    linhas = [l.strip() for l in texto_completo.split('\n') if l.strip()]
    
    for line in linhas:
        partes = [p.strip() for p in line.split('|') if p.strip()]
        
        if len(partes) >= 3:
            if re.match(r'^\d{2}/\d{2}/\d{4}$', partes[0]):
                data = partes[0]
                
                if re.match(r'^\d{2}/\d{2}/\d{4}$', partes[1]):
                    desc = partes[2]
                    valor_raw = partes[3] if len(partes) > 3 else ""
                else:
                    desc = partes[1]
                    valor_raw = partes[2] if len(partes) > 2 else ""

                match_val = re.search(r'([-\+]?\s*R\$\s*[\d\.,]+|[-\+]?\d+[\d\.,]*)', valor_raw)
                if match_val:
                    val_str = match_val.group(1).replace("R$", "").replace(" ", "").strip()
                    desc_final = tratar_descricao_com_cnpj(desc)
                    dados.append({"DATA": data, "VALOR": val_str, "DESCRIÇÃO": desc_final})

    return "XP", agencia, conta, pd.DataFrame(dados)


def extrair_dados_pdf(file_bytes):
    import pdfplumber

    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        texto_completo = ""
        for page in pdf.pages:
            t = page.extract_text(layout=False)
            if t:
                texto_completo += t + "\n"

    texto_upper = texto_completo.upper()
    if "ITAÚ" in texto_upper or "ITAU" in texto_upper:
        return parse_itau(texto_completo)
    elif "CORA" in texto_upper:
        return parse_cora(texto_completo)
    elif "XP INVESTIMENTOS" in texto_upper or "XPI" in texto_upper:
        return parse_xp(texto_completo)
    else:
        return "DESCONHECIDO", "0000", "00000", pd.DataFrame()


def pagina_costa_verde_extratos():
    st.title("📊 Extratos - Costa Verde")
    st.caption("Processador e Padronizador de Extratos Bancários")

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
        key="upload_extratos_costa_verde",
    )

    if uploaded_files and st.button("🚀 Processar e Gerar Planilhas", key="processar_extratos_costa_verde"):
        arquivos_gerados = {}

        for file in uploaded_files:
            banco, agencia, conta, df = extrair_dados_pdf(file.read())
            nome_chave = f"{banco} - {agencia} - {conta}"

            if df.empty:
                df = pd.DataFrame(columns=["DATA", "VALOR", "DESCRIÇÃO"])
                st.info(f"ℹ️ {nome_chave}: Nenhuma movimentação capturada ou PDF sem lançamentos.")
            else:
                st.success(f"✅ Processado: **{nome_chave}.xlsx** ({len(df)} lançamentos)")

            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                df[["DATA", "VALOR", "DESCRIÇÃO"]].to_excel(writer, index=False, sheet_name="Extrato")

            arquivos_gerados[f"{nome_chave}.xlsx"] = buffer.getvalue()

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
                file_name="Costa_Verde_Extratos_Formatados.zip",
                mime="application/zip",
            )
