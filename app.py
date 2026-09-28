import io
import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from github import Github
import pandas as pd
import streamlit as st

# ============================================================
# CONFIGURAÇÃO DA PÁGINA
# ============================================================
st.set_page_config(
    page_title="Extrator de NFS-e (XML)", page_icon="📄", layout="wide"
)

st.title("📄 Extrator de NFS-e (XML) e Gerador Alterdata")
st.write(
    "Faça o upload dos arquivos **XML** (NFS-e ou Eventos de Cancelamento) ou de arquivos **ZIP** contendo os XMLs."
)

NOME_BANCO_DADOS = "banco_de_dados.xlsx"


# ============================================================
# FUNÇÕES UTILITÁRIAS E AUXILIARES
# ============================================================
def extrair_codigo_do_banco(valor):
    if valor is None:
        return ""
    texto = str(valor).strip()
    m = re.match(r"^(\d{2,8})\s*-\s*.+", texto)
    if m:
        return m.group(1)
    return texto


def extrair_descricao_do_banco(valor):
    if valor is None:
        return ""
    texto = str(valor).strip()
    m = re.match(r"^\d{2,8}\s*-\s*(.+)$", texto)
    if m:
        return m.group(1).strip()
    return ""


def montar_celula_banco(codigo, descricao):
    codigo = str(codigo).strip()
    descricao = str(descricao).strip() if descricao else ""
    if descricao:
        return f"{codigo} - {descricao}"
    return codigo


def converter_valor(valor):
    if valor is None:
        return None
    try:
        return float(valor)
    except:
        return None


def formatar_valor(valor):
    if valor is None:
        return ""
    return (
        f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    )


def converter_data_obj(data_str):
    if not data_str:
        return None
    try:
        dt = pd.to_datetime(data_str, dayfirst=True)
        return dt.date()
    except:
        return None


# ============================================================
# GERENCIAMENTO DO BANCO DE DADOS (GITHUB)
# ============================================================
def carregar_banco_dados_github():
    mapa = {}
    if os.path.exists(NOME_BANCO_DADOS):
        try:
            if NOME_BANCO_DADOS.endswith(".csv"):
                df_bd = pd.read_csv(NOME_BANCO_DADOS, header=None)
            else:
                df_bd = pd.read_excel(NOME_BANCO_DADOS, header=None)

            for _, r in df_bd.iterrows():
                cod = extrair_codigo_do_banco(r[0])
                descricao = extrair_descricao_do_banco(r[0])
                conta = str(r[1]).strip() if pd.notna(r[1]) else ""
                if cod:
                    mapa[cod] = {"descricao": descricao, "conta": conta}
        except Exception as e:
            st.error(f"Erro ao carregar o Banco de Dados: {e}")
    return mapa


def salvar_banco_dados_github(mapa):
    linhas = [
        (montar_celula_banco(cod, dados.get("descricao", "")), dados.get("conta", ""))
        for cod, dados in mapa.items()
    ]
    df_bd = pd.DataFrame(linhas)
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
            st.success("Banco de dados salvo permanentemente no GitHub!")
        else:
            st.warning(
                "Salvo apenas na sessão atual (Configure o GITHUB_TOKEN nos Secrets para salvar no GitHub)."
            )
    except Exception as e:
        st.error(f"Erro ao salvar no GitHub: {e}")


# ============================================================
# GERENCIADOR NA BARRA LATERAL (SIDEBAR)
# ============================================================
with st.sidebar:
    st.header("⚙️ Gerenciar Banco de Dados")
    st.write(
        "Modifique, adicione ou remova códigos de tributação e suas contas contábeis vinculadas."
    )

    mapa_atual = carregar_banco_dados_github()
    df_gerenciador = pd.DataFrame(
        [
            {
                "Código Tributação": cod,
                "Descrição (Operação)": dados.get("descricao", ""),
                "Conta Débito": dados.get("conta", ""),
            }
            for cod, dados in mapa_atual.items()
        ],
        columns=["Código Tributação", "Descrição (Operação)", "Conta Débito"],
    )

    df_editado = st.data_editor(
        df_gerenciador,
        num_rows="dynamic",
        use_container_width=True,
        key="editor_bd",
    )

    if st.button("💾 Salvar Alterações no Banco de Dados"):
        novo_mapa = {}
        for _, row in df_editado.iterrows():
            cod = extrair_codigo_do_banco(str(row["Código Tributação"]))
            descricao = (
                str(row["Descrição (Operação)"]).strip()
                if pd.notna(row["Descrição (Operação)"])
                else ""
            )
            conta = (
                str(row["Conta Débito"]).strip()
                if pd.notna(row["Conta Débito"])
                else ""
            )
            if cod:
                novo_mapa[cod] = {"descricao": descricao, "conta": conta}

        salvar_banco_dados_github(novo_mapa)
        st.rerun()


