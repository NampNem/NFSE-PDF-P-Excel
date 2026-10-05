"""
Aplicação Streamlit - Processador de Notas Fiscais
Roda 100% online (Navegador / Streamlit Community Cloud)
"""
import numbers
import re
from datetime import datetime
import io

import pandas as pd
import streamlit as st

# ==========================================
# CONFIGURAÇÃO DA PÁGINA
# ==========================================
st.set_page_config(
    page_title="Processador de NFS-e & XML",
    page_icon="📄",
    layout="wide"
)

# ==========================================
# FUNÇÕES DE APOIO E VALIDAÇÃO (REGRAS)
# ==========================================
def formatar_valor(valor):
    """Formata número para o padrão de moeda BRL (ex: R$ 1.250,50)"""
    try:
        return f"R$ {float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (ValueError, TypeError):
        return "R$ 0,00"


def validar_retencoes(base, liquido, impostos):
    """
    Função base para validação de retenções.
    Mantenha ou integre com as regras exatas do seu sieg_xml.py se necessário.
    """
    diferenca = round(base - liquido, 2)
    soma_impostos = round(sum(impostos.values()), 2)

    retidos = {k: v > 0 for k, v in impostos.items()}
    status = "OK" if abs(diferenca - soma_impostos) <= 0.05 else "Divergente"
    
    return retidos, status, 1


# ==========================================
# LEITOR DE EXCEL (PORTAL NACIONAL)
# ==========================================
ABA_PREFERIDA = "Relação"
COLUNAS_OBRIGATORIAS = ["Número NFS-e", "Valor do Serviço (R$)"]


def _texto(v):
    try:
        if v is None or pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    return str(v).strip()


def _num(v):
    if isinstance(v, bool):
        return 0.0
    if isinstance(v, numbers.Number):
        return 0.0 if pd.isna(v) else float(v)
    t = _texto(v).replace("R$", "").replace(" ", "")
    if not t or t == "-":
        return 0.0
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return 0.0


def _numero_nota(v):
    t = _texto(v)
    if not t:
        return ""
    try:
        f = float(t)
        if f.is_integer():
            return str(int(f))
    except ValueError:
        pass
    return t


def _data_iso(v):
    if v is None or pd.isna(v):
        return ""
    if isinstance(v, (datetime, pd.Timestamp)):
        return v.strftime("%Y-%m-%d")
    t = _texto(v)
    if not t:
        return ""
    dt = pd.to_datetime(t, dayfirst=True, errors="coerce")
    return "" if pd.isna(dt) else dt.strftime("%Y-%m-%d")


def extrair_nfse_excel(origem, validar_retencoes_fn, formatar_valor_fn):
    xls = pd.ExcelFile(origem)
    aba = ABA_PREFERIDA if ABA_PREFERIDA in xls.sheet_names else xls.sheet_names[0]
    df = xls.parse(aba, dtype=object)
    df.columns = [str(c).strip() for c in df.columns]

    faltando = [c for c in COLUNAS_OBRIGATORIAS if c not in df.columns]
    if faltando:
        raise ValueError(
            f"Colunas não encontradas na aba '{aba}': {', '.join(faltando)}. "
            "Este não parece ser o Excel de NFS-e esperado."
        )

    registros = []
    ignoradas = []

    for row in df.to_dict("records"):
        def g(coluna):
            return row.get(coluna)

        numero = _numero_nota(g("Número NFS-e"))
        if not numero:
            continue

        nome_empresa = _texto(g("Nome Prestador"))
        v_serv = _num(g("Valor do Serviço (R$)"))

        situacao = _texto(g("Situação"))
        if situacao and situacao.lower() != "normal":
            ignoradas.append(
                {
                    "Número da NFS-e": numero,
                    "Fornecedor": nome_empresa,
                    "Valor do Serviço": formatar_valor_fn(v_serv),
                    "Situação": situacao,
                }
            )
            continue

        v_desc = _num(g("Desconto Incond. (R$)"))
        v_base = round(v_serv - v_desc, 2)

        v_pis = _num(g("PIS - Débito (R$)"))
        v_cofins = _num(g("COFINS - Débito (R$)"))
        v_contrib_sociais_ret = _num(g("Contrib. Sociais Ret. (R$)"))
        v_csll = round(max(0.0, v_contrib_sociais_ret - v_pis - v_cofins), 2)

        v_irrf = _num(g("IRRF (R$)"))
        v_inss = _num(g("Contrib. Previd. Ret. (R$)"))
        v_iss = _num(g("Valor do ISSQN (R$)"))

        iss_retido_planilha = _texto(g("Retenção ISSQN"))[:1] in ("2", "3")

        retencao_informada = (
            v_contrib_sociais_ret
            + v_irrf
            + v_inss
            + (v_iss if iss_retido_planilha else 0.0)
        )
        v_liq = round(v_base - retencao_informada, 2)

        impostos = {
            "IRRF": v_irrf,
            "PIS": v_pis,
            "COFINS": v_cofins,
            "CSLL": v_csll,
            "INSS": v_inss,
            "ISS": v_iss,
        }
        retidos, status_validacao, qtd_comb = validar_retencoes_fn(
            v_base, v_liq, impostos
        )

        def val_ret(nome):
            return impostos[nome] if retidos[nome] else 0.0

        def flag(nome):
            return "Com Retenção" if retidos[nome] else "Sem Retenção"

        lista_ret = [n for n in impostos if retidos[n]]
        texto_retencoes = (
            "Retenção " + "/".join(lista_ret) if lista_ret else "Sem Retenção"
        )
        diferenca = round(v_base - v_liq, 2)

        cod_texto = _texto(g("Cód. Tributação Nacional"))
        m = re.match(r"^(\d+)\s*-?\s*(.*)$", cod_texto, re.S)
        if m:
            codigo_tributacao, tipo_servico = m.group(1), m.group(2).strip()
        else:
            codigo_tributacao, tipo_servico = cod_texto, ""
        if not tipo_servico:
            tipo_servico = _texto(g("Descrição do Serviço"))

        cnpj_limpo = re.sub(r"\D", "", _texto(g("CNPJ/CPF Prestador")))
        if len(cnpj_limpo) > 11:
            cnpj_formatado = cnpj_limpo.zfill(14)
        elif cnpj_limpo:
            cnpj_formatado = cnpj_limpo.zfill(11)
        else:
            cnpj_formatado = ""

        registros.append(
            {
                "tipo_xml": "NFSE",
                "Chave NFS-e": _texto(g("Chave NFS-e")),
                "Número da NFS-e": numero,
                "Data Competência": _data_iso(g("Data Geração")),
                "CNPJ Prestador": cnpj_formatado,
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
                "Diferença Bruto-Líquido": formatar_valor_fn(diferenca),
                "Retenções Identificadas": texto_retencoes,
                "Valor Total Retenções": formatar_valor_fn(diferenca),
                "Status Validação": status_validacao,
                "Combinações Encontradas": qtd_comb,
            }
        )

    return registros, ignoradas


