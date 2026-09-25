from collections import defaultdict
import io
import itertools
import os
import re
import shutil
import tempfile
import zipfile

from github import Github
import pandas as pd
import pdfplumber
import streamlit as st

# ============================================================
# CONFIGURAÇÃO DA PÁGINA
# ============================================================
st.set_page_config(
    page_title="Extrator de NFS-e", page_icon="📄", layout="wide"
)

st.title("📄 Extrator de NFS-e e Gerador Alterdata")
st.write(
    "Faça o upload dos arquivos **PDF** de NFS-e ou de arquivos **ZIP** contendo os PDFs para processar."
)

NOME_BANCO_DADOS = "banco_de_dados.xlsx"

# ============================================================
# TABELA OFICIAL LC 116 (DESCRIÇÃO DOS SERVIÇOS)
# ============================================================
TABELA_LC116 = {
    "0101": "Análise e desenvolvimento de sistemas",
    "0102": "Programação",
    "0103": "Processamento, armazenamento ou hospedagem de dados, textos, imagens, vídeos, páginas web, aplicativos e sistemas de informação",
    "0104": "Elaboração de programas de computadores, inclusive de jogos eletrônicos",
    "0105": "Licenciamento ou cessão de direito de uso de programas de computação",
    "0106": "Assessoria e consultoria em informática",
    "0107": "Suporte técnico em informática, inclusive instalação, configuração e manutenção de programas de computação e bancos de dados",
    "0108": "Configuração e manutenção de redes, de páginas e de esquemas de nutrição visual",
    "0109": "Disponibilização de conteúdos de áudio, vídeo, imagem e texto por meio da internet",
    "0701": "Engenharia, agronomia, agrimensura, arquitetura, geologia, urbanismo, paisagismo e congêneres",
    "0702": "Execução, por administração, empreitada ou subempreitada, de obras de construção civil, hidráulica ou elétrica",
    "0703": "Elaboração de planos diretores, estudos de viabilidade, projetos e especificações técnicas",
    "1001": "Agenciamento, corretagem ou intermediação de câmbio, de títulos e valores mobiliários",
    "1002": "Agenciamento, corretagem ou intermediação de títulos em geral, valores mobiliários e contratos quaisquer",
    "1005": "Agenciamento, corretagem ou intermediação de bens móveis ou imóveis",
    "1401": "Lubrificação, limpeza, lustração, revisão, carga e recarga, conserto, restauração, blindagem, manutenção e conservação de máquinas, veículos, aparelhos, equipamentos",
    "1701": "Assessoria ou consultoria de qualquer natureza",
    "1702": "Perícias, laudos, exames técnicos e análises técnicas",
    "1703": "Planejamento, organização e administração de feiras, exposições, congressos e congêneres",
    "1704": "Recrutamento, agenciamento, seleção e colocação de mão de obra",
    "1705": "Fornecimento de mão de obra, mesmo em caráter temporário",
    "1706": "Propaganda e publicidade, inclusive promoção de vendas, planejamento de campanhas",
    "1712": "Adestramento, treinamento, ensino e avaliação de qualquer natureza",
    "1719": "Contabilidade, inclusive serviços técnicos e auxiliares",
    "1720": "Consultoria e assessoria econômica ou financeira",
    "2401": "Serviços chaveiros, confecção de carimbos, placas, sinalização visual, banners, adesivos e congêneres",
}


def obter_descricao_servico(codigo):
    cod_limpo = re.sub(r"\D", "", str(codigo))
    if len(cod_limpo) >= 4:
        sub_cod = cod_limpo[:4]
        if sub_cod in TABELA_LC116:
            return TABELA_LC116[sub_cod]
    elif len(cod_limpo) >= 2:
        sub_cod = cod_limpo[:2]
        for k, v in TABELA_LC116.items():
            if k.startswith(sub_cod):
                return v
    return "Descrição de serviço não localizada na tabela resumida LC 116"


