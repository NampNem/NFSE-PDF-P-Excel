import io
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

# ============================================================
# CONFIGURAÇÃO DA PÁGINA
# ============================================================
st.set_page_config(
    page_title="Extrator de NFS-e (XML)", page_icon="📄", layout="wide"
)

st.title("📄 Extrator de NFS-e (XML) e Gerador Alterdata / Domínio")
st.write(
    "Faça o upload dos arquivos **XML**, **PDF** ou de arquivos **ZIP** contendo os documentos."
)

NOME_BANCO_DADOS = "banco_de_dados.xlsx"

# ============================================================
# CONTAS FIXAS POR SISTEMA
# ============================================================
CONTAS = {
    "alterdata": {
        "debito_padrao": "2135",   # usada quando o código não tem conta no banco
        "credito_principal": "708",
        "pcc": "236",              # PIS / COFINS / CSLL
        "irrf": "763",
        "inss": "834",
        "iss": "3332",
    },
    "dominio": {
        "debito_padrao": "325",
        "credito_principal": "3907",
        "pcc": "647",             # PIS / COFINS / CSLL
        "irrf": "178",
        "inss": "184",
        "iss": "183",
    },
}

NOMES_MODO = {"alterdata": "Alterdata", "dominio": "Domínio"}


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


def limpar_conta(valor):
    """Converte o valor de uma célula de conta em texto limpo (2135.0 -> 2135)."""
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
    return (
        f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    )


def converter_data_obj(data_str):
    if not data_str:
        return None
    s = str(data_str).strip()
    # Formato do XML (AAAA-MM-DD): lê explicitamente, sem dayfirst,
    # senão o pandas pode inverter dia e mês (2026-01-12 -> 01/12/2026)
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
    """Extrai o formato 'AAAA-MM' a partir de uma data de competência."""
    if not data_str:
        return "SEM_DATA"
    try:
        dt = pd.to_datetime(data_str)
        return dt.strftime("%Y-%m")
    except:
        return "SEM_DATA"


def limpar_nome_arquivo(texto):
    """Remove caracteres inválidos para pastas/arquivos no SO."""
    return re.sub(r'[\\/*?:"<>|]', "", str(texto)).strip()


def conta_debito_do_banco(mapa, cod, modo):
    """Devolve a conta débito do banco para o sistema escolhido (ou a padrão)."""
    chave = "conta_dominio" if modo == "dominio" else "conta"
    valor = str(mapa.get(cod, {}).get(chave, "") or "").strip()
    return valor or CONTAS[modo]["debito_padrao"]


def codigo_precisa_cadastro(mapa, cod, modo):
    """Alterdata: só se o código não existe. Domínio: também se faltar a conta Domínio."""
    dados = mapa.get(cod)
    if dados is None:
        return True
    if modo == "dominio":
        return not str(dados.get("conta_dominio", "") or "").strip()
    return False


# ============================================================
# VALIDAÇÃO DE RETENÇÕES POR CÁLCULO
# ============================================================
def validar_retencoes(v_serv, v_liq, impostos, tol=0.02):
    """
    impostos: dict ordenado {"IRRF": valor, "PIS": valor, ...}
    Retorna (dict {imposto: True/False retido}, status, qtd_combinacoes).

    Regra: se bruto - líquido == 0, nada foi retido.
    Senão, testa 1 imposto, depois 2, depois 3... até
    líquido + soma(impostos) == bruto. Os impostos da combinação
    que fecha a conta são considerados retidos.
    """
    retidos = {nome: False for nome in impostos}
    diferenca = round(v_serv - v_liq, 2)

    # bruto == líquido -> nada foi retido
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
# GERENCIAMENTO DO BANCO DE DADOS (GITHUB)
# ============================================================
def carregar_banco_dados_github():
    """
    Estrutura do banco (3 colunas, sem cabeçalho):
      A: 'CÓDIGO - Descrição' | B: conta débito Alterdata | C: conta débito Domínio
    Bancos antigos com 2 colunas continuam funcionando (Domínio fica em branco).
    """
    mapa = {}
    if os.path.exists(NOME_BANCO_DADOS):
        try:
            if NOME_BANCO_DADOS.endswith(".csv"):
                df_bd = pd.read_csv(NOME_BANCO_DADOS, header=None)
            else:
                df_bd = pd.read_excel(NOME_BANCO_DADOS, header=None)

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
            st.error(f"Erro ao carregar o Banco de Dados: {e}")
    return mapa