# ==========================================
# INTERFACE ONLINE (STREAMLIT MENU)
# ==========================================
st.title("📄 Processador e Importador de Notas Fiscais")
st.markdown("Selecione o tipo de arquivo de entrada para extrair os dados e validar as retenções.")

# Painel Lateral (Menu)
st.sidebar.header("Menu de Opções")
opcao = st.sidebar.radio(
    "Escolha a Origem dos Dados:",
    ["Excel - Portal Nacional (NFS-e Recebidas)", "Arquivos XML / ZIP (SIEG)"]
)

st.sidebar.markdown("---")
st.sidebar.info("Acesse a documentação das regras para verificar os campos importados.")

# Conteúdo Principal
if opcao == "Excel - Portal Nacional (NFS-e Recebidas)":
    st.subheader("📊 Importar Excel de NFS-e Recebidas")
    st.write("Envie a relação de notas baixadas diretamente do Portal Nacional (.xlsx).")

    arquivo = st.file_uploader("Selecione a planilha Excel", type=["xlsx"])

    if arquivo is not None:
        if st.button("🚀 Processar Planilha", type="primary"):
            with st.spinner("Lendo e validando retenções..."):
                try:
                    registros, ignoradas = extrair_nfse_excel(
                        arquivo, validar_retencoes, formatar_valor
                    )

                    st.success(f"Processamento concluído com sucesso!")
                    
                    # Exibição dos Indicadores
                    col1, col2 = st.columns(2)
                    col1.metric("Notas Processadas", len(registros))
                    col2.metric("Notas Ignoradas/Canceladas", len(ignoradas))

                    # Exibição em Tabelas
                    if registros:
                        st.markdown("### 📝 Notas Processadas")
                        df_registros = pd.DataFrame(registros)
                        st.dataframe(df_registros, use_container_width=True)

                        # Botão para baixar resultado consolidado
                        csv_data = df_registros.to_csv(index=False).encode('utf-8-sig')
                        st.download_button(
                            label="📥 Baixar Resultado em CSV",
                            data=csv_data,
                            file_name="nfse_processadas.csv",
                            mime="text/csv"
                        )

                    if ignoradas:
                        st.markdown("### ⚠️ Notas Ignoradas / Canceladas")
                        st.dataframe(pd.DataFrame(ignoradas), use_container_width=True)

                except Exception as e:
                    st.error(f"Erro ao processar o arquivo: {str(e)}")

elif opcao == "Arquivos XML / ZIP (SIEG)":
    st.subheader("📦 Importar XMLs / Arquivo ZIP")
    st.write("Insira os arquivos XML ou ZIP recebidos do SIEG.")
    
    arquivo_xml = st.file_uploader("Selecione o arquivo XML ou ZIP", type=["xml", "zip"], accept_multiple_files=False)
    
    if arquivo_xml is not None:
        if st.button("🚀 Processar XMLs", type="primary"):
            st.info("Aguardando integração da lógica de leitura de XML (ex: sieg_xml.py).")