# ============================================================
# GERENCIAMENTO DO BANCO DE DADOS (GITHUB)
# ============================================================
def carregar_banco_dados_github():
    mapa_contas = {}
    if os.path.exists(NOME_BANCO_DADOS):
        try:
            if NOME_BANCO_DADOS.endswith(".csv"):
                df_bd = pd.read_csv(NOME_BANCO_DADOS, header=None)
            else:
                df_bd = pd.read_excel(NOME_BANCO_DADOS, header=None)

            for _, r in df_bd.iterrows():
                cod = tratar_codigo_tributacao(r[0])
                conta = str(r[1]).strip() if pd.notna(r[1]) else ""
                if cod:
                    mapa_contas[cod] = conta
        except Exception as e:
            st.error(f"Erro ao carregar o Banco de Dados: {e}")
    return mapa_contas


def salvar_banco_dados_github(mapa_contas):
    df_bd = pd.DataFrame(list(mapa_contas.items()))
    df_bd.to_excel(NOME_BANCO_DADOS, index=False, header=False)

    try:
        token = st.secrets.get("GITHUB_TOKEN")
        repo_name = st.secrets.get("REPO_NAME")

        if token and repo_name:
            g = Github(token)
            repo = g.get_repo(repo_name)

            with open(NOME_BANCO_DADOS, "rb") as f:
                novo_conteudo = f.read()

            try:
                contents = repo.get_contents(NOME_BANCO_DADOS)
                repo.update_file(
                    contents.path,
                    "Atualizando banco de dados de contas tributárias",
                    novo_conteudo,
                    contents.sha,
                )
            except:
                repo.create_file(
                    NOME_BANCO_DADOS,
                    "Criando banco de dados de contas tributárias",
                    novo_conteudo,
                )
            st.success("Novos códigos salvos permanentemente no GitHub!")
        else:
            st.warning(
                "Salvo apenas na sessão atual (Configure o GITHUB_TOKEN nos Secrets para salvar no GitHub)."
            )
    except Exception as e:
        st.error(f"Erro ao salvar no GitHub: {e}")


# ============================================================
# TRATAMENTOS E EXTRATOR
# ============================================================
def extract_rows(page, gap_threshold=10):
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    lines = defaultdict(list)
    for w in words:
        lines[round(w["top"])].append(w)

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
        merged.append(sorted(group, key=lambda w: w["x0"]))

    rows = []
    for group in merged:
        if not group:
            continue
        cols = []
        cur = [group[0]]
        for w in group[1:]:
            if w["x0"] - cur[-1]["x1"] > gap_threshold:
                cols.append(" ".join(x["text"] for x in cur))
                cur = [w]
            else:
                cur.append(w)
        cols.append(" ".join(x["text"] for x in cur))
        rows.append(cols)

    return rows


def find_row_index(rows, text, start=0):
    texto_procurado = text.strip().upper()
    for i in range(start, len(rows)):
        if not rows[i]:
            continue
        primeira_coluna = rows[i][0].strip().upper()
        if primeira_coluna == texto_procurado:
            return i
    return None


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


def converter_valor(valor):
    if valor is None:
        return None
    texto = str(valor).strip()
    if texto == "":
        return None
    texto = texto.upper().replace("R$", "").replace(" ", "")

    if texto in ["-", "—", "", "N/A", "NA"]:
        return None

    texto = re.sub(r"[^0-9,\.\-]", "", texto)
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
        f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    )


def tratar_codigo_tributacao(valor):
    if valor is None:
        return ""
    texto = str(valor).strip()
    if "/" in texto:
        texto = texto.split("/", 1)[0]
    return texto.strip()


def formatar_data_br(data_str):
    if not data_str:
        return ""
    try:
        dt = pd.to_datetime(data_str, dayfirst=True)
        return dt.strftime("%d/%m/%Y")
    except:
        return str(data_str).strip()


