import io
import json
import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from itertools import combinations

from github import Github
import pandas as pd
import streamlit as st

NOME_BANCO_DADOS = "banco_de_dados.xlsx"

CONTAS = {
    "alterdata": {
        "debito_padrao": "2135",
        "credito_principal": "708",
        "pcc": "236",
        "irrf": "763",
        "inss": "834",
        "iss": "3332",
        "historico": "99",
    },
    "dominio": {
        "debito_padrao": "325",
        "credito_principal": "3907",
        "pcc": "647",
        "irrf": "178",
        "inss": "184",
        "iss": "183",
        "historico": "99",
    },
}

NOMES_MODO = {"alterdata": "Alterdata", "dominio": "Domínio"}


# ============================================================
# FUNÇÕES UTILITÁRIAS
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


def limpar_conta(valor):
    if valor is None:
        return ""
    try:
        if pd.isna(valor):
            return ""
    except (TypeError, ValueError):
        pass
    texto = str(valor).strip()
    if re.match(r"^\d+\.0$", texto):
        texto = texto[:-2]
    return "" if texto.lower() == "nan" else texto


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
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def converter_data_obj(data_str):
    if not data_str:
        return None
    s = str(data_str).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        try:
            return pd.to_datetime(s[:10], format="%Y-%m-%d").date()
        except:
            return None
    try:
        return pd.to_datetime(s, dayfirst=True).date()
    except:
        return None


def extrair_pasta_mes_ano(data_str):
    if not data_str:
        return "SEM_DATA"
    try:
        dt = pd.to_datetime(data_str)
        return dt.strftime("%Y-%m")
    except:
        return "SEM_DATA"


def limpar_nome_arquivo(texto):
    return re.sub(r'[\\/*?:"<>|]', "", str(texto)).strip()


def conta_debito_do_banco(mapa, cod, modo):
    chave = "conta_dominio" if modo == "dominio" else "conta"
    valor = str(mapa.get(cod, {}).get(chave, "") or "").strip()
    return valor or CONTAS[modo]["debito_padrao"]


def codigo_precisa_cadastro(mapa, cod, modo):
    dados = mapa.get(cod)
    if dados is None:
        return True
    if modo == "dominio":
        return not str(dados.get("conta_dominio", "") or "").strip()
    else:
        return not str(dados.get("conta", "") or "").strip()


def validar_retencoes(v_serv, v_liq, impostos, tol=0.02):
    retidos = {nome: False for nome in impostos}
    diferenca = round(v_serv - v_liq, 2)

    if abs(diferenca) <= tol:
        return retidos, "OK", 0

    candidatos = [(n, v) for n, v in impostos.items() if v > 0]

    for tamanho in range(1, len(candidatos) + 1):
        achados = [
            comb
            for comb in combinations(candidatos, tamanho)
            if abs(v_liq + sum(v for _, v in comb) - v_serv) <= tol
        ]
        if achados:
            for nome, _ in achados[0]:
                retidos[nome] = True
            status = "OK" if len(achados) == 1 else "Ambíguo (revisar)"
            return retidos, status, len(achados)

    return retidos, "Divergente (revisar)", 0


