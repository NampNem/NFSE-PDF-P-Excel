import os
import io
import pandas as pd
import streamlit as st

from usuarios import USUARIOS_PERMITIDOS

st.set_page_config(page_title="Meus Sistemas", page_icon="🗂️", layout="wide")

# ESCONDER BARRA SUPERIOR, GITHUB, TRÊS PONTOS E RODAPÉ
st.markdown(
    """
    <style>
    header {visibility: hidden !important;}
    .stAppHeader {display: none !important;}
    [data-testid="stHeader"] {display: none !important;}
    [data-testid="stToolbar"] {display: none !important;}
    #MainMenu {visibility: hidden !important;}
    footer {visibility: hidden !important;}
    </style>
    """,
    unsafe_allow_html=True,
)


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
# SELETOR E GERENCIADOR DE PLANOS DE CONTAS DA PASTA PLANOS_EMPRESAS
# ============================================================
def selecionar_plano_de_contas():
    from sieg_xml import (
        carregar_banco_dados_github,
        salvar_banco_dados_github,
        carregar_empresas_github,
        salvar_empresas_github,
        obter_caminho_relativo_bd,
        eh_proprietario_do_banco,
        limpar_cnpj,
    )

    user_id = st.session_state["usuario_logado"]
    
    if "empresas_planos" not in st.session_state or not st.session_state["empresas_planos"]:
        st.session_state["empresas_planos"] = carregar_empresas_github()

    st.subheader("🏢 Seleção do Plano de Contas da Empresa")

    empresas = st.session_state["empresas_planos"]
    col_sel, col_novo = st.columns([2, 1])

    with col_sel:
        opcoes_emp = []
        for cod, d in empresas.items():
            cnpj_str = f" | CNPJ: {limpar_cnpj(d.get('cnpj', 'N/I'))}" if d.get('cnpj') else ""
            criador_str = USUARIOS_PERMITIDOS.get(d.get('criador'), 'Desconhecido')
            opcoes_emp.append(f"{cod} - {d['nome']}{cnpj_str} (Criador: {criador_str})")

        opcoes_emp.insert(0, "Selecione uma Empresa...")
        emp_sel = st.selectbox("Selecione a Empresa:", opcoes_emp)

    with col_novo:
        st.write("")
        with st.popover("➕ Cadastrar Nova Empresa"):
            st.markdown("### 🏢 Criar Plano de Empresa")
            cod_emp = st.text_input("Código da Empresa:")
            cnpj_emp = st.text_input("CNPJ da Empresa:")
            nome_emp = st.text_input("Nome da Empresa:")
            
            if st.button("Criar Plano de Contas", type="primary"):
                if cod_emp and cnpj_emp and nome_emp:
                    st.session_state["empresas_planos"][cod_emp] = {
                        "nome": nome_emp,
                        "cnpj": limpar_cnpj(cnpj_emp),
                        "criador": user_id
                    }
                    salvar_empresas_github(st.session_state["empresas_planos"])
                    
                    caminho_arq = obter_caminho_relativo_bd(empresa_id=cod_emp)
                    salvar_banco_dados_github({}, caminho_arq)
                    st.success(f"Plano de Contas criado em `{caminho_arq}`!")
                    st.rerun()
                else:
                    st.error("Preencha o Código, o CNPJ e o Nome da Empresa!")

    caminho_arquivo_ativo = None
    cnpj_empresa_ativa = ""
    cod_emp_ativo = None

    if emp_sel != "Selecione uma Empresa...":
        cod_emp_ativo = emp_sel.split(" - ")[0]
        caminho_arquivo_ativo = obter_caminho_relativo_bd(empresa_id=cod_emp_ativo)
        dados_emp_sel = empresas.get(cod_emp_ativo, {})
        cnpj_empresa_ativa = limpar_cnpj(dados_emp_sel.get("cnpj", ""))

    st.session_state["empresa_ativa_cod"] = cod_emp_ativo
    st.session_state["empresa_ativa_cnpj"] = cnpj_empresa_ativa

    if caminho_arquivo_ativo:
        eh_dono = eh_proprietario_do_banco(caminho_arquivo_ativo, user_id, st.session_state["empresas_planos"])
        mapa_contas = carregar_banco_dados_github(caminho_arquivo_ativo)

        if eh_dono:
            st.success(f"🔑 Plano Ativo: `{caminho_arquivo_ativo}` (Você é o **Dono** - Permissão total para editar e apagar).")
        else:
            st.info(f"👁️ Plano Ativo: `{caminho_arquivo_ativo}` (**Apenas Leitura** - Pertence a outro usuário).")

        return mapa_contas, caminho_arquivo_ativo, eh_dono

    return None, None, False