def validar_retencoes(bruto, liquido, impostos, tolerancia=0.02):
    resultado = {
        "Diferença Bruto-Líquido": None,
        "Retenções Validadas": "",
        "Valor Retenções Validadas": None,
        "Status Validação": "",
        "Combinações Encontradas": 0,
        "Impostos Retidos": set(),
    }

    if bruto is None or liquido is None:
        resultado["Status Validação"] = "SEM BRUTO OU LÍQUIDO"
        return resultado

    diferenca = round(bruto - liquido, 2)
    resultado["Diferença Bruto-Líquido"] = diferenca

    if abs(diferenca) <= tolerancia:
        resultado["Status Validação"] = "SEM RETENÇÃO PELO CÁLCULO"
        resultado["Retenções Validadas"] = "NENHUMA"
        resultado["Valor Retenções Validadas"] = 0.0
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
            soma = round(sum(impostos_validos[nome] for nome in combinacao), 2)
            erro = abs(soma - diferenca)
            if erro <= tolerancia:
                combinacoes.append((combinacao, soma, erro))

    resultado["Combinações Encontradas"] = len(combinacoes)

    if not combinacoes:
        resultado["Status Validação"] = "NÃO FOI POSSÍVEL FECHAR"
        resultado["Retenções Validadas"] = ""
        return resultado

    if len(combinacoes) == 1:
        melhor = combinacoes[0]
        nomes_melhor, soma_melhor, erro_melhor = (
            melhor[0],
            melhor[1],
            melhor[2],
        )
        resultado["Retenções Validadas"] = " + ".join(nomes_melhor)
        resultado["Valor Retenções Validadas"] = soma_melhor
        resultado["Impostos Retidos"] = set(nomes_melhor)

        if erro_melhor <= 0.01:
            resultado["Status Validação"] = "VALIDADO - FECHAMENTO EXATO"
        else:
            resultado["Status Validação"] = (
                "VALIDADO - DIFERENÇA DE CENTAVOS"
            )
        return resultado

    combinacoes.sort(key=lambda x: (x[2], len(x[0])))
    melhor = combinacoes[0]
    soma_melhor = melhor[1]

    resultado["Retenções Validadas"] = " / ".join(
        "+".join(nomes) for nomes, soma, erro in combinacoes
    )
    resultado["Valor Retenções Validadas"] = soma_melhor
    resultado["Status Validação"] = (
        "ATENÇÃO - MAIS DE UMA COMBINAÇÃO FECHA"
    )

    return resultado


