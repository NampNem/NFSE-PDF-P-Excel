from collections import defaultdict
import io
import itertools
import os
import re
import shutil
import tempfile
import zipfile

import pandas as pd
import pdfplumber
import streamlit as st

# ============================================================
# CONFIGURAÇÃO DA PÁGINA
# ============================================================
st.set_page_config(
    page_title="Extrator de NFS-e",
    page_icon="📄",
    layout="wide",
)

st.title("📄 Extrator de NFS-e e Validador de Retenções")
st.write(
    "Faça o upload dos seus arquivos **PDF** de NFS-e ou de arquivos **ZIP** contendo os PDFs para processar."
)


# ============================================================
# RECONSTRUIR LINHAS DO PDF
# ============================================================
def extract_rows(page, gap_threshold=10):
    words = page.extract_words(
        use_text_flow=False, keep_blank_chars=False
    )

    lines = defaultdict(list)
    for w in words:
        lines[round(w['top'])].append(w)

    sorted_tops = sorted(lines.keys())
    merged = []
    used = set()

    for t in sorted_tops:
        if t in used:
            continue
        group = list(lines[t])
        for t2 in sorted_tops:
            if t2 != t and abs(t2 - t) <= 2 and t2 not in used:
                group.extend(lines[t2])
                used.add(t2)
        used.add(t)
        merged.append(sorted(group, key=lambda w: w['x0']))

    rows = []
    for group in merged:
        if not group:
            continue
        cols = []
        cur = [group[0]]
        for w in group[1:]:
            if w['x0'] - cur[-1]['x1'] > gap_threshold:
                cols.append(' '.join(x['text'] for x in cur))
                cur = [w]
            else:
                cur.append(w)
        cols.append(' '.join(x['text'] for x in cur))
        rows.append(cols)

    return rows


# ============================================================
# LOCALIZAR LINHA
# ============================================================
def find_row_index(rows, text, start=0):
    texto_procurado = text.strip().upper()
    for i in range(start, len(rows)):
        if not rows[i]:
            continue
        primeira_coluna = rows[i][0].strip().upper()
        if primeira_coluna == texto_procurado:
            return i
    return None


# ============================================================
# LOCALIZAR VALOR
# ============================================================
def find_value(rows, label, start=0, end=None):
    if end is None:
        end = len(rows)
    label_upper = label.strip().upper()

    for i in range(start, end):
        row = rows[i]
        for idx, cell in enumerate(row):
            if cell.strip().upper() == label_upper:
                if i + 1 < len(rows):
                    next_row = rows[i + 1]
                    if idx < len(next_row):
                        return next_row[idx].strip()
    return None


# ============================================================
# NORMALIZAÇÃO E TRATAMENTOS DE VALOR
# ============================================================
def normalizar(texto):
    if texto is None:
        return ""
    texto = str(texto).upper()
    texto = re.sub(r'\s+', ' ', texto)
    return texto.strip()


def campo_vazio(valor):
    if valor is None:
        return True
    texto = normalizar(valor)
    return texto in [
        "",
        "-",
        "—",
        "NÃO INFORMADO",
        "NAO INFORMADO",
    ]


def converter_valor(valor):
    if valor is None:
        return None
    texto = str(valor).strip()
    if texto == "":
        return None
    texto = texto.upper().replace("R$", "").replace(" ", "")

    if texto in ["-", "—", "", "N/A", "NA"]:
        return None

    texto = re.sub(r'[^0-9,\.\-]', '', texto)
    if not texto:
        return None

    try:
        if "," in texto:
            texto = texto.replace(".", "").replace(",", ".")
        return float(texto)
    except:
        return None