# ============================================================
# PARSER EXTRATOR DE XML (NFS-E E EVENTOS)
# ============================================================
def extrair_xml(caminho_ou_conteudo):
    """Identifica se o XML é uma NFS-e ou um Evento de Cancelamento/Substituição."""
    if isinstance(caminho_ou_conteudo, bytes):
        root = ET.fromstring(caminho_ou_conteudo)
    elif isinstance(caminho_ou_conteudo, str) and caminho_ou_conteudo.endswith(".xml"):
        tree = ET.parse(caminho_ou_conteudo)
        root = tree.getroot()
    else:
        root = ET.fromstring(caminho_ou_conteudo)

    def find_tag(element, tag_name):
        if element is None:
            return None
        for child in element.iter():
            if child.tag.endswith(tag_name):
                return child
        return None

    def get_text(element, tag_name, default=""):
        node = find_tag(element, tag_name)
        return node.text.strip() if (node is not None and node.text) else default

    def get_float(element, tag_name, default=0.0):
        val = get_text(element, tag_name)
        try:
            return float(val) if val else default
        except ValueError:
            return default

    # VERIFICAÇÃO: EVENTO DE CANCELAMENTO / SUBSTITUIÇÃO
    if root.tag.endswith("evento") or find_tag(root, "pedRegEvento") is not None:
        ch_nfse_original = get_text(root, "chNFSe")
        ch_substituta = get_text(root, "chSubstituta")
        desc_evento = get_text(root, "xDesc")
        motivo_subst = get_text(root, "xMotivo")
        data_evento = get_text(root, "dhEvento")

        return {
            "tipo_xml": "EVENTO",
            "Chave NFS-e Original": ch_nfse_original,
            "Chave NFS-e Substituta": ch_substituta,
            "Descrição Evento": desc_evento,
            "Motivo Cancelamento": motivo_subst,
            "Data Evento": data_evento,
        }

    # NFS-E NORMAL
    inf_nfse_node = find_tag(root, "infNFSe")
    chave_nfse = inf_nfse_node.attrib.get("Id", "") if inf_nfse_node is not None else ""
    if chave_nfse.startswith("NFS"):
        chave_nfse = chave_nfse[3:]

    numero_nfse = get_text(root, "nNFSe")
    data_competencia = get_text(root, "dCompet")

    emit_node = find_tag(root, "emit")
    nome_empresa = get_text(emit_node, "xNome") if emit_node is not None else ""

    codigo_tributacao = get_text(root, "cTribNac")
    tipo_servico = (
        get_text(root, "xTribNac")
        or get_text(root, "xTribMun")
        or get_text(root, "xDescServ")
    )

    v_serv = get_float(root, "vServ")
    v_liq = get_float(root, "vLiq")
    if v_liq == 0.0 and v_serv > 0.0:
        v_liq = v_serv

    v_pis = get_float(root, "vPis")
    v_cofins = get_float(root, "vCofins")
    v_csll = get_float(root, "vCSLL")
    v_irrf = get_float(root, "vRetIRRF")
    v_inss = get_float(root, "vINSS")
    v_iss = get_float(root, "vISSQN")

    tp_ret_iss = get_text(root, "tpRetISSQN")
    iss_retido_flag = "Com Retenção" if tp_ret_iss == "2" else "Sem Retenção"

    # Monta lista amigável de impostos retidos na nota
    impostos_retidos_lista = []
    if v_pis > 0:
        impostos_retidos_lista.append("PIS")
    if v_cofins > 0:
        impostos_retidos_lista.append("COFINS")
    if v_csll > 0:
        impostos_retidos_lista.append("CSLL")
    if v_irrf > 0:
        impostos_retidos_lista.append("IRRF")
    if v_inss > 0:
        impostos_retidos_lista.append("INSS")
    if tp_ret_iss == "2":
        impostos_retidos_lista.append("ISS")

    if impostos_retidos_lista:
        texto_retenções = "Retenção " + "/".join(impostos_retidos_lista)
    else:
        texto_retenções = "Sem Retenção"

    diferenca = round(v_serv - v_liq, 2)

    return {
        "tipo_xml": "NFSE",
        "Chave NFS-e": chave_nfse,
        "Número da NFS-e": numero_nfse,
        "Data Competência": data_competencia,
        "Nome da Empresa": nome_empresa,
        "Código Tributação": codigo_tributacao,
        "Tipo de Serviço": tipo_servico,
        "Valor do Serviço": v_serv,
        "Valor PIS": v_pis,
        "PIS Retido?": "Com Retenção" if v_pis > 0 else "Sem Retenção",
        "Valor COFINS": v_cofins,
        "COFINS Retido?": "Com Retenção" if v_cofins > 0 else "Sem Retenção",
        "CSLL (Retida)": v_csll,
        "CSLL Retida?": "Com Retenção" if v_csll > 0 else "Sem Retenção",
        "IRRF": v_irrf,
        "IRRF Retido?": "Com Retenção" if v_irrf > 0 else "Sem Retenção",
        "INSS (Previdenciária)": v_inss,
        "INSS Retido?": "Com Retenção" if v_inss > 0 else "Sem Retenção",
        "ISS": v_iss,
        "ISS Retenção": v_iss if iss_retido_flag == "Com Retenção" else 0.0,
        "ISS Retido?": iss_retido_flag,
        "Valor Líquido": v_liq,
        "Diferença Bruto-Líquido": formatar_valor(diferenca),
        "Retenções Identificadas": texto_retenções,
        "Valor Total Retenções": formatar_valor(diferenca),
        "Status Validação": "OK",
        "Combinações Encontradas": 1,
    }