def extrair_nfse(caminho_pdf):
    with pdfplumber.open(caminho_pdf) as pdf:
        if len(pdf.pages) == 0:
            raise Exception("PDF sem páginas.")
        rows = extract_rows(pdf.pages[0])

    prestador_i = find_row_index(rows, "PRESTADOR / FORNECEDOR DA NFS-e")
    tomador_i = find_row_index(rows, "TOMADOR / ADQUIRENTE DA OPERAÇÃO")
    if tomador_i is None:
        tomador_i = len(rows)

    numero_nfse = find_value(rows, "NÚMERO DA NFS-e")
    data_competencia = find_value(rows, "COMPETÊNCIA")
    nome_empresa = find_value(
        rows,
        "NOME / NOME EMPRESARIAL",
        prestador_i if prestador_i is not None else 0,
        tomador_i,
    )
    codigo_tributacao_original = find_value(
        rows, "CÓD. TRIBUTAÇÃO NACIONAL / MUNICIPAL"
    )
    codigo_tributacao = tratar_codigo_tributacao(codigo_tributacao_original)

    valor_servico = find_value(rows, "VALOR DO SERVIÇO")
    valor_pis = find_value(rows, "PIS - DÉBITO APURAÇÃO PRÓPRIA")
    valor_cofins = find_value(rows, "COFINS - DÉBITO APURAÇÃO PRÓPRIA")
    valor_csll = find_value(rows, "CONTRIBUIÇÕES SOCIAIS - RETIDAS")
    valor_irrf = find_value(rows, "IRRF")
    valor_inss = find_value(rows, "CONTRIBUIÇÃO PREVIDENCIÁRIA - RETIDA")
    valor_iss = find_value(rows, "ISSQN APURADO")
    valor_iss_retido = find_value(rows, "RETENÇÃO DO ISSQN")
    valor_liquido = find_value(rows, "VALOR LÍQUIDO DA NFS-e")

    bruto = converter_valor(valor_servico)
    liquido = converter_valor(valor_liquido)
    pis = converter_valor(valor_pis)
    cofins = converter_valor(valor_cofins)
    csll = converter_valor(valor_csll)
    irrf = converter_valor(valor_irrf)
    inss = converter_valor(valor_inss)
    iss = converter_valor(valor_iss)

    impostos = {
        "PIS": pis,
        "COFINS": cofins,
        "CSLL": csll,
        "IRRF": irrf,
        "INSS": inss,
        "ISS": iss,
    }

    validacao = validar_retencoes(bruto, liquido, impostos)
    impostos_retidos = validacao["Impostos Retidos"]

    if validacao["Combinações Encontradas"] == 1:
        pis_status = (
            "RETIDO" if "PIS" in impostos_retidos else "NÃO RETIDO"
        )
        cofins_status = (
            "RETIDO" if "COFINS" in impostos_retidos else "NÃO RETIDO"
        )
        csll_status = (
            "RETIDO" if "CSLL" in impostos_retidos else "NÃO RETIDO"
        )
        irrf_status = (
            "RETIDO" if "IRRF" in impostos_retidos else "NÃO RETIDO"
        )
        inss_status = (
            "RETIDO" if "INSS" in impostos_retidos else "NÃO RETIDO"
        )
        iss_status = (
            "RETIDO" if "ISS" in impostos_retidos else "NÃO RETIDO"
        )
    else:
        pis_status = "NÃO VALIDADO"
        cofins_status = "NÃO VALIDADO"
        csll_status = "NÃO VALIDADO"
        irrf_status = "NÃO VALIDADO"
        inss_status = "NÃO VALIDADO"
        iss_status = "NÃO VALIDADO"

    return {
        "Número da NFS-e": numero_nfse,
        "Data Competência": data_competencia,
        "Nome da Empresa": nome_empresa,
        "Código Tributação": codigo_tributacao,
        "Valor do Serviço": valor_servico,
        "Valor PIS": valor_pis,
        "PIS Retido?": pis_status,
        "Valor COFINS": valor_cofins,
        "COFINS Retido?": cofins_status,
        "CSLL (Retida)": valor_csll,
        "CSLL Retida?": csll_status,
        "IRRF": valor_irrf,
        "IRRF Retido?": irrf_status,
        "INSS (Previdenciária)": valor_inss,
        "INSS Retido?": inss_status,
        "ISS": valor_iss,
        "ISS Retenção": valor_iss_retido,
        "ISS Retido?": iss_status,
        "Valor Líquido": valor_liquido,
        "Diferença Bruto-Líquido": formatar_valor(
            validacao["Diferença Bruto-Líquido"]
        ),
        "Retenções Validadas": validacao["Retenções Validadas"],
        "Valor Retenções Validadas": formatar_valor(
            validacao["Valor Retenções Validadas"]
        ),
        "Status Validação": validacao["Status Validação"],
        "Combinações Encontradas": validacao["Combinações Encontradas"],
    }