# ============================================================
# GERENCIAMENTO PERMANENTE DE EMPRESAS E BD (GITHUB)
# ============================================================
def carregar_empresas_github():
    if os.path.exists("empresas.json"):
        try:
            with open("empresas.json", "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def salvar_empresas_github(empresas_dict):
    with open("empresas.json", "w", encoding="utf-8") as f:
        json.dump(empresas_dict, f, ensure_ascii=False, indent=4)

    try:
        token = st.secrets.get("GITHUB_TOKEN")
        repo_name = st.secrets.get("REPO_NAME")
        if token and repo_name:
            g = Github(token)
            repo = g.get_repo(repo_name)
            content = json.dumps(empresas_dict, ensure_ascii=False, indent=4)
            try:
                contents = repo.get_contents("empresas.json")
                repo.update_file(contents.path, "Atualizando lista de empresas", content, contents.sha)
            except Exception:
                repo.create_file("empresas.json", "Criando lista de empresas", content)
    except Exception as e:
        st.error(f"Erro ao sincronizar empresas.json com o GitHub: {e}")


def obter_nome_arquivo_bd(usuario_id=None, empresa_id=None):
    if empresa_id:
        return f"plano_empresa_{empresa_id}.xlsx"
    if usuario_id:
        return f"banco_user_{usuario_id}.xlsx"
    return "banco_de_dados.xlsx"


def eh_proprietario_do_banco(nome_arquivo, usuario_logado, empresas_planos=None):
    if not usuario_logado:
        return True
    if f"banco_user_{usuario_logado}.xlsx" == nome_arquivo or nome_arquivo == "banco_de_dados.xlsx":
        return True

    if empresas_planos and isinstance(empresas_planos, dict):
        for cod_emp, dados in empresas_planos.items():
            if f"plano_empresa_{cod_emp}.xlsx" == nome_arquivo:
                return str(dados.get("criador")) == str(usuario_logado)

    return False


def carregar_banco_dados_github(nome_arquivo="banco_de_dados.xlsx"):
    mapa = {}
    if os.path.exists(nome_arquivo):
        try:
            if nome_arquivo.endswith(".csv"):
                df_bd = pd.read_csv(nome_arquivo, header=None)
            else:
                df_bd = pd.read_excel(nome_arquivo, header=None)

            for _, r in df_bd.iterrows():
                cod = extrair_codigo_do_banco(r.iloc[0])
                descricao = extrair_descricao_do_banco(r.iloc[0])
                conta = limpar_conta(r.iloc[1]) if len(r) > 1 else ""
                conta_dom = limpar_conta(r.iloc[2]) if len(r) > 2 else ""
                if cod:
                    mapa[cod] = {
                        "descricao": descricao,
                        "conta": conta,
                        "conta_dominio": conta_dom,
                    }
        except Exception as e:
            st.error(f"Erro ao carregar o Banco de Dados ({nome_arquivo}): {e}")
    return mapa


def salvar_banco_dados_github(mapa, nome_arquivo="banco_de_dados.xlsx"):
    linhas = [
        (
            montar_celula_banco(cod, dados.get("descricao", "")),
            dados.get("conta", ""),
            dados.get("conta_dominio", ""),
        )
        for cod, dados in mapa.items()
    ]
    df_bd = pd.DataFrame(linhas)
    df_bd.to_excel(nome_arquivo, index=False, header=False)

    try:
        token = st.secrets.get("GITHUB_TOKEN")
        repo_name = st.secrets.get("REPO_NAME")

        if token and repo_name:
            g = Github(token)
            repo = g.get_repo(repo_name)

            with open(nome_arquivo, "rb") as f:
                novo_conteudo = f.read()

            try:
                contents = repo.get_contents(nome_arquivo)
                repo.update_file(
                    contents.path,
                    f"Atualizando BD: {nome_arquivo}",
                    novo_conteudo,
                    contents.sha,
                )
            except:
                repo.create_file(
                    nome_arquivo,
                    f"Criando BD: {nome_arquivo}",
                    novo_conteudo,
                )
            st.success(f"Plano de Contas ({nome_arquivo}) salvo no GitHub com sucesso!")
        else:
            st.warning("Salvo apenas localmente (sem token configurado).")
    except Exception as e:
        st.error(f"Erro ao sincronizar com o GitHub ({nome_arquivo}): {e}")


def deletar_conta_do_banco(mapa, codigo_deletar, nome_arquivo="banco_de_dados.xlsx"):
    if codigo_deletar in mapa:
        del mapa[codigo_deletar]
        salvar_banco_dados_github(mapa, nome_arquivo)
        return True
    return False


# ============================================================
# PARSER DE XML E GERADOR DE RELATÓRIOS
# ============================================================
def extrair_xml(caminho_ou_conteudo):
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

    if root.tag.endswith("evento") or find_tag(root, "pedRegEvento") is not None:
        return {
            "tipo_xml": "EVENTO",
            "Chave NFS-e Original": get_text(root, "chNFSe"),
            "Chave NFS-e Substituta": get_text(root, "chSubstituta"),
            "Descrição Evento": get_text(root, "xDesc"),
            "Motivo Cancelamento": get_text(root, "xMotivo"),
            "Data Evento": get_text(root, "dhEvento"),
            "CNPJ Autor": get_text(root, "CNPJAutor"),
        }

    inf_nfse_node = find_tag(root, "infNFSe")
    chave_nfse = inf_nfse_node.attrib.get("Id", "") if inf_nfse_node is not None else ""
    if chave_nfse.startswith("NFS"):
        chave_nfse = chave_nfse[3:]

    numero_nfse = get_text(root, "nNFSe")
    data_competencia = get_text(root, "dCompet")

    emit_node = find_tag(root, "emit")
    nome_empresa = get_text(emit_node, "xNome") if emit_node is not None else ""
    cnpj_prestador = get_text(emit_node, "CNPJ") if emit_node is not None else ""

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

    def get_float_multi(element, nomes):
        for nome in nomes:
            valor = get_float(element, nome)
            if valor > 0:
                return valor
        return 0.0

    v_pis = get_float(root, "vPis")
    v_cofins = get_float(root, "vCofins")
    v_csll = get_float_multi(root, ["vRetCSLL", "vCSLL"])
    v_irrf = get_float_multi(root, ["vRetIRRF", "vIRRF"])
    v_inss = get_float_multi(root, ["vRetCP", "vINSS"])
    v_iss = get_float(root, "vISSQN")

    v_desc = get_float(root, "vDescIncond") + get_float(root, "vDescCond")
    v_base = round(v_serv - v_desc, 2)

    impostos = {
        "IRRF": v_irrf,
        "PIS": v_pis,
        "COFINS": v_cofins,
        "CSLL": v_csll,
        "INSS": v_inss,
        "ISS": v_iss,
    }
    retidos, status_validacao, qtd_comb = validar_retencoes(v_base, v_liq, impostos)

    def val_ret(nome):
        return impostos[nome] if retidos[nome] else 0.0

    def flag(nome):
        return "Com Retenção" if retidos[nome] else "Sem Retenção"

    lista_ret = [n for n in impostos if retidos[n]]
    texto_retencoes = (
        "Retenção " + "/".join(lista_ret) if lista_ret else "Sem Retenção"
    )
    diferenca = round(v_base - v_liq, 2)

    return {
        "tipo_xml": "NFSE",
        "Chave NFS-e": chave_nfse,
        "Número da NFS-e": numero_nfse,
        "Data Competência": data_competencia,
        "CNPJ Prestador": cnpj_prestador,
        "Nome da Empresa": nome_empresa,
        "Código Tributação": codigo_tributacao,
        "Tipo de Serviço": tipo_servico,
        "Valor do Serviço": v_serv,
        "Valor PIS": val_ret("PIS"),
        "PIS Retido?": flag("PIS"),
        "Valor COFINS": val_ret("COFINS"),
        "COFINS Retido?": flag("COFINS"),
        "CSLL (Retida)": val_ret("CSLL"),
        "CSLL Retida?": flag("CSLL"),
        "IRRF": val_ret("IRRF"),
        "IRRF Retido?": flag("IRRF"),
        "INSS (Previdenciária)": val_ret("INSS"),
        "INSS Retido?": flag("INSS"),
        "ISS": v_iss,
        "ISS Retenção": val_ret("ISS"),
        "ISS Retido?": flag("ISS"),
        "Valor Líquido": v_liq,
        "Diferença Bruto-Líquido": formatar_valor(diferenca),
        "Retenções Identificadas": texto_retencoes,
        "Valor Total Retenções": formatar_valor(diferenca),
        "Status Validação": status_validacao,
        "Combinações Encontradas": qtd_comb,
    }


def gerar_aba_alterdata(df_extrato, mapa_contas, modo="alterdata", contas=None):
    contas = contas or CONTAS[modo]
    historico = str(contas.get("historico", "99")).strip() or "99"
    if historico.isdigit():
        historico = int(historico)
    linhas_alterdata = []

    for _, row in df_extrato.iterrows():
        num_nota = str(row.get("Número da NFS-e", "") or "").strip()
        data_comp = converter_data_obj(row.get("Data Competência", ""))
        nome_empresa = str(row.get("Nome da Empresa", "") or "").strip()
        cod_trib = str(row.get("Código Tributação", "") or "").strip()

        conta_debito_bd = conta_debito_do_banco(mapa_contas, cod_trib, modo)
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
                "credito": contas["credito_principal"],
                "valor": val_bruto,
                "documento": num_nota,
                "historico": historico,
                "descrição": desc_padrao,
            })
        else:
            linhas_alterdata.append({
                "Data": data_comp,
                "debito": conta_debito_bd,
                "credito": "",
                "valor": val_bruto,
                "documento": num_nota,
                "historico": historico,
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
                    "credito": contas["pcc"],
                    "valor": soma_pcc,
                    "documento": num_nota,
                    "historico": historico,
                    "descrição": desc_pcc,
                })

            if str(row.get("IRRF Retido?", "")).strip().upper() == "COM RETENÇÃO" and val_irrf > 0:
                desc_irrf = f"Retenção IRRF s/ NF - {num_nota} {nome_empresa}"
                linhas_alterdata.append({
                    "Data": data_comp,
                    "debito": "",
                    "credito": contas["irrf"],
                    "valor": val_irrf,
                    "documento": num_nota,
                    "historico": historico,
                    "descrição": desc_irrf,
                })

            if str(row.get("INSS Retido?", "")).strip().upper() == "COM RETENÇÃO" and val_inss > 0:
                desc_inss = f"Retenção INSS s/ NF - {num_nota} {nome_empresa}"
                linhas_alterdata.append({
                    "Data": data_comp,
                    "debito": "",
                    "credito": contas["inss"],
                    "valor": val_inss,
                    "documento": num_nota,
                    "historico": historico,
                    "descrição": desc_inss,
                })

            if str(row.get("ISS Retido?", "")).strip().upper() == "COM RETENÇÃO" and val_iss > 0:
                desc_iss = f"Retenção ISS s/ NF - {num_nota} {nome_empresa}"
                linhas_alterdata.append({
                    "Data": data_comp,
                    "debito": "",
                    "credito": contas["iss"],
                    "valor": val_iss,
                    "documento": num_nota,
                    "historico": historico,
                    "descrição": desc_iss,
                })

            linhas_alterdata.append({
                "Data": data_comp,
                "debito": "",
                "credito": contas["credito_principal"],
                "valor": val_liquido,
                "documento": num_nota,
                "historico": historico,
                "descrição": desc_padrao,
            })

    return pd.DataFrame(linhas_alterdata)


