"""
Interface Streamlit (app.py)
Conecta o leitor de Excel e o sieg_xml.py para rodar online.
"""
import streamlit as st
import pandas as pd

# Importa as funções dos seus arquivos de regras e leitura
try:
    from sieg_xml import extrair_xml, validar_retencoes, formatar_valor
except ImportError:
    st.error("Arquivo 'sieg_xml.py' não encontrado na pasta do projeto.")

try:
    from leitor_excel import extrair_nfse_excel
except ImportError:
    # Caso ainda não tenha criado leitor_excel.py, busca no mesmo arquivo se houver
    pass

st.set_page_config(
    page_title="Sistema de Importação NFS-e & XML",
    page_icon="📄",
    layout="wide"
)

st.title("📄 Processador e Importador de Notas Fiscais")

# Menu de Navegação na Barra Lateral
st.sidebar.header("Menu de Opções")
opcao = st.sidebar.radio(
    "Escolha a Origem dos Dados:",
    [
        "Arquivos XML / PDF / ZIP (SIEG)",
        "Excel - Portal Nacional (NFS-e Recebidas)"
    ]
)

st.sidebar.markdown("---")

# -------------------------------------------------------------
# OPÇÃO 1: PROCESSAMENTO DE XMLs / PDFs / ZIPs (SIEG)
# -------------------------------------------------------------
if opcao == "Arquivos XML / PDF / ZIP (SIEG)":
    st.subheader("📦 Importar XMLs, PDFs ou ZIP (via SIEG)")
    st.write("Envie arquivos XML, PDF ou um arquivo ZIP contendo as notas.")

    arquivos_enviados = st.file_uploader(
        "Selecione os arquivos", 
        type=["xml", "pdf", "zip"], 
        accept_multiple_files=True
    )

    if arquivos_enviados:
        if st.button("🚀 Processar Arquivos SIEG", type="primary"):
            with st.spinner("Processando arquivos com as regras do sieg_xml.py..."):
                try:
                    # Chama a função nativa do seu sieg_xml.py
                    registros, ignoradas = extrair_xml(arquivos_enviados)

                    st.success("Processamento de XMLs/PDFs concluído!")

                    col1, col2 = st.columns(2)
                    col1.metric("Notas Processadas", len(registros))
                    col2.metric("Notas Ignoradas / Outros", len(ignoradas))

                    if registros:
                        st.markdown("### 📝 Registros Extraídos")
                        df_reg = pd.DataFrame(registros)
                        st.dataframe(df_reg, use_container_width=True)

                        csv = df_reg.to_csv(index=False).encode('utf-8-sig')
                        st.download_button(
                            "📥 Baixar Relatório em CSV",
                            data=csv,
                            file_name="notas_sieg_processadas.csv",
                            mime="text/csv"
                        )

                    if ignoradas:
                        st.markdown("### ⚠️ Ignoradas / Divergentes")
                        st.dataframe(pd.DataFrame(ignoradas), use_container_width=True)

                except Exception as e:
                    st.error(f"Erro ao processar arquivos: {str(e)}")

# -------------------------------------------------------------
# OPÇÃO 2: PROCESSAMENTO DE EXCEL (PORTAL NACIONAL)
# -------------------------------------------------------------
elif opcao == "Excel - Portal Nacional (NFS-e Recebidas)":
    st.subheader("📊 Importar Excel de NFS-e Recebidas")
    st.write("Envie a planilha baixada do Portal Nacional (.xlsx).")

    arquivo_excel = st.file_uploader("Selecione a planilha Excel", type=["xlsx"])

    if arquivo_excel is not None:
        if st.button("🚀 Processar Planilha", type="primary"):
            with st.spinner("Lendo planilha e aplicando validações..."):
                try:
                    # Passa a função validar_retencoes e formatar_valor do sieg_xml.py como parâmetro
                    registros, ignoradas = extrair_nfse_excel(
                        arquivo_excel, 
                        validar_retencoes=validar_retencoes, 
                        formatar_valor=formatar_valor
                    )

                    st.success("Processamento do Excel concluído!")

                    col1, col2 = st.columns(2)
                    col1.metric("Notas Processadas", len(registros))
                    col2.metric("Notas Ignoradas", len(ignoradas))

                    if registros:
                        st.markdown("### 📝 Notas Processadas")
                        df_excel = pd.DataFrame(registros)
                        st.dataframe(df_excel, use_container_width=True)

                        csv = df_excel.to_csv(index=False).encode('utf-8-sig')
                        st.download_button(
                            "📥 Baixar Resultado Excel em CSV",
                            data=csv,
                            file_name="nfse_excel_processadas.csv",
                            mime="text/csv"
                        )

                    if ignoradas:
                        st.markdown("### ⚠️ Notas Ignoradas / Canceladas")
                        st.dataframe(pd.DataFrame(ignoradas), use_container_width=True)

                except Exception as e:
                    st.error(f"Erro ao processar o Excel: {str(e)}")
