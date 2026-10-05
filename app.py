import streamlit as st

# ============================================================
# ARQUIVO PRINCIPAL (MENU)
# ============================================================
st.set_page_config(page_title="Meus Sistemas", page_icon="🗂️", layout="wide")


def menu_principal():
    st.title("🗂️️ Meus Sistemas")
    st.write("Escolha o sistema que deseja usar:")

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("📄 SIEG XML / PDF PARA Importação", use_container_width=True):
            st.session_state["pagina"] = "sieg"
            st.rerun()
    with col2:
        if st.button("📊 Excel NFS-e (Portal Nacional)", use_container_width=True):
            st.session_state["pagina"] = "excel_nfse"
            st.rerun()
    with col3:
        if st.button("🏢 Empresas", use_container_width=True):
            st.session_state["pagina"] = "empresas"
            st.session_state["empresa_pagina"] = "lista"
            st.rerun()


# ============================================================
# NAVEGAÇÃO ENTRE OS SISTEMAS
# ============================================================
pagina_atual = st.session_state.get("pagina", "menu")

# Botão lateral para voltar ao Menu Principal em qualquer tela
if pagina_atual != "menu":
    if st.sidebar.button("⬅️ Voltar ao Menu Principal"):
        st.session_state["pagina"] = "menu"
        st.rerun()

# ------------------------------------------------------------
# PAGINA 1: LEITOR DE XML / PDF (SIEG)
# ------------------------------------------------------------
if pagina_atual == "sieg":
    try:
        from sieg_xml import pagina_sieg_xml
        pagina_sieg_xml()
    except Exception as e:
        st.error(f"Erro ao carregar o módulo SIEG XML: {e}")

# ------------------------------------------------------------
# PAGINA 2: LEITOR DE EXCEL (PORTAL NACIONAL)
# ------------------------------------------------------------
elif pagina_atual == "excel_nfse":
    import pandas as pd
    
    try:
        from sieg_xml import validar_retencoes, formatar_valor
        from leitor_excel import extrair_nfse_excel

        st.title("📊 Leitor de Excel NFS-e (Portal Nacional)")
        st.write("Envie a planilha `.xlsx` de NFS-e Recebidas baixada do Portal Nacional.")

        arquivo_excel = st.file_uploader("Selecione a planilha Excel", type=["xlsx"])

        if arquivo_excel is not None:
            if st.button("🚀 Processar Planilha", type="primary"):
                with st.spinner("Lendo e aplicando validações de retenção..."):
                    registros, ignoradas = extrair_nfse_excel(
                        arquivo_excel, 
                        validar_retencoes=validar_retencoes, 
                        formatar_valor=formatar_valor
                    )

                    st.success("Planilha processada com sucesso!")

                    c1, c2 = st.columns(2)
                    c1.metric("Notas Processadas", len(registros))
                    c2.metric("Notas Ignoradas / Canceladas", len(ignoradas))

                    if registros:
                        st.markdown("### 📝 Registros Validados")
                        df_reg = pd.DataFrame(registros)
                        st.dataframe(df_reg, use_container_width=True)

                        csv = df_reg.to_csv(index=False).encode('utf-8-sig')
                        st.download_button(
                            label="📥 Baixar Resultado em CSV",
                            data=csv,
                            file_name="nfse_excel_processadas.csv",
                            mime="text/csv"
                        )

                    if ignoradas:
                        st.markdown("### ⚠️ Notas Ignoradas ou Canceladas")
                        st.dataframe(pd.DataFrame(ignoradas), use_container_width=True)

    except Exception as e:
        st.error(f"Erro ao carregar o leitor de Excel: {e}")

# ------------------------------------------------------------
# PAGINA 3: EMPRESAS
# ------------------------------------------------------------
elif pagina_atual == "empresas":
    try:
        from empresas import pagina_empresas
        pagina_empresas()
    except Exception as e:
        st.error(f"Erro ao carregar o módulo de empresas: {e}")

# ------------------------------------------------------------
# MENU INICIAL
# ------------------------------------------------------------
else:
    menu_principal()