def salvar_banco_dados_github(mapa):
    linhas = [
        (
            montar_celula_banco(cod, dados.get("descricao", "")),
            dados.get("conta", ""),
            dados.get("conta_dominio", ""),
        )
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
COL_COD = "Código Tributação"
COL_DESC = "Descrição (Operação)"
COL_ALT = "Conta Débito Alterdata"
COL_DOM = "Conta Débito Domínio"

with st.sidebar:
    st.header("⚙️ Gerenciar Banco de Dados")
    st.write(
        "Modifique, adicione ou remova códigos de tributação e as contas contábeis "
        "vinculadas (uma coluna para o Alterdata e outra para o Domínio)."
    )

    mapa_atual = carregar_banco_dados_github()
    df_gerenciador = pd.DataFrame(
        [
            {
                COL_COD: cod,
                COL_DESC: dados.get("descricao", ""),
                COL_ALT: dados.get("conta", ""),
                COL_DOM: dados.get("conta_dominio", ""),
            }
            for cod, dados in mapa_atual.items()
        ],
        columns=[COL_COD, COL_DESC, COL_ALT, COL_DOM],
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
            # Ignora linhas em branco (evita gravar "None" / "nan" como código)
            if pd.isna(row[COL_COD]) or not str(row[COL_COD]).strip():
                continue

            cod = extrair_codigo_do_banco(str(row[COL_COD]))
            descricao = (
                str(row[COL_DESC]).strip() if pd.notna(row[COL_DESC]) else ""
            )
            conta = limpar_conta(row[COL_ALT])
            conta_dom = limpar_conta(row[COL_DOM])
            if cod:
                novo_mapa[cod] = {
                    "descricao": descricao,
                    "conta": conta,
                    "conta_dominio": conta_dom,
                }

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

    # EVENTO DE CANCELAMENTO / SUBSTITUIÇÃO
    if root.tag.endswith("evento") or find_tag(root, "pedRegEvento") is not None:
        ch_nfse_original = get_text(root, "chNFSe")
        ch_substituta = get_text(root, "chSubstituta")
        desc_evento = get_text(root, "xDesc")
        motivo_subst = get_text(root, "xMotivo")
        data_evento = get_text(root, "dhEvento")
        cnpj_autor = get_text(root, "CNPJAutor")

        return {
            "tipo_xml": "EVENTO",
            "Chave NFS-e Original": ch_nfse_original,
            "Chave NFS-e Substituta": ch_substituta,
            "Descrição Evento": desc_evento,
            "Motivo Cancelamento": motivo_subst,
            "Data Evento": data_evento,
            "CNPJ Autor": cnpj_autor,
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
        """Tenta várias grafias de tag e devolve o primeiro valor encontrado (>0)."""
        for nome in nomes:
            valor = get_float(element, nome)
            if valor > 0:
                return valor
        return 0.0

    v_pis = get_float(root, "vPis")
    v_cofins = get_float(root, "vCofins")
    # Layout nacional: vRetCSLL (CSLL retida) e vRetCP (contribuição previdenciária retida)
    v_csll = get_float_multi(root, ["vRetCSLL", "vCSLL"])
    v_irrf = get_float_multi(root, ["vRetIRRF", "vIRRF"])
    v_inss = get_float_multi(root, ["vRetCP", "vINSS"])
    v_iss = get_float(root, "vISSQN")

    # Descontos reduzem o líquido sem serem retenção
    v_desc = get_float(root, "vDescIncond") + get_float(root, "vDescCond")
    v_base = round(v_serv - v_desc, 2)

    # Ordem de tentativa (em empates, escolhe o primeiro da lista)
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
        """Só considera o valor do imposto se a retenção foi confirmada pelo cálculo."""
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
        "ISS": v_iss,  # ISS devido (informado no XML)
        "ISS Retenção": val_ret("ISS"),  # ISS efetivamente retido
        "ISS Retido?": flag("ISS"),
        "Valor Líquido": v_liq,
        "Diferença Bruto-Líquido": formatar_valor(diferenca),
        "Retenções Identificadas": texto_retencoes,
        "Valor Total Retenções": formatar_valor(diferenca),
        "Status Validação": status_validacao,
        "Combinações Encontradas": qtd_comb,
    }


# ============================================================
# GERAR LANÇAMENTOS (ALTERDATA OU DOMÍNIO)
# ============================================================
def gerar_aba_alterdata(df_extrato, mapa_contas, modo="alterdata"):
    """
    Gera os lançamentos contábeis. O layout das linhas é o mesmo nos dois
    sistemas; o que muda são as contas (ver dicionário CONTAS e a coluna
    de conta débito do banco de dados).
    """
    contas = CONTAS[modo]
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
                    "credito": contas["pcc"],
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
                    "credito": contas["irrf"],
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
                    "credito": contas["inss"],
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
                    "credito": contas["iss"],
                    "valor": val_iss,
                    "documento": num_nota,
                    "historico": 99,
                    "descrição": desc_iss,
                })

            linhas_alterdata.append({
                "Data": data_comp,
                "debito": "",
                "credito": contas["credito_principal"],
                "valor": val_liquido,
                "documento": num_nota,
                "historico": 99,
                "descrição": desc_padrao,
            })

    return pd.DataFrame(linhas_alterdata)