def gerar_txt_dominio(df_dominio, lote_inicial=1):
    def limpo(v):
        if v is None or (not isinstance(v, str) and pd.isna(v)):
            return ""
        return str(v).strip()

    linhas = []
    lote = int(lote_inicial) - 1

    for _, r in df_dominio.iterrows():
        deb = limpo(r.get("debito"))
        cred = limpo(r.get("credito"))

        data = r.get("Data")
        data_txt = data.strftime("%d/%m/%Y") if hasattr(data, "strftime") else ""

        valor = converter_valor(r.get("valor")) or 0.0
        valor_txt = f"{valor:.2f}".replace(".", ",")

        hist = limpo(r.get("descrição")).replace(";", " ")

        if deb:
            lote += 1
            lote_txt = str(lote)
        else:
            lote_txt = ""

        linhas.append(f"{data_txt};{deb};{cred};{valor_txt};{hist};{lote_txt};;;")

    conteudo = "\r\n".join(linhas) + "\r\n"
    return conteudo.encode("cp1252", errors="replace")


def gerar_aba_substituidas(eventos_list, df_nfse):
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

        fornecedor_nome = (
            nf_orig.get("Nome da Empresa")
            or nf_sub.get("Nome da Empresa")
            or "Não encontrado no lote"
        )
        fornecedor_cnpj = (
            nf_orig.get("CNPJ Prestador")
            or nf_sub.get("CNPJ Prestador")
            or ev.get("CNPJ Autor", "N/A")
        )

        linhas_subst.append({
            "Fornecedor / Prestador": fornecedor_nome,
            "CNPJ Fornecedor": fornecedor_cnpj,
            "Chave Nota Cancelada": ch_orig,
            "Nº Nota Cancelada": nf_orig.get("Número da NFS-e", "Não importada no lote"),
            "Valor Nota Cancelada": nf_orig.get("Valor do Serviço", "N/A"),
            "Chave Nota Substituta (Nova)": ch_sub,
            "Nº Nota Substituta (Nova)": nf_sub.get("Número da NFS-e", "Não importada no lote"),
            "Valor Nota Substituta": nf_sub.get("Valor do Serviço", "N/A"),
            "Motivo Cancelamento": ev.get("Motivo Cancelamento", "Não informado"),
            "Descrição do Evento": ev.get("Descrição Evento", ""),
            "Data do Evento": ev.get("Data Evento", ""),
        })

    return pd.DataFrame(linhas_subst)