# ============================================================
# GERAR ABA ALTERDATA
# ============================================================
def gerar_aba_alterdata(df_extrato, mapa_contas):
    linhas_alterdata = []

    for _, row in df_extrato.iterrows():
        num_nota = str(row.get("Número da NFS-e", "") or "").strip()
        data_comp = converter_data_obj(row.get("Data Competência", ""))
        nome_empresa = str(row.get("Nome da Empresa", "") or "").strip()
        cod_trib = str(row.get("Código Tributação", "") or "").strip()

        conta_debito_bd = mapa_contas.get(cod_trib, {}).get("conta", "2135")
        desc_padrao = f"NF - {num_nota} {nome_empresa}".strip()

        val_bruto = converter_valor(row.get("Valor do Serviço")) or 0.0
        val_liquido = converter_valor(row.get("Valor Líquido")) or 0.0

        val_pis = converter_valor(row.get("Valor PIS")) or 0.0
        val_cofins = converter_valor(row.get("Valor COFINS")) or 0.0
        val_csll = converter_valor(row.get("CSLL (Retida)")) or 0.0
        val_irrf = converter_valor(row.get("IRRF")) or 0.0
        val_inss = converter_valor(row.get("INSS (Previdenciária)")) or 0.0
        val_iss = converter_valor(row.get("ISS Retenção")) or 0.0

        soma_retencoes = val_pis + val_cofins + val_csll + val_irrf + val_inss + val_iss

        if soma_retencoes == 0.0:
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
            if str(row.get("PIS Retido?", "")).strip().upper() == "COM RETENÇÃO":
                soma_pcc += val_pis
                pcc_retidos.append("PIS")
            if str(row.get("COFINS Retido?", "")).strip().upper() == "COM RETENÇÃO":
                soma_pcc += val_cofins
                pcc_retidos.append("COFINS")
            if str(row.get("CSLL Retida?", "")).strip().upper() == "COM RETENÇÃO":
                soma_pcc += val_csll
                pcc_retidos.append("CSLL")

            if soma_pcc > 0:
                nome_pcc = "/".join(pcc_retidos)
                desc_pcc = f"Retenção {nome_pcc} s/ NF - {num_nota} {nome_empresa}"
                linhas_alterdata.append({
                    "Data": data_comp,
                    "debito": "",
                    "credito": "236",
                    "valor": soma_pcc,
                    "documento": num_nota,
                    "historico": 99,
                    "descrição": desc_pcc,
                })

            if str(row.get("IRRF Retido?", "")).strip().upper() == "COM RETENÇÃO" and val_irrf > 0:
                desc_irrf = f"Retenção IRRF s/ NF - {num_nota} {nome_empresa}"
                linhas_alterdata.append({
                    "Data": data_comp,
                    "debito": "",
                    "credito": "763",
                    "valor": val_irrf,
                    "documento": num_nota,
                    "historico": 99,
                    "descrição": desc_irrf,
                })

            if str(row.get("INSS Retido?", "")).strip().upper() == "COM RETENÇÃO" and val_inss > 0:
                desc_inss = f"Retenção INSS s/ NF - {num_nota} {nome_empresa}"
                linhas_alterdata.append({
                    "Data": data_comp,
                    "debito": "",
                    "credito": "834",
                    "valor": val_inss,
                    "documento": num_nota,
                    "historico": 99,
                    "descrição": desc_inss,
                })

            if str(row.get("ISS Retido?", "")).strip().upper() == "COM RETENÇÃO" and val_iss > 0:
                desc_iss = f"Retenção ISS s/ NF - {num_nota} {nome_empresa}"
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

    return pd.DataFrame(linhas_alterdata)