# ============================================================
# GERAR TXT DOMÍNIO
# ============================================================
def gerar_txt_dominio(df_dominio, lote_inicial=1):
    """
    Layout Domínio: Data;Débito;Crédito;Valor;Histórico;Lote;;;
    - Linha só com débito   -> abre o múltiplo e recebe o próximo nº de lote
    - Linhas só com crédito -> continuam o múltiplo (lote vazio)
    - Linha com débito e crédito -> lançamento simples, também recebe o próximo nº de lote
    """

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
            # Toda linha com débito recebe um novo nº de lote
            # (abre o múltiplo OU é um lançamento simples)
            lote += 1
            lote_txt = str(lote)
        else:
            # Linhas só com crédito continuam o múltiplo (sem lote)
            lote_txt = ""

        linhas.append(f"{data_txt};{deb};{cred};{valor_txt};{hist};{lote_txt};;;")

    conteudo = "\r\n".join(linhas) + "\r\n"
    return conteudo.encode("cp1252", errors="replace")


# ============================================================
# GERAR ABA DE NOTAS CANCELADAS E SUBSTITUÍDAS
# ============================================================
def gerar_aba_substituidas(eventos_list, df_nfse):
    """Mapeia notas canceladas/substitutas incluindo Nome e CNPJ do Fornecedor."""
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

        # Tenta obter o nome do fornecedor do XML da NF ou assume N/A
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


# ============================================================
# RENOMEAR E ORGANIZAR PDFS EM PASTAS MENSAIS DENTRO DO ZIP
# ============================================================
def gerar_zip_pdfs_renomeados(df_nfse, pdfs_mapeados):
    """Mapeia os PDFs, organiza por pastas mensais (AAAA-MM) e renomeia para FORNECEDOR - NF 0000.pdf."""
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
                # Cria a estrutura de pastas por mês/ano dentro do ZIP
                caminho_no_zip = os.path.join(pasta_mes, nome_pdf)
                zip_out.write(caminho_pdf_original, arcname=caminho_no_zip)

    zip_buffer.seek(0)
    return zip_buffer.getvalue() if zip_buffer.getbuffer().nbytes > 0 else None


# ============================================================
# INTERFACE STREAMLIT PRINCIPAL
# ============================================================
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

                pasta_zip = os.path.join(
                    temp_dir, os.path.splitext(nome_arquivo)[0]
                )
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

            # Avisa quando a conta bruto/líquido/retenções não fechou ou ficou ambígua
            if not df_nfse.empty:
                problemas = df_nfse[df_nfse["Status Validação"] != "OK"]
                if not problemas.empty:
                    st.warning(
                        f"⚠️ {len(problemas)} nota(s) com retenção que não fechou "
                        "ou ficou ambígua. Confira a aba 'NFS-e Extraídas'."
                    )
                    st.dataframe(
                        problemas[
                            [
                                "Número da NFS-e",
                                "Nome da Empresa",
                                "Valor do Serviço",
                                "Valor Líquido",
                                "Status Validação",
                            ]
                        ],
                        use_container_width=True,
                    )

            st.session_state["df_extrato"] = df_nfse
            st.session_state["eventos_list"] = registros_eventos

            # Mapeia os PDFs para o novo arquivo ZIP organizado por pastas mensais
            zip_pdf_bytes = gerar_zip_pdfs_renomeados(df_nfse, pdfs_encontrados)
            st.session_state["zip_pdf_bytes"] = zip_pdf_bytes

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
                ausentes = [
                    c
                    for c in codigos_na_nf
                    if c and codigo_precisa_cadastro(mapa_contas, c, modo_escolhido)
                ]
            else:
                ausentes = []

            st.session_state["codigos_ausentes"] = ausentes

        shutil.rmtree(temp_dir, ignore_errors=True)

