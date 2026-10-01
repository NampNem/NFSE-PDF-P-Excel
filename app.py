import streamlit as st

# ============================================================
# ARQUIVO PRINCIPAL (MENU)
# Este é o arquivo que você executa. Cada sistema fica no seu
# próprio arquivo, na mesma pasta:
#   - sieg_xml.py  -> SIEG XML PARA Importação
#   - empresas.py  -> Empresas (e os sistemas de cada uma, ex.: extratos.py)
# ============================================================
st.set_page_config(page_title="Meus Sistemas", page_icon="🗂️", layout="wide")


def menu_principal():
    st.title("🗂️ Meus Sistemas")
    st.write("Escolha o sistema que deseja usar:")

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("📄 SIEG XML PARA Importação", use_container_width=True):
            st.session_state["pagina"] = "sieg"
            st.rerun()
    with col2:
        if st.button("🏢 Empresas", use_container_width=True):
            st.session_state["pagina"] = "empresas"
            st.session_state["empresa_pagina"] = "lista"
            st.rerun()


# ============================================================
# NAVEGAÇÃO ENTRE OS SISTEMAS
# Para adicionar um novo sistema:
#   1) crie um arquivo novo (ex.: meu_sistema.py) com uma função pagina_meu_sistema()
#   2) coloque um botão no menu_principal()
#   3) acrescente um bloco "elif" aqui embaixo
# ============================================================
pagina_atual = st.session_state.get("pagina", "menu")

if pagina_atual == "sieg":
    from sieg_xml import pagina_sieg_xml
    pagina_sieg_xml()
elif pagina_atual == "empresas":
    from empresas import pagina_empresas
    pagina_empresas()
else:
    menu_principal()
