# ============================================================
# GERENCIAMENTO DE PLANOS DE CONTAS COM PROPRIEDADE (GITHUB)
# ============================================================
def obter_nome_arquivo_bd(usuario_id=None, empresa_id=None):
    if empresa_id:
        return f"plano_empresa_{empresa_id}.xlsx"
    if usuario_id:
        return f"banco_user_{usuario_id}.xlsx"
    return "banco_de_dados.xlsx"


def eh_proprietario_do_banco(nome_arquivo, usuario_logado, empresas_planos):
    """Verifica se o usuário logado é o dono do banco individual ou da empresa."""
    if f"banco_user_{usuario_logado}.xlsx" == nome_arquivo:
        return True

    for cod_emp, dados in empresas_planos.items():
        if f"plano_empresa_{cod_emp}.xlsx" == nome_arquivo:
            return dados.get("criador") == usuario_logado

    return False


def carregar_banco_dados_github(nome_arquivo="banco_de_dados.xlsx"):
    mapa = {}
    if os.path.exists(nome_arquivo):
        try:
            if nome_arquivo.endswith(".csv"):
                df_bd = pd.read_csv(nome_arquivo, header=None)
            else:
                df_bd = pd.read_excel(nome_arquivo, header=None)

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
            st.error(f"Erro ao carregar o Banco de Dados ({nome_arquivo}): {e}")
    return mapa


def salvar_banco_dados_github(mapa, nome_arquivo="banco_de_dados.xlsx"):
    linhas = [
        (
            montar_celula_banco(cod, dados.get("descricao", "")),
            dados.get("conta", ""),
            dados.get("conta_dominio", ""),
        )
        for cod, dados in mapa.items()
    ]
    df_bd = pd.DataFrame(linhas)
    df_bd.to_excel(nome_arquivo, index=False, header=False)

    try:
        token = st.secrets.get("GITHUB_TOKEN")
        repo_name = st.secrets.get("REPO_NAME")

        if token and repo_name:
            g = Github(token)
            repo = g.get_repo(repo_name)

            with open(nome_arquivo, "rb") as f:
                novo_conteudo = f.read()

            try:
                contents = repo.get_contents(nome_arquivo)
                repo.update_file(
                    contents.path,
                    f"Atualizando BD: {nome_arquivo}",
                    novo_conteudo,
                    contents.sha,
                )
            except:
                repo.create_file(
                    nome_arquivo,
                    f"Criando BD: {nome_arquivo}",
                    novo_conteudo,
                )
            st.success(f"Banco de Dados ({nome_arquivo}) salvo no GitHub!")
        else:
            st.warning("Salvo apenas na sessão local.")
    except Exception as e:
        st.error(f"Erro ao salvar no GitHub ({nome_arquivo}): {e}")


def deletar_conta_do_banco(mapa, codigo_deletar, nome_arquivo):
    if codigo_deletar in mapa:
        del mapa[codigo_deletar]
        salvar_banco_dados_github(mapa, nome_arquivo)
        return True
    return False
