import os
import io
import pandas as pd
import streamlit as st

from usuarios import USUARIOS_PERMITIDOS

st.set_page_config(page_title="Meus Sistemas", page_icon="🗂️", layout="wide")


# ============================================================
# TELA DE LOGIN
# ============================================================
def tela_login():
    st.markdown("<h1 style='text-align: center;'>🔒 Acesso ao Sistema</h1>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        pin = st.text_input("Digite seu código de acesso (3 dígitos):", type="password", max_chars=3)
        if st.button("Entrar", use_container_width=True, type="primary"):
            if pin in USUARIOS_PERMITIDOS:
                st.session_state["usuario_logado"] = pin
                st.session_state["usuario_nome"] = USUARIOS_PERMITIDOS[pin]
                st.session_state["pagina"] = "menu"
                st.rerun()
            else:
                st.error("Código de acesso inválido!")


if "usuario_logado" not in st.session_state:
    tela_login()
    st.stop()


# ============================================================
# SELETOR E GERENCIADOR DE PLANOS DE CONTAS
# ============================================================
def selecionar_plano_de_contas():
    from sieg_xml import (
        carregar_banco_dados_github,
        salvar_banco_dados_github,
        obter_nome_arquivo_bd,
        eh_proprietario_do_banco,
        deletar_conta_do_banco
    )

    user_id = st.session_state["usuario_logado"]
    if "empresas_planos" not in st.session_state:
        st.session_state["empresas_planos"] = {}

    st.subheader("⚙️ Seleção do Plano de Contas")

    # Opção para alternar entre Empresa ou Banco Individual de qualquer usuário
    origem_plano = st.radio(
        "Origem do Plano de Contas:",
        ["Plano da Empresa (Compartilhado)", "Plano do Usuário"],
        horizontal=True
    )

    nome_arquivo_ativo = None

    # 1. PLANOS POR EMPRESA
    if origem_plano == "Plano da Empresa (Compartilhado)":
        empresas = st.session_state["empresas_planos"]
        col_sel, col_novo = st.columns([2, 1])

        with col_sel:
            opcoes_emp = [
                f"{cod} - {d['nome']} (Criador: {USUARIOS_PERMITIDOS.get(d['criador'], 'Desconhecido')})" 
                for cod, d in empresas.items()
            ]
            opcoes_emp.insert(0, "Selecione uma Empresa...")
            emp_sel = st.selectbox("Selecione a Empresa:", opcoes_emp)

        with col_novo:
            st.write("")
            with st.popover("➕ Cadastrar Nova Empresa"):
                st.markdown("### 🏢 Novo Plano de Empresa")
                cod_emp = st.text_input("Código da Empresa:")
                nome_emp = st.text_input("Nome da Empresa:")
                if st.button("Criar Plano de Contas", type="primary"):
                    if cod_emp and nome_emp:
                        st.session_state["empresas_planos"][cod_emp] = {
                            "nome": nome_emp,
                            "criador": user_id
                        }
                        nome_arq = obter_nome_arquivo_bd(empresa_id=cod_emp)
                        salvar_banco_dados_github({}, nome_arq)
                        st.success(f"Plano de Contas criado para {nome_emp}!")
                        st.rerun()
                    else:
                        st.error("Preencha o Código e o Nome da Empresa!")

        if emp_sel != "Selecione uma Empresa...":
            cod_emp = emp_sel.split(" - ")[0]
            nome_arquivo_ativo = obter_nome_arquivo_bd(empresa_id=cod_emp)

    # 2. PLANOS POR USUÁRIO (VISIBILIDADE TOTAL)
    else:
        opcoes_usr = [f"{u_id} - {nome}" for u_id, nome in USUARIOS_PERMITIDOS.items()]
        idx_padrao = list(USUARIOS_PERMITIDOS.keys()).index(user_id) if user_id in USUARIOS_PERMITIDOS else 0
        usr_sel = st.selectbox("Selecione o Usuário para carregar o Plano dele:", opcoes_usr, index=idx_padrao)
        
        target_id = usr_sel.split(" - ")[0]
        nome_arquivo_ativo = obter_nome_arquivo_bd(usuario_id=target_id)

    if nome_arquivo_ativo:
        eh_dono = eh_proprietario_do_banco(nome_arquivo_ativo, user_id, st.session_state["empresas_planos"])
        mapa_contas = carregar_banco_dados_github(nome_arquivo_ativo)

        if eh_dono:
            st.success(f"🔑 Plano Ativo: `{nome_arquivo_ativo}` (Você é o **Dono** - Permissão total para editar/apagar).")
        else:
            st.info(f"👁️ Plano Ativo: `{nome_arquivo_ativo}` (**Apenas Leitura** - Pertence a outro usuário/empresa).")

        # GERENCIAMENTO DE CONTAS EXISTENTES (SÓ O DONO CONSEGUE EDITAR / REMOVER)
        if eh_dono and mapa_contas:
            with st.expander("📝 Editar ou Apagar Contas Cadastradas neste Plano"):
                cod_sel_editar = st.selectbox("Selecione o Código do Serviço:", list(mapa_contas.keys()))
                
                if cod_sel_editar:
                    dados_atuais = mapa_contas[cod_sel_editar]
                    st.write(f"**Serviço:** {dados_atuais.get('descricao', 'Sem Descrição')}")
                    
                    c_alt, c_dom, c_btns = st.columns([2, 2, 2])
                    with c_alt:
                        nova_cnt_alt = st.text_input("Conta Alterdata:", value=str(dados_atuais.get("conta", "")), key=f"edit_alt_{cod_sel_editar}")
                    with c_dom:
                        nova_cnt_dom = st.text_input("Conta Domínio:", value=str(dados_atuais.get("conta_dominio", "")), key=f"edit_dom_{cod_sel_editar}")
                    
                    with c_btns:
                        st.write("")
                        st.write("")
                        col_salv, col_del = st.columns(2)
                        with col_salv:
                            if st.button("💾 Salvar Alteração", key=f"btn_save_{cod_sel_editar}"):
                                mapa_contas[cod_sel_editar]["conta"] = nova_cnt_alt.strip()
                                mapa_contas[cod_sel_editar]["conta_dominio"] = nova_cnt_dom.strip()
                                salvar_banco_dados_github(mapa_contas, nome_arquivo_ativo)
                                st.success("Conta atualizada!")
                                st.rerun()
                        with col_del:
                            if st.button("❌ Apagar Código", key=f"btn_del_{cod_sel_editar}"):
                                if deletar_conta_do_banco(mapa_contas, cod_sel_editar, nome_arquivo_ativo):
                                    st.success(f"Código {cod_sel_editar} apagado!")
                                    st.rerun()

        return mapa_contas, nome_arquivo_ativo, eh_dono

    return None, None, False


# ============================================================
# MENU PRINCIPAL
# ============================================================
def menu_principal():
    st.sidebar.markdown(f"👤 Logado como: **{st.session_state['usuario_nome']}** (`{st.session_state['usuario_logado']}`)")
    if st.sidebar.button("🚪 Sair / Logoff"):
        del st.session_state["usuario_logado"]
        st.rerun()

    st.title("🗂️ Meus Sistemas")
    st.write("Escolha o sistema que deseja usar:")

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("📄 SIEG XML PARA Importação", use_container_width=True):
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

if pagina_atual != "menu":
    st.sidebar.markdown(f"👤 **{st.session_state['usuario_nome']}**")
    if st.sidebar.button("⬅️ Voltar ao Menu Principal"):
        st.session_state["pagina"] = "menu"
        st.rerun()

# ------------------------------------------------------------
# SIEG XML
# ------------------------------------------------------------
if pagina_atual == "sieg":
    from sieg_xml import pagina_sieg_xml
    mapa_contas, nome_arquivo_bd, eh_dono = selecionar_plano_de_contas()
    st.markdown("---")
    if mapa_contas is not None:
        pagina_sieg_xml(mapa_contas, nome_arquivo_bd, eh_dono)

# ------------------------------------------------------------
# EXCEL NFSE (CONFIRMAÇÃO 1 POR 1 AO APERTAR ENTER)
# ------------------------------------------------------------
elif pagina_atual == "excel_nfse":
    from sieg_xml import (
        formatar_valor, 
        salvar_banco_dados_github,
        gerar_aba_alterdata,
        gerar_txt_dominio,
        CONTAS,
        NOMES_MODO
    )
    from leitor_excel import extrair_nfse_excel

    mapa_contas, nome_arquivo_bd, eh_dono = selecionar_plano_de_contas()

    if mapa_contas is not None:
        st.markdown("---")
        arquivo_excel = st.file_uploader("Selecione a planilha Excel (.xlsx)", type=["xlsx"])

        if arquivo_excel is not None:
            col_btn1, col_btn2, _ = st.columns([1, 1, 2])
            with col_btn1:
                processar_alterdata = st.button("🚀 Processar para Alterdata")
            with col_btn2:
                processar_dominio = st.button("🚀 Processar para Domínio")

            if processar_alterdata or processar_dominio:
                modo = "dominio" if processar_dominio else "alterdata"
                st.session_state["modo_excel"] = modo

                with st.spinner("Processando dados e aplicando regras V2..."):
                    registros, ignoradas = extrair_nfse_excel(arquivo_excel, formatar_valor)
                    
                    df_nfse = pd.DataFrame(registros)
                    mapa_descricoes = {}
                    codigos_ausentes = []
                    
                    if not df_nfse.empty:
                        for _, row in df_nfse.iterrows():
                            cod = row.get("Código Tributação")
                            tipo = row.get("Tipo de Serviço")
                            if cod and cod not in mapa_descricoes:
                                mapa_descricoes[cod] = tipo
                            if cod and not mapa_contas.get(cod):
                                if cod not in codigos_ausentes:
                                    codigos_ausentes.append(cod)

                    st.session_state["df_excel_processado"] = df_nfse
                    st.session_state["df_excel_ignoradas"] = pd.DataFrame(ignoradas)
                    st.session_state["excel_codigos_ausentes"] = codigos_ausentes
                    st.session_state["mapa_descricoes_excel"] = mapa_descricoes

        if "df_excel_processado" in st.session_state and not st.session_state["df_excel_processado"].empty:
            df_nfse = st.session_state["df_excel_processado"]
            df_ignoradas = st.session_state.get("df_excel_ignoradas", pd.DataFrame())
            ausentes = st.session_state.get("excel_codigos_ausentes", [])
            mapa_descricoes = st.session_state.get("mapa_descricoes_excel", {})
            modo = st.session_state.get("modo_excel", "alterdata")
            nome_modo = NOMES_MODO[modo]

            st.info(f"Modo de processamento: **{nome_modo}**")

            if ausentes:
                if eh_dono:
                    st.warning(f"⚠️️ Existem códigos de serviço sem conta {nome_modo} cadastrada neste Plano de Contas!")
                    st.write("Configure abaixo **um a um**. Pressione **Enter** em cada caixa para salvar individualmente:")
                    conta_padrao = CONTAS[modo]["debito_padrao"]

                    # Formulários 1 por 1 individuais
                    for cod in list(ausentes):
                        descr = mapa_descricoes.get(cod, "Descrição do Serviço")
                        
                        with st.form(key=f"form_single_v2_{modo}_{cod}"):
                            st.markdown(f"#### 📌 Código: `{cod}`")
                            st.info(f"📄 **Serviço Prestado:** {cod} - {descr}")

                            conta_in = st.text_input(
                                f"Informe a conta Débito {nome_modo} para `{cod}`:",
                                value=conta_padrao,
                                key=f"input_single_v2_{modo}_{cod}"
                            )
                            btn_salvar_indiv = st.form_submit_button(f"💾 Salvar Conta para Código {cod}")

                            if btn_salvar_indiv:
                                c_limpa = conta_in.strip() or conta_padrao
                                existente = mapa_contas.get(cod, {"descricao": descr, "conta": "", "conta_dominio": ""})
                                existente["descricao"] = existente.get("descricao") or descr
                                
                                if modo == "dominio":
                                    existente["conta_dominio"] = c_limpa
                                else:
                                    existente["conta"] = c_limpa
                                mapa_contas[cod] = existente

                                salvar_banco_dados_github(mapa_contas, nome_arquivo_bd)
                                st.session_state["excel_codigos_ausentes"].remove(cod)
                                st.success(f"Conta para o código {cod} salva com sucesso!")
                                st.rerun()
                        st.divider()

                else:
                    st.error(f"⚠️ Existem códigos sem conta cadastrada (`{', '.join(ausentes)}`). Como você está no modo apenas leitura, peça ao dono do plano para registrá-los.")

            else:
                st.subheader(f"🧾 Contas dos Impostos Retidos - {nome_modo}")
                padrao = CONTAS[modo]
                campos_contas = [
                    ("credito_principal", "Fornecedores (crédito)"),
                    ("pcc", "PIS / COFINS / CSLL"),
                    ("irrf", "IRRF"),
                    ("inss", "INSS"),
                    ("iss", "ISS"),
                    ("historico", "Histórico Padrão"),
                ]

                contas_editadas = dict(padrao)
                cols = st.columns(len(campos_contas))
                for col, (chave, rotulo) in zip(cols, campos_contas):
                    with col:
                        v_dig = st.text_input(rotulo, value=padrao.get(chave, ""), key=f"v2_cnt_{modo}_{chave}")
                        contas_editadas[chave] = v_dig.strip() or padrao.get(chave, "")

                df_lancamentos = gerar_aba_alterdata(df_nfse, mapa_contas, modo, contas_editadas)
                nome_aba = "Domínio" if modo == "dominio" else "Alterdata"

                st.subheader(f"📊 Prévia dos Lançamentos ({nome_aba})")
                st.dataframe(df_lancamentos, use_container_width=True)

                if not df_ignoradas.empty:
                    st.subheader("⚠️ Notas Canceladas / Ignoradas")
                    st.dataframe(df_ignoradas, use_container_width=True)

                buffer_excel = io.BytesIO()
                with pd.ExcelWriter(buffer_excel, engine="openpyxl", date_format="dd/mm/yyyy") as writer:
                    df_lancamentos.to_excel(writer, index=False, sheet_name=nome_aba)
                    df_nfse.to_excel(writer, index=False, sheet_name="NFS-e Extraídas")

                st.markdown("---")
                st.subheader("📥 Downloads Disponíveis")

                if modo == "dominio":
                    lote_init = st.number_input("Nº do primeiro lote (Domínio)", min_value=1, value=1, step=1)
                    txt_dominio = gerar_txt_dominio(df_lancamentos, lote_init)

                    c1, c2 = st.columns(2)
                    with c1:
                        st.download_button("📄 Baixar Layout Domínio (.txt)", data=txt_dominio, file_name="importacao_dominio_excel.txt", mime="text/plain")
                    with c2:
                        st.download_button("📊 Baixar Planilha Domínio (.xlsx)", data=buffer_excel.getvalue(), file_name="importacao_dominio_excel.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                else:
                    st.download_button("📊 Baixar Planilha Alterdata (.xlsx)", data=buffer_excel.getvalue(), file_name="importacao_alterdata_excel.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

elif pagina_atual == "empresas":
    from empresas import pagina_empresas
    pagina_empresas()

else:
    menu_principal()