def gerar_zip_pdfs_renomeados(df_nfse, pdfs_mapeados):
    if not pdfs_mapeados or df_nfse.empty:
        return None

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_out:
        for _, row in df_nfse.iterrows():
            chave = str(row.get("Chave NFS-e", "")).strip()
            num_nota = str(row.get("Número da NFS-e", "")).strip()
            fornecedor = limpar_nome_arquivo(row.get("Nome da Empresa", "FORNECEDOR"))
            pasta_mes = extrair_pasta_mes_ano(row.get("Data Competência"))

            caminho_pdf_original = pdfs_mapeados.get(chave) or pdfs_mapeados.get(num_nota)

            if caminho_pdf_original and os.path.exists(caminho_pdf_original):
                nome_pdf = f"{fornecedor} - NF {num_nota}.pdf"
                caminho_no_zip = os.path.join(pasta_mes, nome_pdf)
                zip_out.write(caminho_pdf_original, arcname=caminho_no_zip)

    zip_buffer.seek(0)
    return zip_buffer.getvalue() if zip_buffer.getbuffer().nbytes > 0 else None


# ============================================================
# PÁGINA STREAMLIT SIEG XML (ENTRADA 1 POR 1 AO APERTAR ENTER)
# ============================================================
def pagina_sieg_xml(mapa_contas=None, nome_arquivo_bd="banco_de_dados.xlsx", eh_dono=True):
    st.title("📄 SIEG XML PARA Importação")
    st.write("Faça o upload dos arquivos **XML**, **PDF** ou **ZIP**.")

    if mapa_contas is None:
        mapa_contas = carregar_banco_dados_github(nome_arquivo_bd)

    uploaded_files = st.file_uploader(
        "Arraste ou selecione os arquivos XML, PDF ou ZIP aqui",
        type=["xml", "pdf", "zip"],
        accept_multiple_files=True,
    )

    if uploaded_files:
        col_btn1, col_btn2, _ = st.columns([1, 1, 2])
        with col_btn1:
            processar_alterdata = st.button("🚀 Processar para Alterdata")
        with col_btn2:
            processar_dominio = st.button("🚀 Processar para Domínio")

        if processar_alterdata or processar_dominio:
            modo_escolhido = "dominio" if processar_dominio else "alterdata"
            st.session_state["modo"] = modo_escolhido

            temp_dir = tempfile.mkdtemp()
            xmls_para_processar = []
            pdfs_encontrados = {}

            for uploaded_file in uploaded_files:
                nome_arquivo = uploaded_file.name
                extensao = os.path.splitext(nome_arquivo)[1].lower()

                if extensao == ".xml":
                    caminho_xml = os.path.join(temp_dir, nome_arquivo)
                    with open(caminho_xml, "wb") as f:
                        f.write(uploaded_file.getbuffer())
                    xmls_para_processar.append(caminho_xml)

                elif extensao == ".pdf":
                    caminho_pdf = os.path.join(temp_dir, nome_arquivo)
                    with open(caminho_pdf, "wb") as f:
                        f.write(uploaded_file.getbuffer())
                    nome_sem_ext = os.path.splitext(nome_arquivo)[0]
                    pdfs_encontrados[nome_sem_ext] = caminho_pdf

                elif extensao == ".zip":
                    caminho_zip = os.path.join(temp_dir, nome_arquivo)
                    with open(caminho_zip, "wb") as f:
                        f.write(uploaded_file.getbuffer())

                    pasta_zip = os.path.join(temp_dir, os.path.splitext(nome_arquivo)[0])
                    os.makedirs(pasta_zip, exist_ok=True)

                    try:
                        with zipfile.ZipFile(caminho_zip, "r") as zip_ref:
                            zip_ref.extractall(pasta_zip)

                        for raiz, _, arquivos in os.walk(pasta_zip):
                            for arq in arquivos:
                                ext = os.path.splitext(arq)[1].lower()
                                caminho_completo = os.path.join(raiz, arq)
                                if ext == ".xml":
                                    xmls_para_processar.append(caminho_completo)
                                elif ext == ".pdf":
                                    nome_sem_ext = os.path.splitext(arq)[0]
                                    pdfs_encontrados[nome_sem_ext] = caminho_completo
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
                    status_text.text(f"Processando [{i+1}/{len(xmls_para_processar)}]: {nome_xml}")
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

                df_nfse = pd.DataFrame(registros_nfse)
                st.session_state["df_extrato"] = df_nfse
                st.session_state["eventos_list"] = registros_eventos
                st.session_state["zip_pdf_bytes"] = gerar_zip_pdfs_renomeados(df_nfse, pdfs_encontrados)

                mapa_tipo_servico_xml = {}
                if not df_nfse.empty:
                    for _, row in df_nfse.iterrows():
                        cod = str(row.get("Código Tributação", "") or "").strip()
                        tipo = str(row.get("Tipo de Serviço", "") or "").strip()
                        if cod and tipo and cod not in mapa_tipo_servico_xml:
                            mapa_tipo_servico_xml[cod] = tipo
                st.session_state["mapa_tipo_servico_xml"] = mapa_tipo_servico_xml

                if not df_nfse.empty:
                    codigos_na_nf = set(df_nfse["Código Tributação"].dropna().unique())
                    ausentes = [
                        c for c in codigos_na_nf
                        if c and codigo_precisa_cadastro(mapa_contas, c, modo_escolhido)
                    ]
                else:
                    ausentes = []

                st.session_state["codigos_ausentes"] = ausentes

            shutil.rmtree(temp_dir, ignore_errors=True)

    if "df_extrato" in st.session_state and st.session_state["df_extrato"] is not None:
        modo = st.session_state.get("modo", "alterdata")
        nome_modo = NOMES_MODO[modo]
        df = st.session_state["df_extrato"]
        eventos_list = st.session_state.get("eventos_list", [])
        ausentes = st.session_state.get("codigos_ausentes", [])
        mapa_tipo_servico_xml = st.session_state.get("mapa_tipo_servico_xml", {})
        zip_pdf_bytes = st.session_state.get("zip_pdf_bytes")

        st.info(f"Modo de processamento: **{nome_modo}**")

        if ausentes:
            if eh_dono:
                st.warning(f"⚠️ Existem códigos sem conta {nome_modo} cadastrada neste Plano de Contas!")
                st.write("Configure abaixo **um a um**. Pressione **Enter** em cada caixa para salvar individualmente:")
                conta_padrao = CONTAS[modo]["debito_padrao"]

                for cod in list(ausentes):
                    desc_salvar = mapa_contas.get(cod, {}).get("descricao") or mapa_tipo_servico_xml.get(cod, "Descrição")
                    
                    with st.form(key=f"form_single_xml_{modo}_{cod}"):
                        st.markdown(f"#### 📌 Código: `{cod}`")
                        st.info(f"📄 **Descrição do XML:** {cod} - {desc_salvar}")

                        nova_conta = st.text_input(
                            f"Informe a conta débito {nome_modo} para `{cod}`:",
                            value=conta_padrao,
                            key=f"input_single_xml_{modo}_{cod}",
                        )
                        btn_salvar_indiv = st.form_submit_button(f"💾 Salvar Conta para Código {cod}")

                        if btn_salvar_indiv:
                            c_inf = nova_conta.strip() or conta_padrao
                            existente = mapa_contas.get(cod, {"descricao": desc_salvar, "conta": "", "conta_dominio": ""})
                            existente["descricao"] = existente.get("descricao") or desc_salvar
                            
                            if modo == "dominio":
                                existente["conta_dominio"] = c_inf
                            else:
                                existente["conta"] = c_inf
                            mapa_contas[cod] = existente

                            salvar_banco_dados_github(mapa_contas, nome_arquivo_bd)
                            st.session_state["codigos_ausentes"].remove(cod)
                            st.success(f"Conta para o código {cod} salva no plano!")
                            st.rerun()
                    st.divider()
            else:
                st.error(f"⚠️ Os códigos `{', '.join(ausentes)}` não estão cadastrados. Solicite ao dono deste plano que adicione as contas.")

        else:
            st.subheader(f"🧾 Contas dos impostos retidos - {nome_modo}")
            padrao = CONTAS[modo]
            campos_contas = [
                ("credito_principal", "Fornecedores (crédito)"),
                ("pcc", "PIS / COFINS / CSLL"),
                ("irrf", "IRRF"),
                ("inss", "INSS"),
                ("iss", "ISS"),
                ("historico", "Histórico padrão"),
            ]

            contas_editadas = dict(padrao)
            colunas_contas = st.columns(len(campos_contas))
            for coluna, (chave, rotulo) in zip(colunas_contas, campos_contas):
                with coluna:
                    v_dig = st.text_input(rotulo, value=padrao[chave], key=f"conta_sieg_{modo}_{chave}")
                    contas_editadas[chave] = v_dig.strip() or padrao[chave]

            df_lancamentos = gerar_aba_alterdata(df, mapa_contas, modo, contas_editadas)
            df_substituidas = gerar_aba_substituidas(eventos_list, df)
            nome_aba = "Domínio" if modo == "dominio" else "Alterdata"

            st.subheader(f"📊 Prévia - Aba {nome_aba}")
            st.dataframe(df_lancamentos, use_container_width=True)

            buffer_excel = io.BytesIO()
            with pd.ExcelWriter(buffer_excel, engine="openpyxl", date_format="dd/mm/yyyy") as writer:
                df_lancamentos.to_excel(writer, index=False, sheet_name=nome_aba)
                df.to_excel(writer, index=False, sheet_name="NFS-e Extraídas")
                if not df_substituidas.empty:
                    df_substituidas.to_excel(writer, index=False, sheet_name="Notas Canceladas")

            st.markdown("---")
            st.subheader("📥 Downloads Disponíveis")

            if modo == "dominio":
                lote_inicial = st.number_input("Nº do primeiro lote (Domínio)", min_value=1, value=1, step=1)
                txt_dominio = gerar_txt_dominio(df_lancamentos, lote_inicial)

                col1, col2, col3 = st.columns(3)
                with col1:
                    st.download_button("📄 Baixar Layout Domínio (.txt)", data=txt_dominio, file_name="importacao_dominio.txt", mime="text/plain")
                with col2:
                    st.download_button("📊 Baixar Planilha Domínio (.xlsx)", data=buffer_excel.getvalue(), file_name="importacao_dominio.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                with col3:
                    if zip_pdf_bytes:
                        st.download_button("📦 Baixar PDFs Organizados (.zip)", data=zip_pdf_bytes, file_name="NFS_PDFs_Organizados.zip", mime="application/zip")
            else:
                col1, col2 = st.columns(2)
                with col1:
                    st.download_button("📊 Baixar Planilha Alterdata (.xlsx)", data=buffer_excel.getvalue(), file_name="importacao_alterdata.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                with col2:
                    if zip_pdf_bytes:
                        st.download_button("📦 Baixar PDFs Organizados (.zip)", data=zip_pdf_bytes, file_name="NFS_PDFs_Organizados.zip", mime="application/zip")