def formatar_valor(valor):
    if valor is None:
        return ""
    return (
        f"R$ {valor:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def tratar_codigo_tributacao(valor):
    if valor is None:
        return ""
    texto = str(valor).strip()
    if "/" in texto:
        texto = texto.split("/", 1)[0]
    return texto.strip()


# ============================================================
# VALIDAR RETENÇÕES
# ============================================================
def validar_retencoes(bruto, liquido, impostos, tolerancia=0.02):
    resultado = {
        'Diferença Bruto-Líquido': None,
        'Retenções Validadas': '',
        'Valor Retenções Validadas': None,
        'Status Validação': '',
        'Combinações Encontradas': 0,
        'Impostos Retidos': set(),
    }

    if bruto is None or liquido is None:
        resultado['Status Validação'] = 'SEM BRUTO OU LÍQUIDO'
        return resultado

    diferenca = round(bruto - liquido, 2)
    resultado['Diferença Bruto-Líquido'] = diferenca

    if abs(diferenca) <= tolerancia:
        resultado['Status Validação'] = 'SEM RETENÇÃO PELO CÁLCULO'
        resultado['Retenções Validadas'] = 'NENHUMA'
        resultado['Valor Retenções Validadas'] = 0.0
        return resultado

    impostos_validos = {}
    for nome, valor in impostos.items():
        if valor is None:
            continue
        valor = round(float(valor), 2)
        if valor > 0:
            impostos_validos[nome] = valor

    nomes = list(impostos_validos.keys())
    combinacoes = []

    for tamanho in range(1, len(nomes) + 1):
        for combinacao in itertools.combinations(nomes, tamanho):
            soma = round(
                sum(impostos_validos[nome] for nome in combinacao), 2
            )
            erro = abs(soma - diferenca)
            if erro <= tolerancia:
                combinacoes.append((combinacao, soma, erro))

    resultado['Combinações Encontradas'] = len(combinacoes)

    if not combinacoes:
        resultado['Status Validação'] = 'NÃO FOI POSSÍVEL FECHAR'
        resultado['Retenções Validadas'] = ''
        return resultado

    if len(combinacoes) == 1:
        melhor = combinacoes[0]
        nomes_melhor, soma_melhor, erro_melhor = (
            melhor[0],
            melhor[1],
            melhor[2],
        )
        resultado['Retenções Validadas'] = " + ".join(nomes_melhor)
        resultado['Valor Retenções Validadas'] = soma_melhor
        resultado['Impostos Retidos'] = set(nomes_melhor)

        if erro_melhor <= 0.01:
            resultado['Status Validação'] = 'VALIDADO - FECHAMENTO EXATO'
        else:
            resultado['Status Validação'] = (
                'VALIDADO - DIFERENÇA DE CENTAVOS'
            )
        return resultado

    combinacoes.sort(key=lambda x: (x[2], len(x[0])))
    melhor = combinacoes[0]
    soma_melhor = melhor[1]

    resultado['Retenções Validadas'] = " / ".join(
        "+".join(nomes) for nomes, soma, erro in combinacoes
    )
    resultado['Valor Retenções Validadas'] = soma_melhor
    resultado['Status Validação'] = (
        'ATENÇÃO - MAIS DE UMA COMBINAÇÃO FECHA'
    )

    return resultado


# ============================================================
# EXTRAIR NFS-e
# ============================================================
def extrair_nfse(caminho_pdf):
    with pdfplumber.open(caminho_pdf) as pdf:
        if len(pdf.pages) == 0:
            raise Exception("PDF sem páginas.")
        rows = extract_rows(pdf.pages[0])

    prestador_i = find_row_index(
        rows, 'PRESTADOR / FORNECEDOR DA NFS-e'
    )
    tomador_i = find_row_index(
        rows, 'TOMADOR / ADQUIRENTE DA OPERAÇÃO'
    )
    if tomador_i is None:
        tomador_i = len(rows)

    numero_nfse = find_value(rows, 'NÚMERO DA NFS-e')
    data_competencia = find_value(rows, 'COMPETÊNCIA')
    nome_empresa = find_value(
        rows,
        'NOME / NOME EMPRESARIAL',
        prestador_i if prestador_i is not None else 0,
        tomador_i,
    )
    codigo_tributacao_original = find_value(
        rows, 'CÓD. TRIBUTAÇÃO NACIONAL / MUNICIPAL'
    )
    codigo_tributacao = tratar_codigo_tributacao(
        codigo_tributacao_original
    )

    valor_servico = find_value(rows, 'VALOR DO SERVIÇO')
    valor_pis = find_value(rows, 'PIS - DÉBITO APURAÇÃO PRÓPRIA')
    valor_cofins = find_value(rows, 'COFINS - DÉBITO APURAÇÃO PRÓPRIA')
    valor_csll = find_value(rows, 'CONTRIBUIÇÕES SOCIAIS - RETIDAS')
    valor_irrf = find_value(rows, 'IRRF')
    valor_inss = find_value(rows, 'CONTRIBUIÇÃO PREVIDENCIÁRIA - RETIDA')
    valor_iss = find_value(rows, 'ISSQN APURADO')
    valor_iss_retido = find_value(rows, 'RETENÇÃO DO ISSQN')
    valor_liquido = find_value(rows, 'VALOR LÍQUIDO DA NFS-e')

    bruto = converter_valor(valor_servico)
    liquido = converter_valor(valor_liquido)
    pis = converter_valor(valor_pis)
    cofins = converter_valor(valor_cofins)
    csll = converter_valor(valor_csll)
    irrf = converter_valor(valor_irrf)
    inss = converter_valor(valor_inss)
    iss = converter_valor(valor_iss)

    impostos = {
        'PIS': pis,
        'COFINS': cofins,
        'CSLL': csll,
        'IRRF': irrf,
        'INSS': inss,
        'ISS': iss,
    }

    validacao = validar_retencoes(bruto, liquido, impostos)
    impostos_retidos = validacao['Impostos Retidos']

    if validacao['Combinações Encontradas'] == 1:
        pis_status = (
            'RETIDO' if 'PIS' in impostos_retidos else 'NÃO RETIDO'
        )
        cofins_status = (
            'RETIDO' if 'COFINS' in impostos_retidos else 'NÃO RETIDO'
        )
        csll_status = (
            'RETIDO' if 'CSLL' in impostos_retidos else 'NÃO RETIDO'
        )
        irrf_status = (
            'RETIDO' if 'IRRF' in impostos_retidos else 'NÃO RETIDO'
        )
        inss_status = (
            'RETIDO' if 'INSS' in impostos_retidos else 'NÃO RETIDO'
        )
        iss_status = (
            'RETIDO' if 'ISS' in impostos_retidos else 'NÃO RETIDO'
        )
    else:
        pis_status = 'NÃO VALIDADO'
        cofins_status = 'NÃO VALIDADO'
        csll_status = 'NÃO VALIDADO'
        irrf_status = 'NÃO VALIDADO'
        inss_status = 'NÃO VALIDADO'
        iss_status = 'NÃO VALIDADO'

    return {
        'Número da NFS-e': numero_nfse,
        'Data Competência': data_competencia,
        'Nome da Empresa': nome_empresa,
        'Código Tributação': codigo_tributacao,
        'Valor do Serviço': valor_servico,
        'Valor PIS': valor_pis,
        'PIS Retido?': pis_status,
        'Valor COFINS': valor_cofins,
        'COFINS Retido?': cofins_status,
        'CSLL (Retida)': valor_csll,
        'CSLL Retida?': csll_status,
        'IRRF': valor_irrf,
        'IRRF Retido?': irrf_status,
        'INSS (Previdenciária)': valor_inss,
        'INSS Retido?': inss_status,
        'ISS': valor_iss,
        'ISS Retenção': valor_iss_retido,
        'ISS Retido?': iss_status,
        'Valor Líquido': valor_liquido,
        'Diferença Bruto-Líquido': formatar_valor(
            validacao['Diferença Bruto-Líquido']
        ),
        'Retenções Validadas': validacao['Retenções Validadas'],
        'Valor Retenções Validadas': formatar_valor(
            validacao['Valor Retenções Validadas']
        ),
        'Status Validação': validacao['Status Validação'],
        'Combinações Encontradas': validacao['Combinações Encontradas'],
    }


# ============================================================
# INTERFACE STREAMLIT (UPLOAD E PROCESSAMENTO)
# ============================================================

uploaded_files = st.file_uploader(
    "Selecione os arquivos PDF e/ou ZIP",
    type=["pdf", "zip"],
    accept_multiple_files=True,
)

if uploaded_files:
    if st.button("🚀 Processar NFS-e"):

        # Cria diretório temporário isolado
        temp_dir = tempfile.mkdtemp()
        pdfs_para_processar = []

        st.info("Organizando e extraindo arquivos...")

        for uploaded_file in uploaded_files:
            nome_arquivo = uploaded_file.name
            extensao = os.path.splitext(nome_arquivo)[1].lower()

            if extensao == '.pdf':
                caminho_pdf = os.path.join(temp_dir, nome_arquivo)
                with open(caminho_pdf, "wb") as f:
                    f.write(uploaded_file.getbuffer())
                pdfs_para_processar.append(caminho_pdf)

            elif extensao == '.zip':
                caminho_zip = os.path.join(temp_dir, nome_arquivo)
                with open(caminho_zip, "wb") as f:
                    f.write(uploaded_file.getbuffer())

                pasta_zip = os.path.join(
                    temp_dir, os.path.splitext(nome_arquivo)[0]
                )
                os.makedirs(pasta_zip, exist_ok=True)

                try:
                    with zipfile.ZipFile(caminho_zip, 'r') as zip_ref:
                        zip_ref.extractall(pasta_zip)

                    for raiz, _, arquivos in os.walk(pasta_zip):
                        for arq in arquivos:
                            if arq.lower().endswith('.pdf'):
                                pdfs_para_processar.append(
                                    os.path.join(raiz, arq)
                                )
                except Exception as e:
                    st.error(f"Erro ao descompactar {nome_arquivo}: {e}")

        if not pdfs_para_processar:
            st.warning("Nenhum arquivo PDF válido foi encontrado.")
        else:
            st.success(
                f"Encontrados {len(pdfs_para_processar)} arquivo(s) PDF para processamento."
            )

            registros = []
            erros = []

            # Barra de progresso visual no site
            progress_bar = st.progress(0)
            status_text = st.empty()

            for i, caminho_pdf in enumerate(pdfs_para_processar):
                nome_pdf = os.path.basename(caminho_pdf)
                status_text.text(
                    f"Processando [{i+1}/{len(pdfs_para_processar)}]: {nome_pdf}"
                )

                try:
                    registro = extrair_nfse(caminho_pdf)
                    registros.append(registro)
                except Exception as e:
                    erros.append((nome_pdf, str(e)))

                progress_bar.progress((i + 1) / len(pdfs_para_processar))

            status_text.text("Processamento concluído!")

            # Montagem das colunas do DataFrame
            colunas = [
                'Número da NFS-e',
                'Data Competência',
                'Nome da Empresa',
                'Código Tributação',
                'Valor do Serviço',
                'Valor PIS',
                'PIS Retido?',
                'Valor COFINS',
                'COFINS Retido?',
                'CSLL (Retida)',
                'CSLL Retida?',
                'IRRF',
                'IRRF Retido?',
                'INSS (Previdenciária)',
                'INSS Retido?',
                'ISS',
                'ISS Retenção',
                'ISS Retido?',
                'Valor Líquido',
                'Diferença Bruto-Líquido',
                'Retenções Validadas',
                'Valor Retenções Validadas',
                'Status Validação',
                'Combinações Encontradas',
            ]

            if registros:
                df = pd.DataFrame(registros)
                for c in colunas:
                    if c not in df.columns:
                        df[c] = ""
                df = df[colunas]
            else:
                df = pd.DataFrame(columns=colunas)

            # Exibe os resultados no site
            st.subheader("📊 Resultado do Processamento")
            st.write(
                f"**NFS-e Processadas com Sucesso:** {len(registros)} | **Erros:** {len(erros)}"
            )

            st.dataframe(df, use_container_width=True)

            if erros:
                with st.expander("⚠️ Ver Arquivos que Apresentaram Erro"):
                    for arq_erro, msg_erro in erros:
                        st.write(f"• **{arq_erro}**: {msg_erro}")

            # Botão de download da planilha final gerada
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df.to_excel(
                    writer, index=False, sheet_name='NFS-e Extraídas'
                )

            st.download_button(
                label="📥 Baixar Planilha Excel (.xlsx)",
                data=buffer.getvalue(),
                file_name="nfse_extraidas.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

        # Limpa arquivos temporários ao final
        shutil.rmtree(temp_dir, ignore_errors=True)