# ============================================================
# GERAR ABA ALTERDATA
# ============================================================
def gerar_aba_alterdata(df_extrato, mapa_contas):
    linhas_alterdata = []

    for _, row in df_extrato.iterrows():
        num_nota = str(row.get("Número da NFS-e", "") or "").strip()
        data_comp = formatar_data_br(row.get("Data Competência", ""))
        nome_empresa = str(row.get("Nome da Empresa", "") or "").strip()
        cod_trib = str(row.get("Código Tributação", "") or "").strip()

        conta_debito_bd = mapa_contas.get(cod_trib, "2135")

        desc_padrao = f"NF - {num_nota} {nome_empresa}".strip()

        val_bruto = converter_valor(row.get("Valor do Serviço")) or 0.0
        val_liquido = converter_valor(row.get("Valor Líquido")) or 0.0

        val_pis = converter_valor(row.get("Valor PIS")) or 0.0
        val_cofins = converter_valor(row.get("Valor COFINS")) or 0.0
        val_csll = converter_valor(row.get("CSLL (Retida)")) or 0.0
        val_irrf = converter_valor(row.get("IRRF")) or 0.0
        val_inss = converter_valor(row.get("INSS (Previdenciária)")) or 0.0
        val_iss = (
            converter_valor(row.get("ISS Retenção"))
            or converter_valor(row.get("ISS"))
            or 0.0
        )

        try:
            comb_encontradas = int(row.get("Combinações Encontradas", 0) or 0)
        except:
            comb_encontradas = 0

        if comb_encontradas == 0:
            linhas_alterdata.append({
                "Data": data_comp,
                "debito": conta_debito_bd,
                "credito": "708",
                "valor": val_bruto,
                "documento": num_nota,
                "historico": 99,
                "descrição": desc_padrao,
            })
        else:
            linhas_alterdata.append({
                "Data": data_comp,
                "debito": conta_debito_bd,
                "credito": "",
                "valor": val_bruto,
                "documento": num_nota,
                "historico": 99,
                "descrição": desc_padrao,
            })

            soma_pcc = 0.0
            pcc_retidos = []
            if str(row.get("PIS Retido?", "")).strip().upper() == "RETIDO":
                soma_pcc += val_pis
                pcc_retidos.append("PIS")
            if str(row.get("COFINS Retido?", "")).strip().upper() == "RETIDO":
                soma_pcc += val_cofins
                pcc_retidos.append("COFINS")
            if str(row.get("CSLL Retida?", "")).strip().upper() == "RETIDO":
                soma_pcc += val_csll
                pcc_retidos.append("CSLL")

            if soma_pcc > 0:
                nome_pcc = "/".join(pcc_retidos)
                desc_pcc = (
                    f"Retenção {nome_pcc} s/ NF - {num_nota} {nome_empresa}"
                )
                linhas_alterdata.append({
                    "Data": data_comp,
                    "debito": "",
                    "credito": "236",
                    "valor": soma_pcc,
                    "documento": num_nota,
                    "historico": 99,
                    "descrição": desc_pcc,
                })

            if (
                str(row.get("IRRF Retido?", "")).strip().upper() == "RETIDO"
                and val_irrf > 0
            ):
                desc_irrf = (
                    f"Retenção IRRF s/ NF - {num_nota} {nome_empresa}"
                )
                linhas_alterdata.append({
                    "Data": data_comp,
                    "debito": "",
                    "credito": "763",
                    "valor": val_irrf,
                    "documento": num_nota,
                    "historico": 99,
                    "descrição": desc_irrf,
                })

            if (
                str(row.get("INSS Retido?", "")).strip().upper() == "RETIDO"
                and val_inss > 0
            ):
                desc_inss = (
                    f"Retenção INSS s/ NF - {num_nota} {nome_empresa}"
                )
                linhas_alterdata.append({
                    "Data": data_comp,
                    "debito": "",
                    "credito": "834",
                    "valor": val_inss,
                    "documento": num_nota,
                    "historico": 99,
                    "descrição": desc_inss,
                })

            if (
                str(row.get("ISS Retido?", "")).strip().upper() == "RETIDO"
                and val_iss > 0
            ):
                desc_iss = (
                    f"Retenção ISS s/ NF - {num_nota} {nome_empresa}"
                )
                linhas_alterdata.append({
                    "Data": data_comp,
                    "debito": "",
                    "credito": "3332",
                    "valor": val_iss,
                    "documento": num_nota,
                    "historico": 99,
                    "descrição": desc_iss,
                })

            linhas_alterdata.append({
                "Data": data_comp,
                "debito": "",
                "credito": "708",
                "valor": val_liquido,
                "documento": num_nota,
                "historico": 99,
                "descrição": desc_padrao,
            })

    df_alt = pd.DataFrame(linhas_alterdata)
    # Garante que a coluna Data permaneça como texto string sem conversão nativa
    df_alt["Data"] = df_alt["Data"].astype(str)
    return df_alt


# ============================================================
# INTERFACE STREAMLIT
# ============================================================

uploaded_files = st.file_uploader(
    "Arraste ou selecione os arquivos PDF ou ZIP aqui",
    type=["pdf", "zip"],
    accept_multiple_files=True,
)