# ============================================================
# GERAR ABA DE NOTAS CANCELADAS E SUBSTITUÍDAS
# ============================================================
def gerar_aba_substituidas(eventos_list, df_nfse):
    """Mapeia os dados das notas originais e substitutas com base nos eventos."""
    if not eventos_list:
        return pd.DataFrame()

    linhas_subst = []
    mapa_nfse = {}

    if df_nfse is not None and not df_nfse.empty:
        for _, row in df_nfse.iterrows():
            chave = str(row.get("Chave NFS-e", "")).strip()
            if chave:
                mapa_nfse[chave] = row

    for ev in eventos_list:
        ch_orig = ev.get("Chave NFS-e Original", "")
        ch_sub = ev.get("Chave NFS-e Substituta", "")

        nf_orig = mapa_nfse.get(ch_orig, {})
        nf_sub = mapa_nfse.get(ch_sub, {})

        linhas_subst.append({
            "Chave Nota Cancelada": ch_orig,
            "Nº Nota Cancelada": nf_orig.get("Número da NFS-e", "Não importada no lote"),
            "Valor Nota Cancelada": nf_orig.get("Valor do Serviço", "N/A"),
            "Chave Nota Substituta (Nova)": ch_sub,
            "Nº Nota Substituta (Nova)": nf_sub.get("Número da NFS-e", "Não importada no lote"),
            "Valor Nota Substituta": nf_sub.get("Valor do Serviço", "N/A"),
            "Motivo Cancelamento": ev.get("Motivo Cancelamento", ""),
            "Descrição do Evento": ev.get("Descrição Evento", ""),
            "Data do Evento": ev.get("Data Evento", ""),
        })

    return pd.DataFrame(linhas_subst)


# ============================================================
# INTERFACE STREAMLIT PRINCIPAL
# ============================================================
uploaded_files = st.file_uploader(
    "Arraste ou selecione os arquivos XML ou ZIP aqui",
    type=["xml", "zip"],
    accept_multiple_files=True,
)

if uploaded_files:
    if st.button("🚀 Processar NFS-e (XML)"):

        temp_dir = tempfile.mkdtemp()
        xmls_para_processar = []

        for uploaded_file in uploaded_files:
            nome_arquivo = uploaded_file.name
            extensao = os.path.splitext(nome_arquivo)[1].lower()

            if extensao == ".xml":
                caminho_xml = os.path.join(temp_dir, nome_arquivo)
                with open(caminho_xml, "wb") as f:
                    f.write(uploaded_file.getbuffer())
                xmls_para_processar.append(caminho_xml)

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
                            if arq.lower().endswith(".xml"):
                                xmls_para_processar.append(
                                    os.path.join(raiz, arq)
                                )
                except Exception as e:
                    st.error(f"Erro ao descompactar {nome_arquivo}: {e}")

        if xmls_para_processar:
            registros_nfse = []
            registros_eventos = []
            erros_processamento = []
            progress_bar = st.progress(0)
            status_text = st.empty()

            for i, caminho_xml in enumerate(xmls_para_processar):
                nome_xml = os.path.basename(caminho_xml)
                status_text.text(
                    f"Processando [{i+1}/{len(xmls_para_processar)}]: {nome_xml}"
                )
                try:
                    dados = extrair_xml(caminho_xml)
                    if dados.get("tipo_xml") == "EVENTO":
                        registros_eventos.append(dados)
                    else:
                        registros_nfse.append(dados)
                except Exception as err:
                    erros_processamento.append(f"{nome_xml}: {str(err)}")
                progress_bar.progress((i + 1) / len(xmls_para_processar))

            status_text.text("Extração concluída!")

            if erros_processamento:
                with st.expander("⚠️ Arquivos com erro de leitura"):
                    for err_msg in erros_processamento:
                        st.write(f"- {err_msg}")

            df_nfse = pd.DataFrame(registros_nfse)
            st.session_state["df_extrato"] = df_nfse
            st.session_state["eventos_list"] = registros_eventos

            mapa_tipo_servico_xml = {}
            if not df_nfse.empty:
                for _, row in df_nfse.iterrows():
                    cod = str(row.get("Código Tributação", "") or "").strip()
                    tipo = str(row.get("Tipo de Serviço", "") or "").strip()
                    if cod and tipo and cod not in mapa_tipo_servico_xml:
                        mapa_tipo_servico_xml[cod] = tipo
            st.session_state["mapa_tipo_servico_xml"] = mapa_tipo_servico_xml

            mapa_contas = carregar_banco_dados_github()
            if not df_nfse.empty:
                codigos_na_nf = set(df_nfse["Código Tributação"].dropna().unique())
                ausentes = [c for c in codigos_na_nf if c and c not in mapa_contas]
            else:
                ausentes = []

            st.session_state["codigos_ausentes"] = ausentes

        shutil.rmtree(temp_dir, ignore_errors=True)