# ============================================================
# TELA DEDICADA: ALTERAR PLANO DE CONTAS
# ============================================================
def pagina_alterar_plano_de_contas():
    from sieg_xml import (
        carregar_banco_dados_github,
        salvar_banco_dados_github,
        deletar_conta_do_banco,
        deletar_empresa_completa_github,
    )

    st.title("📝 Alterar Plano de Contas")
    
    mapa_contas, caminho_arquivo_ativo, eh_dono = selecionar_plano_de_contas()
    cod_emp_ativo = st.session_state.get("empresa_ativa_cod")
    st.markdown("---")

    if caminho_arquivo_ativo and cod_emp_ativo:
        if eh_dono:
            col_tit, col_del_emp = st.columns([3, 1])
            with col_del_emp:
                with st.popover("🗑️ Apagar Empresa / Plano", use_container_width=True):
                    st.warning("⚠️️ Esta ação vai apagar permanentemente esta empresa e o plano de contas dela!")
                    st.write(f"Empresa Código: **{cod_emp_ativo}**")
                    if st.button("Confirmar Exclusão Definitiva", type="primary", key="btn_confirm_del_emp"):
                        if deletar_empresa_completa_github(cod_emp_ativo, st.session_state["empresas_planos"]):
                            st.success("Empresa e Plano de Contas apagados com sucesso!")
                            st.session_state["empresas_planos"] = {}
                            st.rerun()

            if mapa_contas:
                st.subheader("⚙️ Gerenciar / Alterar Contas Existentes")
                cod_sel_editar = st.selectbox("Selecione o Código do Serviço para alterar:", list(mapa_contas.keys()))
                
                if cod_sel_editar:
                    dados_atuais = mapa_contas[cod_sel_editar]
                    st.write(f"**Serviço:** {dados_atuais.get('descricao', 'Sem Descrição')}")
                    
                    st.markdown("##### 🛒 Contas de DESPESA (Tomador)")
                    c_alt_desp, c_dom_desp = st.columns(2)
                    with c_alt_desp:
                        nova_cnt_alt = st.text_input("Conta Alterdata (Despesa):", value=str(dados_atuais.get("conta", "")), key=f"edit_alt_{cod_sel_editar}")
                    with c_dom_desp:
                        nova_cnt_dom = st.text_input("Conta Domínio (Despesa):", value=str(dados_atuais.get("conta_dominio", "")), key=f"edit_dom_{cod_sel_editar}")

                    st.markdown("##### 💰 Contas de RECEITA (Prestador)")
                    c_alt_rec, c_dom_rec = st.columns(2)
                    with c_alt_rec:
                        nova_cnt_alt_rec = st.text_input("Conta Alterdata (Receita):", value=str(dados_atuais.get("conta_rec", "")), key=f"edit_alt_rec_{cod_sel_editar}")
                    with c_dom_rec:
                        nova_cnt_dom_rec = st.text_input("Conta Domínio (Receita):", value=str(dados_atuais.get("conta_dominio_rec", "")), key=f"edit_dom_rec_{cod_sel_editar}")

                    st.write("")
                    col_salv, col_del = st.columns(2)
                    with col_salv:
                        if st.button("💾 Salvar Alterações", key=f"btn_save_{cod_sel_editar}", type="primary"):
                            mapa_contas[cod_sel_editar]["conta"] = nova_cnt_alt.strip()
                            mapa_contas[cod_sel_editar]["conta_dominio"] = nova_cnt_dom.strip()
                            mapa_contas[cod_sel_editar]["conta_rec"] = nova_cnt_alt_rec.strip()
                            mapa_contas[cod_sel_editar]["conta_dominio_rec"] = nova_cnt_dom_rec.strip()
                            salvar_banco_dados_github(mapa_contas, caminho_arquivo_ativo)
                            st.success("Contas atualizadas no GitHub!")
                            st.rerun()
                    with col_del:
                        if st.button("❌ Apagar Código", key=f"btn_del_{cod_sel_editar}"):
                            if deletar_conta_do_banco(mapa_contas, cod_sel_editar, caminho_arquivo_ativo):
                                st.success(f"Código {cod_sel_editar} apagado com sucesso!")
                                st.rerun()

                st.markdown("---")
                st.subheader("📊 Tabela Completa das Contas Cadastradas")
                
                linhas_tbl = []
                for cod_item, d_item in mapa_contas.items():
                    linhas_tbl.append({
                        "Código Serviço": cod_item,
                        "Descrição": d_item.get("descricao", ""),
                        "Alterdata (Despesa)": d_item.get("conta", ""),
                        "Domínio (Despesa)": d_item.get("conta_dominio", ""),
                        "Alterdata (Receita)": d_item.get("conta_rec", ""),
                        "Domínio (Receita)": d_item.get("conta_dominio_rec", ""),
                    })
                st.dataframe(pd.DataFrame(linhas_tbl), use_container_width=True)
            else:
                st.info("Este Plano de Contas está em branco no momento. Ele será preenchido automaticamente ao processar notas.")

        else:
            st.error("⚠️ Você não tem permissão para alterar ou apagar esta empresa. Apenas o criador (dono) do plano tem essa autorização.")
            
            if mapa_contas:
                st.subheader("📊 Visualização das Contas (Modo Leitura)")
                linhas_tbl = []
                for cod_item, d_item in mapa_contas.items():
                    linhas_tbl.append({
                        "Código Serviço": cod_item,
                        "Descrição": d_item.get("descricao", ""),
                        "Alterdata (Despesa)": d_item.get("conta", ""),
                        "Domínio (Despesa)": d_item.get("conta_dominio", ""),
                        "Alterdata (Receita)": d_item.get("conta_rec", ""),
                        "Domínio (Receita)": d_item.get("conta_dominio_rec", ""),
                    })
                st.dataframe(pd.DataFrame(linhas_tbl), use_container_width=True)


