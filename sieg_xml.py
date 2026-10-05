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

NOME_BANCO_DADOS = "banco_de_dados.xlsx"

# ============================================================
# CONTAS FIXAS POR SISTEMA (valores padrão)
# ============================================================
CONTAS = {
    "alterdata": {
        "debito_padrao": "2135",   # usada quando o código não tem conta no banco
        "credito_principal": "708",
        "pcc": "236",              # PIS / COFINS / CSLL
        "irrf": "763",
        "inss": "834",
        "iss": "3332",
        "historico": "99",         # código do histórico padrão
    },
    "dominio": {
        "debito_padrao": "325",
        "credito_principal": "3907",
        "pcc": "647",             # PIS / COFINS / CSLL
        "irrf": "178",
        "inss": "184",
        "iss": "183",
        "historico": "99",
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


def resolver_codigo_por_prefixo(mapa, cod_4_digitos):
    """
    Inspirado na Macro VBA:
    Procura no mapa do Banco de Dados códigos de serviço que iniciem com a chave de 4 dígitos.
    Retorna a chave correspondente encontrada.
    """
    cod_str = str(cod_4_digitos).strip()
    if not cod_str:
        return None
    if cod_str in mapa:
        return cod_str

    coincidentes = [k for k in mapa.keys() if str(k).startswith(cod_str)]
    if len(coincidentes) >= 1:
        return coincidentes[0]  # Retorna a primeira ocorrência encontrada por prefixo
    return None


def conta_debito_do_banco(mapa, cod, modo):
    """Devolve a conta débito do banco para o sistema escolhido (suporta busca por prefixo)."""
    chave_modo = "conta_dominio" if modo == "dominio" else "conta"
    cod_encontrado = resolver_codigo_por_prefixo(mapa, cod)
    
    if cod_encontrado:
        valor = str(mapa.get(cod_encontrado, {}).get(chave_modo, "") or "").strip()
        if valor:
            return valor

    return CONTAS[modo]["debito_padrao"]


def codigo_precisa_cadastro(mapa, cod, modo):
    """
    Determina se um código precisa de cadastro no Banco de Dados.
    Verifica correspondência exata ou por prefixo.
    """
    cod_encontrado = resolver_codigo_por_prefixo(mapa, cod)
    if cod_encontrado is None:
        return True
    
    dados = mapa.get(cod_encontrado)
    if modo == "dominio":
        return not str(dados.get("conta_dominio", "") or "").strip()
    return not str(dados.get("conta", "") or "").strip()


# ============================================================
# VALIDAÇÃO DE RETENÇÕES POR CÁLCULO
# ============================================================
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
# PARSER EXTRATOR DE XML
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


# ============================================================
# LEITOR DE EXCEL SIEG COM REGRAS DA MACRO VBA INTEGRADAS
# ============================================================
def normalizar_nome_coluna(valor):
    if valor is None:
        return ""
    texto = str(valor).strip().lower()
    texto = (
        texto.replace("á", "a").replace("à", "a").replace("ã", "a").replace("â", "a")
        .replace("é", "e").replace("ê", "e")
        .replace("í", "i")
        .replace("ó", "o").replace("ô", "o").replace("õ", "o")
        .replace("ú", "u").replace("ç", "c")
    )
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def encontrar_coluna_excel(df, aliases):
    if df is None or df.empty:
        return None

    mapa = {normalizar_nome_coluna(col): col for col in df.columns}
    aliases_norm = [normalizar_nome_coluna(a) for a in aliases]

    for alias in aliases_norm:
        if alias in mapa:
            return mapa[alias]

    for alias in aliases_norm:
        if not alias:
            continue
        for nome_norm, nome_real in mapa.items():
            if alias in nome_norm or nome_norm in alias:
                return nome_real

    return None


def valor_excel_para_float(valor):
    if valor is None:
        return 0.0
    try:
        if pd.isna(valor):
            return 0.0
    except Exception:
        pass

    if isinstance(valor, (int, float)):
        return float(valor)

    texto = str(valor).strip().replace("R$", "").replace(" ", "")
    if not texto:
        return 0.0

    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif "," in texto:
        texto = texto.replace(",", ".")

    texto = re.sub(r"[^0-9.\-]", "", texto)

    try:
        return float(texto)
    except Exception:
        return 0.0


def primeira_coluna_encontrada(df, aliases):
    return encontrar_coluna_excel(df, aliases)


def preparar_dataframe_excel_sieg(df_raw, nome_aba=""):
    """
    Integração com a Macro VBA:
    - Suporta arquivos ABRASF e SIEG.
    - Lê OutRetencoes para compor retenções sociais.
    - Executa a verificação de inferência de ISS retido:
      restante = Valor_Servico - PIS - COFINS - OutRetencoes - IR
      Se 'restante' for igual à coluna de ISS (com tolerância), assume ISS Retido.
    """
    if df_raw is None or df_raw.empty:
        return None, "A planilha está vazia."

    candidatos = []
    for idx in range(min(12, len(df_raw))):
        valores = [normalizar_nome_coluna(v) for v in df_raw.iloc[idx].tolist()]
        qtd = sum(
            1
            for v in valores
            if any(
                termo in v
                for termo in [
                    "data competencia", "dt emissao", "nome da empresa", "rzprestador",
                    "numero da nf", "numero", "valor do servico", "valor servico",
                    "pis", "cofins", "outretencoes", "valor liquido", "status"
                ]
            )
        )
        candidatos.append((qtd, idx))

    _, linha_cabecalho = max(candidatos, key=lambda x: x[0])

    df = df_raw.iloc[linha_cabecalho + 1:].copy()
    df.columns = [str(v).strip() if not pd.isna(v) else "" for v in df_raw.iloc[linha_cabecalho].tolist()]
    df = df.loc[:, [c for c in df.columns if str(c).strip() and str(c).lower() != "nan"]]
    df = df.dropna(how="all").reset_index(drop=True)

    if df.empty:
        return None, f"A aba '{nome_aba}' não possui dados após o cabeçalho."

    # Mapeamento de colunas incluindo relatórios ABRASF e SIEG
    col_status = primeira_coluna_encontrada(df, ["Status", "Situacao", "Situação"])
    col_data = primeira_coluna_encontrada(
        df, ["Dt_Emissao", "Dt Emissao", "Data Competência", "Data Competencia", "Competência", "Data"]
    )
    col_empresa = primeira_coluna_encontrada(
        df, ["RzPrestador", "Razao Social Prestador", "Nome da Empresa", "Prestador", "Fornecedor", "Razão Social"]
    )
    col_nota = primeira_coluna_encontrada(
        df, ["Numero", "Número", "Número da NFS-e", "Numero da NFS-e", "Nota Fiscal", "NF", "Nº Nota"]
    )
    col_cod = primeira_coluna_encontrada(
        df, ["Cod_Servico", "Cod Servico", "Código Tributação", "Codigo Tributacao", "Código de Tributação"]
    )
    col_servico = primeira_coluna_encontrada(
        df, ["Valor_Servico", "Valor Servico", "Valor do Serviço", "Valor Bruto"]
    )
    col_pis = primeira_coluna_encontrada(df, ["PIS", "PIS Retido", "Valor PIS"])
    col_cofins = primeira_coluna_encontrada(df, ["COFINS", "COFINS Retido", "Valor COFINS"])
    col_pcc_total = primeira_coluna_encontrada(
        df,
        [
            "OutRetencoes", "Out Retencoes", "Outras Retenções", "Outras Retencoes",
            "Contrib. Sociais Ret. (R$)", "Contrib. Sociais Ret.", "PCC"
        ],
    )
    col_csll = primeira_coluna_encontrada(df, ["CSLL (Retida)", "CSLL Retida", "CSLL", "Valor CSLL"])
    col_irrf = primeira_coluna_encontrada(df, ["IR", "IRRF", "IRRF Retido", "Valor IRRF"])
    col_inss = primeira_coluna_encontrada(
        df, ["INSS (Previdenciária)", "INSS Retido", "Valor INSS"]
    )
    col_iss = primeira_coluna_encontrada(df, ["ISS", "Valor ISS"])
    col_iss_retido = primeira_coluna_encontrada(
        df, ["ISS Retido", "ISS Retenção", "ISS Retencao", "Valor ISS Retido"]
    )
    col_liquido = primeira_coluna_encontrada(
        df, ["Valor_Liquido", "Valor Liquido", "Valor Líquido"]
    )
    col_tipo = primeira_coluna_encontrada(df, ["Tipo de Serviço", "Descrição Serviço", "Serviço"])
    col_cnpj = primeira_coluna_encontrada(df, ["CNPJ Prestador", "CNPJ", "CNPJ Fornecedor"])

    obrigatorias = {
        "data": col_data,
        "empresa": col_empresa,
        "nota": col_nota,
        "valor do serviço": col_servico,
    }

    faltantes = [nome for nome, coluna in obrigatorias.items() if coluna is None]
    if faltantes:
        return None, f"Colunas obrigatórias não encontradas: {', '.join(faltantes)}"

    registros = []

    for _, row in df.iterrows():
        # Regra da Macro VBA: Ignorar notas não autorizadas se a coluna Status existir
        if col_status:
            status_val = str(row.get(col_status, "") or "").strip()
            if status_val and not status_val.lower().startswith("autorizado"):
                continue

        empresa = str(row.get(col_empresa, "") or "").strip()
        numero = str(row.get(col_nota, "") or "").strip()

        if numero.lower() in ("nan", "none"):
            numero = ""
        if empresa.lower() in ("nan", "none"):
            empresa = ""

        valor_servico = valor_excel_para_float(row.get(col_servico))
        if not numero and not empresa and valor_servico == 0:
            continue

        data = row.get(col_data)
        valor_pis = valor_excel_para_float(row.get(col_pis)) if col_pis else 0.0
        valor_cofins = valor_excel_para_float(row.get(col_cofins)) if col_cofins else 0.0
        valor_irrf = valor_excel_para_float(row.get(col_irrf)) if col_irrf else 0.0
        valor_inss = valor_excel_para_float(row.get(col_inss)) if col_inss else 0.0
        valor_iss = valor_excel_para_float(row.get(col_iss)) if col_iss else 0.0

        contrib_out_ret = (
            valor_excel_para_float(row.get(col_pcc_total)) if col_pcc_total else 0.0
        )
        csll_informada = valor_excel_para_float(row.get(col_csll)) if col_csll else 0.0

        # Ajuste para CSLL / Outras Retenções
        if contrib_out_ret > 0 and (valor_pis > 0 or valor_cofins > 0):
            valor_csll = round(contrib_out_ret - valor_pis - valor_cofins, 2)
            if valor_csll < 0:
                valor_csll = 0.0
        elif contrib_out_ret > 0:
            valor_csll = contrib_out_ret
        else:
            valor_csll = csll_informada

        # Inferência de ISS Retido (Regra da Macro VBA)
        valor_iss_ret = valor_excel_para_float(row.get(col_iss_retido)) if col_iss_retido else 0.0
        if valor_iss_ret == 0.0 and valor_iss > 0.0:
            restante_vba = valor_servico - valor_pis - valor_cofins - contrib_out_ret - valor_irrf
            if abs(restante_vba - valor_iss) <= 0.02:
                valor_iss_ret = valor_iss

        valor_liquido = (
            valor_excel_para_float(row.get(col_liquido))
            if col_liquido
            else round(
                valor_servico - valor_pis - valor_cofins - valor_csll - valor_irrf - valor_inss - valor_iss_ret, 2
            )
        )

        ret_pis = valor_pis > 0
        ret_cofins = valor_cofins > 0
        ret_csll = valor_csll > 0
        ret_irrf = valor_irrf > 0
        ret_inss = valor_inss > 0
        ret_iss = valor_iss_ret > 0

        lista_ret = []
        if ret_pis: lista_ret.append("PIS")
        if ret_cofins: lista_ret.append("COFINS")
        if ret_csll: lista_ret.append("CSLL/OutRet")
        if ret_irrf: lista_ret.append("IRRF")
        if ret_inss: lista_ret.append("INSS")
        if ret_iss: lista_ret.append("ISS")

        soma_ret = round(
            valor_pis + valor_cofins + valor_csll + valor_irrf + valor_inss + valor_iss_ret, 2
        )
        diferenca = round(valor_servico - valor_liquido, 2)

        status = "OK" if abs(diferenca - soma_ret) <= 0.02 or (soma_ret == 0 and abs(diferenca) <= 0.02) else "Divergente (revisar)"

        codigo = str(row.get(col_cod, "") if col_cod else "").strip()
        if codigo.lower() in ("nan", "none"):
            codigo = ""

        tipo_servico = str(row.get(col_tipo, "") if col_tipo else "").strip()
        cnpj = str(row.get(col_cnpj, "") if col_cnpj else "").strip()

        registros.append(
            {
                "tipo_xml": "EXCEL",
                "Chave NFS-e": "",
                "Número da NFS-e": numero,
                "Data Competência": data,
                "CNPJ Prestador": cnpj,
                "Nome da Empresa": empresa,
                "Código Tributação": codigo,
                "Tipo de Serviço": tipo_servico,
                "Valor do Serviço": valor_servico,
                "Valor PIS": valor_pis,
                "PIS Retido?": "Com Retenção" if ret_pis else "Sem Retenção",
                "Valor COFINS": valor_cofins,
                "COFINS Retido?": "Com Retenção" if ret_cofins else "Sem Retenção",
                "CSLL (Retida)": valor_csll,
                "CSLL Retida?": "Com Retenção" if ret_csll else "Sem Retenção",
                "IRRF": valor_irrf,
                "IRRF Retido?": "Com Retenção" if ret_irrf else "Sem Retenção",
                "INSS (Previdenciária)": valor_inss,
                "INSS Retido?": "Com Retenção" if ret_inss else "Sem Retenção",
                "ISS": valor_iss,
                "ISS Retenção": valor_iss_ret,
                "ISS Retido?": "Com Retenção" if ret_iss else "Sem Retenção",
                "Valor Líquido": valor_liquido,
                "Diferença Bruto-Líquido": formatar_valor(diferenca),
                "Retenções Identificadas": "Retenção " + "/".join(lista_ret) if lista_ret else "Sem Retenção",
                "Valor Total Retenções": formatar_valor(soma_ret),
                "Status Validação": status,
                "Combinações Encontradas": 1 if lista_ret else 0,
            }
        )

    if not registros:
        return None, f"A aba '{nome_
