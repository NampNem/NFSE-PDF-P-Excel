import streamlit as st

# ============================================================
# EMPRESAS
# Tela com os sistemas de cada empresa. Cada botão abre um sistema
# que fica em um arquivo próprio (ex.: extratos.py).
#
# Para adicionar um novo item aqui:
#   1) crie o arquivo do sistema (ex.: contas_pagar.py) com uma função pagina_xxx()
#   2) coloque um botão em lista_empresas()
#   3) acrescente um bloco "elif" em pagina_empresas()
# ============================================================


def lista_empresas():
    if st.button("⬅️ Voltar ao menu", key="voltar_menu_empresas"):
        st.session_state["pagina"] = "menu"
        st.rerun()

    st.title("🏢 Empresas")
    st.write("Escolha a empresa / sistema:")

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("📊 Extratos - Costa Verde", use_container_width=True):
            st.session_state["empresa_pagina"] = "extratos_costa_verde"
            st.rerun()


def pagina_empresas():
    destino = st.session_state.get("empresa_pagina", "lista")

    if destino == "extratos_costa_verde":
        if st.button("⬅️ Voltar para Empresas", key="voltar_empresas"):
            st.session_state["empresa_pagina"] = "lista"
            st.rerun()

        from extratos import pagina_extratos
        pagina_extratos()
    else:
        lista_empresas()