# ============================================================
# MENU PRINCIPAL
# ============================================================
def menu_principal():
    st.sidebar.markdown(f"👤 Logado como: **{st.session_state['usuario_nome']}** (`{st.session_state['usuario_logado']}`)")
    if st.sidebar.button("🚪 Sair / Logoff"):
        del st.session_state["usuario_logado"]
        st.rerun()

    st.title("🗂️ Meus Sistemas")
    st.write("Escolha a opção que deseja acessar:")

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
        if st.button("📝 Alterar Plano de Contas", use_container_width=True):
            st.session_state["pagina"] = "alterar_plano"
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
# OPÇÃO 1: SIEG XML
# ------------------------------------------------------------
if pagina_atual == "sieg":
    from sieg_xml import pagina_sieg_xml
    mapa_contas, caminho_arquivo_bd, eh_dono = selecionar_plano_de_contas()
    st.markdown("---")
    if mapa_contas is not None:
        pagina_sieg_xml(mapa_contas, caminho_arquivo_bd, eh_dono)

# ------------------------------------------------------------
# OPÇÃO 2: EXCEL NFSE
# ------------------------------------------------------------
elif pagina_atual == "excel_nfse":
    from sieg_xml import (
        formatar_valor, 
        salvar_banco_dados_github,
        gerar_aba_alterdata,
        gerar_txt_dominio,
        limpar_cnpj,
        codigo_precisa_cadastro,
        CONTAS,
        NOMES_MODO,
    )
    from leitor_excel import extrair_nfse_excel

    mapa_contas, caminho_arquivo_bd, eh_dono = selecionar_plano_de_contas()

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
                    
                    # DETERMINA RECEITA x DESPESA
                    cnpj_emp_sel = limpar_cnpj(st.session_state.get("empresa_ativa_cnpj", ""))
                    cnpjs_prest_lote = set(df_nfse["CNPJ Prestador"].dropna().apply(limpar_cnpj).unique()) if not df_nfse.empty and "CNPJ Prestador" in df_nfse.columns else set()
                    eh_receita = bool(cnpj_emp_sel and cnpj_emp_sel in cnpjs_prest_lote)
                    st.session_state["eh_receita_excel"] = eh_receita

                    codigos_ausentes = []
                    if not df_nfse.empty:
                        for _, row in df_nfse.iterrows():
                            cod = row.get("Código Tributação")
                            tipo = row.get("Tipo de Serviço")
                            if cod and cod not in mapa_descricoes:
                                mapa_descricoes[cod] = tipo
                            if cod and codigo_precisa_cadastro(mapa_contas, cod, modo, eh_receita=eh_receita):
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
            eh_receita = st.session_state.get("eh_receita_excel", False)

            if eh_receita:
                st.success("💰 **TIPO DE OPERAÇÃO: RECEITA (SERVIÇOS PRESTADOS)**")
            else:
                st.info("🛒 **TIPO DE OPERAÇÃO: DESPESA (SERVIÇOS TOMADOS)**")

            if ausentes:
                if eh_dono:
                    st.warning(f"⚠️ Existem códigos de serviço sem conta de {'RECEITA' if eh_receita else 'DESPESA'} {nome_modo} cadastrada!")
                    st.write("Configure abaixo **um a um**. Pressione **Enter** em cada caixa para salvar individualmente:")
                    conta_padrao = CONTAS[modo]["credito_receita"] if eh_receita else CONTAS[modo]["debito_padrao"]

                    for cod in list(ausentes):
                        descr = mapa_descricoes.get(cod, "Descrição do Serviço")
                        
                        with st.form(key=f"form_single_v2_{modo}_{cod}"):
                            st.markdown(f"#### 📌 Código: `{cod}`")
                            st.info(f"📄 **Serviço Prestado:** {cod} - {descr}")

                            label_campo = f"Informe a conta Crédito (RECEITA) {nome_modo}:" if eh_receita else f"Informe a conta Débito (DESPESA) {nome_modo}:"
                            conta_in = st.text_input(
                                label_campo,
                                value=conta_padrao,
                                key=f"input_single_v2_{modo}_{cod}"
                            )
                            btn_salvar_indiv = st.form_submit_button(f"💾 Salvar Conta para Código {cod}")

                            if btn_salvar_indiv:
                                c_limpa = conta_in.strip() or conta_padrao
                                existente = mapa_contas.get(cod, {"descricao": descr, "conta": "", "conta_dominio": "", "conta_rec": "", "conta_dominio_rec": ""})
                                existente["descricao"] = existente.get("descricao") or descr
                                
                                if eh_receita:
                                    if modo == "dominio":
                                        existente["conta_dominio_rec"] = c_limpa
                                    else:
                                        existente["conta_rec"] = c_limpa
                                else:
                                    if modo == "dominio":
                                        existente["conta_dominio"] = c_limpa
                                    else:
                                        existente["conta"] = c_limpa

                                mapa_contas[cod] = existente

                                salvar_banco_dados_github(mapa_contas, caminho_arquivo_bd)
                                st.session_state["excel_codigos_ausentes"].remove(cod)
                                st.success(f"Conta para o código {cod} salva com sucesso!")
                                st.rerun()
                        st.divider()

                else:
                    st.error(f"⚠️️ Existem códigos sem conta cadastrada (`{', '.join(ausentes)}`). Como você está no modo apenas leitura, peça ao dono do plano para registrá-los.")

            else:
                st.subheader(f"🧾 Contas dos Impostos e Contrapartida - {nome_modo}")
                padrao = CONTAS[modo]
                rotulo_principal = "Clientes (Débito)" if eh_receita else "Fornecedores (Crédito)"
                
                campos_contas = [
                    ("credito_principal", rotulo_principal),
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

                df_lancamentos = gerar_aba_alterdata(df_nfse, mapa_contas, modo, contas_editadas, eh_receita=eh_receita)
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

# ------------------------------------------------------------
# OPÇÃO 3: ALTERAR PLANO DE CONTAS
# ------------------------------------------------------------
elif pagina_atual == "alterar_plano":
    pagina_alterar_plano_de_contas()

else:
    menu_principal()