if uploaded_files:
    if st.button("🚀 Processar NFS-e"):

        temp_dir = tempfile.mkdtemp()
        pdfs_para_processar = []

        for uploaded_file in uploaded_files:
            nome_arquivo = uploaded_file.name
            extensao = os.path.splitext(nome_arquivo)[1].lower()

            if extensao == ".pdf":
                caminho_pdf = os.path.join(temp_dir, nome_arquivo)
                with open(caminho_pdf, "wb") as f:
                    f.write(uploaded_file.getbuffer())
                pdfs_para_processar.append(caminho_pdf)

            elif extensao == ".zip":
                caminho_zip = os.path.join(temp_dir, nome_arquivo)
                with open(caminho_zip, "wb") as f:
                    f.write(uploaded_file.getbuffer())

                pasta_zip = os.path.join(
                    temp_dir, os.path.splitext(nome_arquivo)[0]
                )
                os.makedirs(pasta_zip, exist_ok=True)

                try:
                    with zipfile.ZipFile(caminho_zip, "r") as zip_ref:
                        zip_ref.extractall(pasta_zip)

                    for raiz, _, arquivos in os.walk(pasta_zip):
                        for arq in arquivos:
                            if arq.lower().endswith(".pdf"):
                                pdfs_para_processar.append(
                                    os.path.join(raiz, arq)
                                )
                except Exception as e:
                    st.error(f"Erro ao descompactar {nome_arquivo}: {e}")

        if pdfs_para_processar:
            registros = []
            progress_bar = st.progress(0)
            status_text = st.empty()

            for i, caminho_pdf in enumerate(pdfs_para_processar):
                nome_pdf = os.path.basename(caminho_pdf)
                status_text.text(
                    f"Processando [{i+1}/{len(pdfs_para_processar)}]: {nome_pdf}"
                )
                try:
                    registros.append(extrair_nfse(caminho_pdf))
                except:
                    pass
                progress_bar.progress((i + 1) / len(pdfs_para_processar))

            status_text.text("Extração concluída!")

            df = pd.DataFrame(registros)
            st.session_state["df_extrato"] = df

            mapa_contas = carregar_banco_dados_github()
            codigos_na_nf = set(df["Código Tributação"].dropna().unique())
            ausentes = [c for c in codigos_na_nf if c and c not in mapa_contas]

            st.session_state["codigos_ausentes"] = ausentes

        shutil.rmtree(temp_dir, ignore_errors=True)

# EXIBIÇÃO DE RESULTADOS E MAPPING DE CÓDIGOS
if (
    "df_extrato" in st.session_state
    and st.session_state["df_extrato"] is not None
):
    mapa_contas = carregar_banco_dados_github()
    df = st.session_state["df_extrato"]
    ausentes = st.session_state.get("codigos_ausentes", [])

    if ausentes:
        st.warning(
            "⚠️ Foram encontrados Códigos de Tributação não cadastrados no Banco de Dados!"
        )

        with st.form("form_novos_codigos"):
            novos_cadastros = {}
            for cod in ausentes:
                desc_lc116 = obter_descricao_servico(cod)

                st.markdown(f"### 📌 Código: `{cod}`")
                st.info(f"📄 **Descrição Oficial (LC 116):** {desc_lc116}")

                nova_conta = st.text_input(
                    f"Informe a conta débito para o código {cod} (deixe em branco para usar 2135):",
                    key=f"input_{cod}",
                )
                novos_cadastros[cod] = nova_conta
                st.divider()

            salvar_btn = st.form_submit_button("💾 Confirmar e Processar")

        if salvar_btn:
            for cod, conta in novos_cadastros.items():
                if conta.strip():
                    mapa_contas[cod] = conta.strip()
                else:
                    mapa_contas[cod] = "2135"

            salvar_banco_dados_github(mapa_contas)
            st.session_state["codigos_ausentes"] = []
            st.success("Contas atualizadas com sucesso!")

    if not st.session_state.get("codigos_ausentes"):
        df_alterdata = gerar_aba_alterdata(df, mapa_contas)

        st.subheader("📊 Prévia - Aba Alterdata")
        st.dataframe(df_alterdata, use_container_width=True)

        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            # Exporta a coluna Data como texto literal
            df_alterdata.to_excel(writer, index=False, sheet_name="Alterdata")
            df.to_excel(writer, index=False, sheet_name="NFS-e Extraídas")

        st.download_button(
            label="📥 Baixar Planilha para Importação Alterdata (.xlsx)",
            data=buffer.getvalue(),
            file_name="importacao_alterdata.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
