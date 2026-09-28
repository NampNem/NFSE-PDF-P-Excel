from collections import defaultdict
import datetime
import io
import itertools
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
    "Faça o upload dos arquivos **XML** de NFS-e ou de arquivos **ZIP** contendo os XMLs para processar."
)

NOME_BANCO_DADOS = "banco_de_dados.xlsx"

# ============================================================
# TABELA EXPANDIDA LC 116 / ISS (MUNICIPAL E NACIONAL)
# ============================================================
TABELA_LC116 = {
    # GRUPO 01 - INFORMÁTICA
    "01": "Serviços de informática e congêneres",
    "0101": "Análise e desenvolvimento de sistemas",
    "0102": "Programação",
    "0103": "Processamento, armazenamento ou hospedagem de dados, textos, imagens, vídeos, páginas web, aplicativos e sistemas de informação",
    "0104": "Elaboração de programas de computadores, inclusive de jogos eletrônicos",
    "0105": "Licenciamento ou cessão de direito de uso de programas de computação",
    "0106": "Assessoria e consultoria em informática",
    "0107": "Suporte técnico em informática, inclusive instalação, configuração e manutenção de programas de computação e bancos de dados",
    "0108": "Configuração e manutenção de redes, de páginas e de esquemas de nutrição visual",
    "0109": "Disponibilização de conteúdos de áudio, vídeo, imagem e texto por meio da internet",
    # GRUPO 07 - ENGENHARIA / ARQUITETURA / CONSTRUÇÃO
    "07": "Serviços relativos a engenharia, arquitetura, geologia, urbanismo, construção civil, manutenção, limpeza e meio ambiente",
    "0701": "Engenharia, agronomia, agrimensura, arquitetura, geologia, urbanismo, paisagismo e congêneres",
    "0702": "Execução, por administração, empreitada ou subempreitada, de obras de construção civil, hidráulica ou elétrica e de outras obras semelhantes",
    "0703": "Elaboração de planos diretores, estudos de viabilidade, projetos e especificações técnicas",
    "0704": "Demolição",
    "0705": "Reparação, conservação e reforma de edifícios, estradas, pontes, portos e congêneres",
    "0706": "Colocação e instalação de tapetes, carpetes, assoalhos, cortinas, revestimentos de parede, vidros, divisórias, placas de gesso e congêneres",
    "0709": "Varrição, coleta, remoção, incineração, tratamento, reciclagem, separação e destinação final de lixo, rejeitos e outros resíduos",
    "0710": "Limpeza, manutenção e conservação de vias e logradouros públicos, imóveis, chaminés, piscinas, parques e jardins",
    "0711": "Decoração e jardinagem, inclusive corte e poda de árvores",
    "0712": "Controle e eliminação de pragas urbanas, dedetização, desinfecção, desinsetização, imunização e desratização",
    # GRUPO 10 - INTERMEDIAÇÃO / AGENCIAMENTO
    "10": "Serviços de intermediação e congêneres",
    "1001": "Agenciamento, corretagem ou intermediação de câmbio, de títulos e valores mobiliários",
    "1002": "Agenciamento, corretagem ou intermediação de títulos em geral, valores mobiliários e contratos quaisquer",
    "1003": "Agenciamento, corretagem ou intermediação de direitos de propriedade industrial, artística ou literária",
    "1004": "Agenciamento, corretagem ou intermediação de contratos de arrendamento mercantil (leasing), de franquia (franchising) e de faturização (factoring)",
    "1005": "Agenciamento, corretagem ou intermediação de bens móveis ou imóveis, não abrangidos em outros itens",
    "1006": "Agenciamento de notícias",
    "1007": "Agenciamento de publicidade e propaganda, inclusive o agenciamento de veiculação por quaisquer meios",
    "1008": "Agenciamento de navegação marítima, fluvial ou lacustre",
    "1009": "Agenciamento de transporte de carga",
    # GRUPO 11 - GUARDA, VIGILÂNCIA E ARMAZENAMENTO
    "11": "Serviços de guarda, estacionamento, armazenamento, vigilância e rasteio",
    "1101": "Guarda e estacionamento de veículos automotores terrestres, de aeronaves e de embarcações",
    "1102": "Vigilância, segurança ou monitoramento de bens, pessoas e semoventes",
    "1104": "Armazenamento, depósito, carga, descarga, arrumação e guarda de bens de qualquer espécie",
    # GRUPO 13 - SERVIÇOS GRÁFICOS E REPROGRAFIA
    "13": "Serviços relativos a fonografia, fotografia, cinematografia e reprografia",
    "1304": "Reprografia, microfilmagem e digitalização",
    # GRUPO 14 - MANUTENÇÃO E ASSISTÊNCIA TÉCNICA
    "14": "Serviços relativos a bens de terceiros",
    "1401": "Lubrificação, limpeza, lustração, revisão, carga e recarga, conserto, restauração, blindagem, manutenção e conservação de máquinas, veículos, aparelhos, equipamentos",
    "1402": "Assistência técnica",
    "1405": "Restauração, recondicionamento, acondicionamento, pintura, beneficiamento, lavagem, secagem, tingimento, galvanoplastia, anodização, corte, recorte, plastificação, costura e acabamento",
    "1406": "Instalação e montagem de aparelhos, máquinas e equipamentos, inclusive montagem industrial",
    # GRUPO 17 - CONSULTORIA, APOIO ADMINISTRATIVO E CONTABILIDADE
    "17": "Serviços de apoio técnico, comercial, jurídico, contábil, administrativo e congêneres",
    "1701": "Assessoria ou consultoria de qualquer natureza, não contida em outros itens",
    "1702": "Perícias, laudos, exames técnicos e análises técnicas",
    "1703": "Planejamento, organização e administração de feiras, exposições, congressos e congêneres",
    "1704": "Recrutamento, agenciamento, seleção e colocação de mão de obra",
    "1705": "Fornecimento de mão de obra, mesmo em caráter temporário, inclusive de empregados ou trabalhadores, avulsos ou temporários",
    "1706": "Propaganda e publicidade, inclusive promoção de vendas, planejamento de campanhas ou sistemas de publicidade, elaboração de desenhos, textos e demais materiais publicitários",
    "1712": "Adestramento, treinamento, ensino e avaliação de qualquer natureza",
    "1714": "Advocacia",
    "1719": "Contabilidade, inclusive serviços técnicos e auxiliares",
    "1720": "Consultoria e assessoria econômica ou financeira",
    "1725": "Inserção de textos, desenhos e outros materiais de propaganda e publicidade em qualquer meio (exceto em livros, jornais e periódicos)",
    # GRUPO 24 - CHAVEIROS E SINALIZAÇÃO
    "24": "Serviços de chaveiros, confecção de carimbos, placas, sinalização e congêneres",
    "2401": "Serviços chaveiros, confecção de carimbos, placas, sinalização visual, banners, adesivos e congêneres",
}


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


