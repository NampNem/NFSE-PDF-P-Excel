elif pagina_atual == "excel_nfse":
    import io
    import pandas as pd
    from sieg_xml import (
        formatar_valor, 
        carregar_banco_dados_github,
        salvar_banco_dados_github,
        gerar_aba_alterdata,
        gerar_txt_dominio,
        CONTAS,
        NOMES_MODO
    )
    from leitor_excel import extrair_nfse_excel

    st.title("📊 Leitor de Excel NFS-e (Portal Nacional - Relação)")
    st.write("Processamento das planilhas baixadas do Portal Nacional com regras da V2.")

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
                mapa_contas = carregar_banco_dados_github()

                # Mapeia código -> descrição do serviço da planilha
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

    # GERAÇÃO E EXIBIÇÃO DOS RELATÓRIOS
    if "df_excel_processado" in st.session_state and not st.session_state["df_excel_processado"].empty:
        df_nfse = st.session_state["df_excel_processado"]
        df_ignoradas = st.session_state.get("df_excel_ignoradas", pd.DataFrame())
        ausentes = st.session_state.get("excel_codigos_ausentes", [])
        mapa_descricoes = st.session_state.get("mapa_descricoes_excel", {})
        modo = st.session_state.get("modo_excel", "alterdata")
        nome_modo = NOMES_MODO[modo]
        mapa_contas = carregar_banco_dados_github()

        st.info(f"Modo de processamento: **{nome_modo}**")

        if ausentes:
            st.warning(f"⚠️ Existem códigos de serviço sem conta {nome_modo} cadastrada!")
            conta_padrao = CONTAS[modo]["debito_padrao"]

            with st.form("form_novos_codigos_v2"):
                novos = {}
                for cod in ausentes:
                    descr = mapa_descricoes.get(cod, "Descrição do Serviço")
                    st.markdown(f"### 📌 Código: `{cod}`")
                    st.info(f"📄 **Serviço Prestado:** {cod} - {descr}")

                    conta_in = st.text_input(
                        f"Informe a conta Débito {nome_modo} para o código {cod}:",
                        value=conta_padrao,
                        key=f"v2_{cod}"
                    )
                    novos[cod] = {"conta": conta_in, "descricao": descr}
                    st.divider()
                
                if st.form_submit_button("💾 Salvar e Continuar"):
                    for cod, dados in novos.items():
                        c_limpa = dados["conta"].strip() or conta_padrao
                        existente = mapa_contas.get(cod, {"descricao": dados["descricao"], "conta": "", "conta_dominio": ""})
                        existente["descricao"] = existente.get("descricao") or dados["descricao"]
                        
                        if modo == "dominio":
                            existente["conta_dominio"] = c_limpa
                        else:
                            existente["conta"] = c_limpa
                        mapa_contas[cod] = existente

                    salvar_banco_dados_github(mapa_contas)
                    st.session_state["excel_codigos_ausentes"] = []
                    st.success("Banco de Dados Atualizado!")
                    st.rerun()

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
