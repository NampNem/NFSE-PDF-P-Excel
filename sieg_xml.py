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

PASTA_BANCOS = "planos_empresas"
ARQUIVO_EMPRESAS_JSON = os.path.join(PASTA_BANCOS, "empresas.json")

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
# FUNÇÕES UTILITÁRIAS DE FORMATAÇÃO E LIMPEZA
# ============================================================
def limpar_cnpj(cnpj):
    if not cnpj:
        return ""
    return re.sub(r"\D", "", str(cnpj)).strip()


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
# CONFERÊNCIA EXCLUSIVA DO CNPJ DO PRESTADOR
# ============================================================
def verificar_e_exibir_conferencia_cnpj(df_nfse, cnpj_empresa_esperado):
    if not cnpj_empresa_esperado or df_nfse.empty or "CNPJ Prestador" not in df_nfse.columns:
        return

    cnpj_limpo_esperado = limpar_cnpj(cnpj_empresa_esperado)
    cnpjs_prestadores = set(df_nfse["CNPJ Prestador"].dropna().apply(limpar_cnpj).unique())
    cnpjs_prestadores.discard("")

    if not cnpjs_prestadores:
        return

    if len(cnpjs_prestadores) == 1 and cnpj_limpo_esperado in cnpjs_prestadores:
        st.success(f"✅ **CNPJ do Prestador Confere!** O CNPJ do prestador das notas ({cnpj_empresa_esperado}) é idêntico ao cadastrado na empresa.")
    else:
        cnpjs_encontrados_str = ", ".join(list(cnpjs_prestadores))
        st.warning(
            f"⚠️ **Alerta de Divergência de CNPJ!**\n\n"
            f"- **CNPJ da Empresa Selecionada:** `{cnpj_empresa_esperado}`\n"
            f"- **CNPJ do Prestador Encontrado nas Notas:** `{cnpjs_encontrados_str}`\n\n"
            f"Verifique se selecionou a empresa correta antes de exportar!"
        )


# ============================================================
# GERENCIAMENTO DE PASTAS E REPOSITÓRIO GITHUB
# ============================================================
def garantir_pasta_local():
    """Cria a pasta local se não existir."""
    if not os.path.exists(PASTA_BANCOS):
        os.makedirs(PASTA_BANCOS, exist_ok=True)


def obter_caminho_relativo_bd(empresa_id):
    """Devolve o caminho do arquivo na pasta planos_empresas."""
    return os.path.join(PASTA_BANCOS, f"plano_empresa_{empresa_id}.xlsx")


def carregar_empresas_github():
    """Carrega a lista central de empresas da pasta planos_empresas."""
    garantir_pasta_local()
    if os.path.exists(ARQUIVO_EMPRESAS_JSON):
        try:
            with open(ARQUIVO_EMPRESAS_JSON, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def salvar_empresas_github(empresas_dict):
    """Salva a lista de empresas na pasta planos_empresas no GitHub."""
    garantir_pasta_local()
    with open(ARQUIVO_EMPRESAS_JSON, "w", encoding="utf-8") as f:
        json.dump(empresas_dict, f, ensure_ascii=False, indent=4)

    try:
        token = st.secrets.get("GITHUB_TOKEN")
        repo_name = st.secrets.get("REPO_NAME")
        if token and repo_name:
            g = Github(token)
            repo = g.get_repo(repo_name)
            content = json.dumps(empresas_dict, ensure_ascii=False, indent=4)
            caminho_repo = ARQUIVO_EMPRESAS_JSON.replace("\\", "/")
            try:
                contents = repo.get_contents(caminho_repo)
                repo.update_file(contents.path, "Atualizando lista de empresas", content, contents.sha)
            except Exception:
                repo.create_file(caminho_repo, "Criando lista de empresas", content)
    except Exception as e:
        st.error(f"Erro ao salvar empresas.json no GitHub: {e}")


def deletar_empresa_completa_github(cod_empresa, empresas_dict):
    """Apaga permanentemente o plano .xlsx e a empresa do JSON no GitHub."""
    caminho_local = obter_caminho_relativo_bd(cod_empresa)
    caminho_repo = caminho_local.replace("\\", "/")

    if cod_empresa in empresas_dict:
        del empresas_dict[cod_empresa]
        salvar_empresas_github(empresas_dict)

    if os.path.exists(caminho_local):
        os.remove(caminho_local)

    try:
        token = st.secrets.get("GITHUB_TOKEN")
        repo_name = st.secrets.get("REPO_NAME")
        if token and repo_name:
            g = Github(token)
            repo = g.get_repo(repo_name)
            try:
                contents = repo.get_contents(caminho_repo)
                repo.delete_file(contents.path, f"Deletando plano da empresa {cod_empresa}", contents.sha)
            except Exception:
                pass
        return True
    except Exception as e:
        st.error(f"Erro ao deletar empresa do GitHub: {e}")
        return False


def eh_proprietario_do_banco(nome_arquivo, usuario_logado, empresas_planos=None):
    if not usuario_logado or not nome_arquivo:
        return True

    nome_simples = os.path.basename(nome_arquivo)
    if empresas_planos and isinstance(empresas_planos, dict):
        for cod_emp, dados in empresas_planos.items():
            if f"plano_empresa_{cod_emp}.xlsx" == nome_simples:
                return str(dados.get("criador")) == str(usuario_logado)

    return False


def carregar_banco_dados_github(caminho_arquivo):
    garantir_pasta_local()
    mapa = {}
    if os.path.exists(caminho_arquivo):
        try:
            df_bd = pd.read_excel(caminho_arquivo, header=None)
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
            st.error(f"Erro ao carregar o Plano de Contas ({caminho_arquivo}):