def obter_descricao_servico(codigo, tipo_servico_xml=None):
    if tipo_servico_xml:
        return tipo_servico_xml.strip()

    cod_limpo = re.sub(r"\D", "", str(codigo))
    if not cod_limpo:
        return "Código de tributação não informado"

    if len(cod_limpo) >= 4:
        sub_cod4 = cod_limpo[:4]
        if sub_cod4 in TABELA_LC116:
            return TABELA_LC116[sub_cod4]

    if len(cod_limpo) >= 2:
        sub_cod2 = cod_limpo[:2]
        if sub_cod2 in TABELA_LC116:
            return f"Grupo {sub_cod2}: {TABELA_LC116[sub_cod2]}"

    return f"Código {codigo} (Consulte o plano de contas para definir o débito)"


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
# PARSER EXTRATOR DE XML (PADRÃO NACIONAL SPED)
# ============================================================
def extrair_nfse_xml(caminho_ou_conteudo):
    """Realiza o parse das tags do XML da NFS-e do Padrão Nacional (v1.01)."""
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

    # Dados da Nota
    numero_nfse = get_text(root, "nNFSe")
    data_competencia = get_text(root, "dCompet")

    # Prestador
    emit_node = find_tag(root, "emit")
    nome_empresa = get_text(emit_node, "xNome") if emit_node is not None else ""

    # Códigos de Serviço
    c_trib_nac = get_text(root, "cTribNac")
    codigo_tributacao = c_trib_nac[:4] if c_trib_nac else ""

    # Descrição do Serviço
    tipo_servico = get_text(root, "xTribNac") or get_text(root, "xDescServ")

    # Valores Financeiros
    v_serv = get_float(root, "vServ")
    v_liq = get_float(root, "vLiq")
    if v_liq == 0.0 and v_serv > 0.0:
        v_liq = v_serv

    # Impostos e Retenções
    v_pis = get_float(root, "vPis")
    v_cofins = get_float(root, "vCofins")
    v_csll = get_float(root, "vCSLL")
    v_irrf = get_float(root, "vRetIRRF")
    v_inss = get_float(root, "vINSS")
    v_iss = get_float(root, "vISSQN")

    # Status de Retenção
    tp_ret_iss = get_text(root, "tpRetISSQN")
    iss_retido_flag = "RETIDO" if tp_ret_iss == "2" else "NÃO RETIDO"

    pis_status = "RETIDO" if v_pis > 0 else "NÃO RETIDO"
    cofins_status = "RETIDO" if v_cofins > 0 else "NÃO RETIDO"
    csll_status = "RETIDO" if v_csll > 0 else "NÃO RETIDO"
    irrf_status = "RETIDO" if v_irrf > 0 else "NÃO RETIDO"
    inss_status = "RETIDO" if v_inss > 0 else "NÃO RETIDO"

    diferenca = round(v_serv - v_liq, 2)

    return {
        "Número da NFS-e": numero_nfse,
        "Data Competência": data_competencia,
        "Nome da Empresa": nome_empresa,
        "Código Tributação": codigo_tributacao,
        "Tipo de Serviço": tipo_servico,
        "Valor do Serviço": v_serv,
        "Valor PIS": v_pis,
        "PIS Retido?": pis_status,
        "Valor COFINS": v_cofins,
        "COFINS Retido?": cofins_status,
        "CSLL (Retida)": v_csll,
        "CSLL Retida?": csll_status,
        "IRRF": v_irrf,
        "IRRF Retido?": irrf_status,
        "INSS (Previdenciária)": v_inss,
        "INSS Retido?": inss_status,
        "ISS": v_iss,
        "ISS Retenção": v_iss if iss_retido_flag == "RETIDO" else 0.0,
        "ISS Retido?": iss_retido_flag,
        "Valor Líquido": v_liq,
        "Diferença Bruto-Líquido": formatar_valor(diferenca),
        "Retenções Validadas": "VALIDAÇÃO XML OK",
        "Valor Retenções Validadas": formatar_valor(diferenca),
        "Status Validação": "VALIDADO - FECHAMENTO EXATO",
        "Combinações Encontradas": 1,
    }