# EXIBIÇÃO DE RESULTADOS E MAPPING DE CÓDIGOS
if (
    "df_extrato" in st.session_state
    and st.session_state["df_extrato"] is not None
):
    mapa_contas = carregar_banco_dados_github()
    df = st.session_state["df_extrato"]
    eventos_list = st.session_state.get("eventos_list", [])
    ausentes = st.session_state.get("codigos_ausentes", [])
    mapa_tipo_servico_xml = st.session_state.get("mapa_tipo_servico_xml", {})

    if ausentes:
        st.warning(
            "⚠️ Foram encontrados Códigos de Tributação não cadastrados no Banco de Dados!"
        )

        with st.form("form_novos_codigos"):
            novos_cadastros = {}
            for cod in ausentes:
                descricao_para_salvar = mapa_tipo_servico_xml.get(
                    cod, "Descrição do Serviço"
                )

                st.markdown(f"### 📌 Código: `{cod}`")
                st.info(f"📄 **Descrição do XML:** {cod} - {descricao_para_salvar}")

                nova_conta = st.text_input(
                    f"Informe a conta débito para o código {cod} (deixe em branco para usar 2135):",
                    key=f"input_{cod}",
                )
                novos_cadastros[cod] = {
                    "descricao": descricao_para_salvar,
                    "conta": nova_conta,
                }
                st.divider()

            salvar_btn = st.form_submit_button("💾 Confirmar e Processar")

        if salvar_btn:
            for cod, dados in novos_cadastros.items():
                conta_informada = dados["conta"].strip()
                mapa_contas[cod] = {
                    "descricao": dados["descricao"],
                    "conta": conta_informada if conta_informada else "2135",
                }

            salvar_banco_dados_github(mapa_contas)
            st.session_state["codigos_ausentes"] = []
            st.success("Contas atualizadas com sucesso!")

    if not st.session_state.get("codigos_ausentes"):
        df_alterdata = gerar_aba_alterdata(df, mapa_contas)
        df_substituidas = gerar_aba_substituidas(eventos_list, df)

        st.subheader("📊 Prévia - Aba Alterdata")
        st.dataframe(df_alterdata, use_container_width=True)

        if not df_substituidas.empty:
            st.subheader("⚠️ Notas Canceladas e Substituídas Identificadas")
            st.dataframe(df_substituidas, use_container_width=True)

        buffer = io.BytesIO()
        with pd.ExcelWriter(
            buffer, engine="openpyxl", date_format="dd/mm/yyyy"
        ) as writer:
            df_alterdata.to_excel(writer, index=False, sheet_name="Alterdata")
            df.to_excel(writer, index=False, sheet_name="NFS-e Extraídas")

            if not df_substituidas.empty:
                df_substituidas.to_excel(
                    writer, index=False, sheet_name="Notas Canceladas e Substituídas"
                )

            ws = writer.sheets["Alterdata"]
            for row in range(2, ws.max_row + 1):
                cell = ws.cell(row=row, column=1)
                cell.number_format = "dd/mm/yyyy"

        st.download_button(
            label="📥 Baixar Planilha para Importação Alterdata (.xlsx)",
            data=buffer.getvalue(),
            file_name="importacao_alterdata.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