# EXIBIÇÃO DE RESULTADOS E MAPPINGS
if (
    "df_extrato" in st.session_state
    and st.session_state["df_extrato"] is not None
):
    modo = st.session_state.get("modo", "alterdata")
    nome_modo = NOMES_MODO[modo]

    mapa_contas = carregar_banco_dados_github()
    df = st.session_state["df_extrato"]
    eventos_list = st.session_state.get("eventos_list", [])
    ausentes = st.session_state.get("codigos_ausentes", [])
    mapa_tipo_servico_xml = st.session_state.get("mapa_tipo_servico_xml", {})
    zip_pdf_bytes = st.session_state.get("zip_pdf_bytes")

    st.info(f"Modo de processamento: **{nome_modo}**")

    if ausentes:
        st.warning(
            f"⚠️ Foram encontrados Códigos de Tributação sem conta {nome_modo} "
            "cadastrada no Banco de Dados!"
        )

        conta_padrao = CONTAS[modo]["debito_padrao"]

        with st.form("form_novos_codigos"):
            novos_cadastros = {}
            for cod in ausentes:
                descricao_para_salvar = (
                    mapa_contas.get(cod, {}).get("descricao")
                    or mapa_tipo_servico_xml.get(cod, "Descrição do Serviço")
                )

                st.markdown(f"### 📌 Código: `{cod}`")
                st.info(f"📄 **Descrição do XML:** {cod} - {descricao_para_salvar}")

                nova_conta = st.text_input(
                    f"Informe a conta débito {nome_modo} para o código {cod} "
                    f"(deixe em branco para usar {conta_padrao}):",
                    key=f"input_{modo}_{cod}",
                )
                novos_cadastros[cod] = {
                    "descricao": descricao_para_salvar,
                    "conta": nova_conta,
                }
                st.divider()

            salvar_btn = st.form_submit_button("💾 Confirmar e Processar")

        if salvar_btn:
            for cod, dados in novos_cadastros.items():
                conta_informada = dados["conta"].strip() or conta_padrao
                existente = mapa_contas.get(
                    cod, {"descricao": dados["descricao"], "conta": "", "conta_dominio": ""}
                )
                existente["descricao"] = existente.get("descricao") or dados["descricao"]
                if modo == "dominio":
                    existente["conta_dominio"] = conta_informada
                else:
                    existente["conta"] = conta_informada
                mapa_contas[cod] = existente

            salvar_banco_dados_github(mapa_contas)
            st.session_state["codigos_ausentes"] = []
            st.success("Contas atualizadas com sucesso!")

    if not st.session_state.get("codigos_ausentes"):
        df_lancamentos = gerar_aba_alterdata(df, mapa_contas, modo)
        df_substituidas = gerar_aba_substituidas(eventos_list, df)

        nome_aba = "Domínio" if modo == "dominio" else "Alterdata"

        st.subheader(f"📊 Prévia - Aba {nome_aba}")
        st.dataframe(df_lancamentos, use_container_width=True)

        if not df_substituidas.empty:
            st.subheader("⚠️ Notas Canceladas e Substituídas Identificadas")
            st.dataframe(df_substituidas, use_container_width=True)

        # Monta a planilha Excel em memória
        buffer_excel = io.BytesIO()
        with pd.ExcelWriter(
            buffer_excel, engine="openpyxl", date_format="dd/mm/yyyy"
        ) as writer:
            df_lancamentos.to_excel(writer, index=False, sheet_name=nome_aba)
            df.to_excel(writer, index=False, sheet_name="NFS-e Extraídas")

            if not df_substituidas.empty:
                df_substituidas.to_excel(
                    writer, index=False, sheet_name="Notas Canceladas e Substituídas"
                )

            ws = writer.sheets[nome_aba]
            for row in range(2, ws.max_row + 1):
                cell = ws.cell(row=row, column=1)
                cell.number_format = "dd/mm/yyyy"

        # DOWNLOADS
        st.markdown("---")
        st.subheader("📥 Downloads Disponíveis")

        if modo == "dominio":
            lote_inicial = st.number_input(
                "Nº do primeiro lote (Domínio)", min_value=1, value=1, step=1
            )
            txt_dominio = gerar_txt_dominio(df_lancamentos, lote_inicial)

            col1, col2, col3 = st.columns(3)

            with col1:
                st.download_button(
                    label="📄 Baixar Layout Domínio (.txt)",
                    data=txt_dominio,
                    file_name="importacao_dominio.txt",
                    mime="text/plain",
                )

            with col2:
                st.download_button(
                    label="📊 Baixar Planilha Domínio (.xlsx)",
                    data=buffer_excel.getvalue(),
                    file_name="importacao_dominio.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )

            with col3:
                if zip_pdf_bytes:
                    st.download_button(
                        label="📦 Baixar PDFs Organizados por Mês (.zip)",
                        data=zip_pdf_bytes,
                        file_name="NFS_PDFs_Organizados_Mensal.zip",
                        mime="application/zip",
                    )
                else:
                    st.info("Nenhum arquivo PDF correspondente encontrado no lote.")

        else:
            col1, col2 = st.columns(2)

            with col1:
                st.download_button(
                    label="📊 Baixar Planilha Alterdata (.xlsx)",
                    data=buffer_excel.getvalue(),
                    file_name="importacao_alterdata.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )

            with col2:
                if zip_pdf_bytes:
                    st.download_button(
                        label="📦 Baixar PDFs Organizados por Mês (.zip)",
                        data=zip_pdf_bytes,
                        file_name="NFS_PDFs_Organizados_Mensal.zip",
                        mime="application/zip",
                    )
                else:
                    st.info("Nenhum arquivo PDF correspondente encontrado no lote.")