# ============================================================
# GERAR ABA ALTERDATA
# ============================================================
def gerar_aba_alterdata(df_extrato, mapa_contas):
    linhas_alterdata = []

    for _, row in df_extrato.iterrows():
        status_validacao = str(row.get("Status Validação", "") or "").upper()
        if "SUBSTITUÍDA" in status_validacao or "CANCELADA" in status_validacao:
            continue

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

            if str(row.get("IRRF Retido?", "")).strip().upper() == "RETIDO" and val_irrf > 0:
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

            if str(row.get("INSS Retido?", "")).strip().upper() == "RETIDO" and val_inss > 0:
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

            if str(row.get("ISS Retido?", "")).strip().upper() == "RETIDO" and val_iss > 0:
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
            registros = []
            erros_processamento = []
            progress_bar = st.progress(0)
            status_text = st.empty()

            for i, caminho_xml in enumerate(xmls_para_processar):
                nome_xml = os.path.basename(caminho_xml)
                status_text.text(
                    f"Processando [{i+1}/{len(xmls_para_processar)}]: {nome_xml}"
                )
                try:
                    registros.append(extrair_nfse_xml(caminho_xml))
                except Exception as err:
                    erros_processamento.append(f"{nome_xml}: {str(err)}")
                progress_bar.progress((i + 1) / len(xmls_para_processar))

            status_text.text("Extração concluída!")

            if erros_processamento:
                with st.expander("⚠️ Arquivos com erro de leitura"):
                    for err_msg in erros_processamento:
                        st.write(f"- {err_msg}")

            df = pd.DataFrame(registros)
            st.session_state["df_extrato"] = df

            mapa_tipo_servico_xml = {}
            for _, row in df.iterrows():
                cod = str(row.get("Código Tributação", "") or "").strip()
                tipo = str(row.get("Tipo de Serviço", "") or "").strip()
                if cod and tipo and cod not in mapa_tipo_servico_xml:
                    mapa_tipo_servico_xml[cod] = tipo
            st.session_state["mapa_tipo_servico_pdf"] = mapa_tipo_servico_xml

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
    mapa_tipo_servico_xml = st.session_state.get("mapa_tipo_servico_pdf", {})

    if ausentes:
        st.warning(
            "⚠️ Foram encontrados Códigos de Tributação não cadastrados no Banco de Dados!"
        )

        with st.form("form_novos_codigos"):
            novos_cadastros = {}
            for cod in ausentes:
                tipo_xml = mapa_tipo_servico_xml.get(cod)
                if tipo_xml:
                    descricao_para_salvar = tipo_xml
                    fonte = "extraída do XML"
                else:
                    descricao_para_salvar = obter_descricao_servico(cod)
                    fonte = "tabela local (aproximada)"

                st.markdown(f"### 📌 Código: `{cod}`")
                st.info(f"📄 **Descrição {fonte}:** {cod} - {descricao_para_salvar}")

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

        st.subheader("📊 Prévia - Aba Alterdata")
        st.dataframe(df_alterdata, use_container_width=True)

        buffer = io.BytesIO()
        with pd.ExcelWriter(
            buffer, engine="openpyxl", date_format="dd/mm/yyyy"
        ) as writer:
            df_alterdata.to_excel(writer, index=False, sheet_name="Alterdata")
            df.to_excel(writer, index=False, sheet_name="NFS-e Extraídas")

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
