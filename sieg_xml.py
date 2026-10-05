"""
Módulo SIEG XML / PDF / ZIP
Responsável pelo processamento de arquivos XML de NFS-e/NFe, PDFs e validação de retenções.
"""
import io
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile

import pandas as pd
import streamlit as st


# ==========================================
# FUNÇÕES DE FORMATAÇÃO E VALIDAÇÃO
# ==========================================
def formatar_valor(valor):
    """Formata número para o padrão de moeda BRL (ex: R$ 1.250,50)."""
    try:
        return f"R$ {float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (ValueError, TypeError):
        return "R$ 0,00"


def validar_retencoes(base, liquido, impostos):
    """
    Valida as retenções de impostos comparando o valor bruto/base com o líquido.
    Retorna: (dict_retidos, status_validacao, qtd_combinacoes)
    """
    diferenca = round(base - liquido, 2)
    soma_impostos = round(sum(impostos.values()), 2)

    retidos = {k: v > 0 for k, v in impostos.items()}
    
    if abs(diferenca - soma_impostos) <= 0.05:
        status = "OK"
    elif diferenca > 0:
        status = "Divergente"
    else:
        status = "Sem Retenção"

    return retidos, status, 1


# ==========================================
# PARSER DE XML
# ==========================================
def _extrair_texto_tag(root, tags):
    """Busca o valor da primeira tag encontrada na lista de tags."""
    for tag in tags:
        elem = root.find(f".//{tag}")
        if elem is not None and elem.text:
            return elem.text.strip()
    return ""


def processar_conteudo_xml(conteudo_bytes):
    """Lê os bytes de um arquivo XML e extrai os campos principais."""
    try:
        root = ET.fromstring(conteudo_bytes)
    except Exception:
        return None

    # Tenta identificar o número da nota e valor
    numero = _extrair_texto_tag(root, ["Numero", "nNF", "nNfse", "InfNfse/Numero"])
    if not numero:
        return None

    fornecedor = _extrair_texto_tag(root, ["xNome", "RazaoSocial", "PrestadorServico/RazaoSocial"])
    cnpj = _extrair_texto_tag(root, ["CNPJ", "Cnpj", "Cpf"])
    v_serv = _extrair_texto_tag(root, ["vServ", "vNF", "ValorServicos"])

    try:
        v_serv_num = float(v_serv.replace(",", ".")) if v_serv else 0.0
    except ValueError:
        v_serv_num = 0.0

    return {
        "tipo_xml": "NFSE/NFE",
        "Número da NFS-e": numero,
        "CNPJ Prestador": cnpj,
        "Nome da Empresa": fornecedor,
        "Valor do Serviço": v_serv_num,
        "Valor Líquido": v_serv_num,
        "Status Validação": "OK"
    }


# ==========================================
# LEITURA DE ARQUIVOS (XML, PDF, ZIP)
# ==========================================
def extrair_xml(lista_arquivos):
    """
    Processa a lista de arquivos enviados pelo Streamlit (XML, PDF ou ZIP).
    Retorna (registros, ignoradas).
    """
    registros = []
    ignoradas = []

    for arq in lista_arquivos:
        nome = arq.name.lower()

        # Se for ZIP, extrai e lê os arquivos contidos nele
        if nome.endswith(".zip"):
            with ZipFile(io.BytesIO(arq.read())) as z:
                for filename in z.namelist():
                    if filename.lower().endswith(".xml"):
                        conteudo = z.read(filename)
                        res = processar_conteudo_xml(conteudo)
                        if res:
                            registros.append(res)
                        else:
                            ignoradas.append({"Arquivo": filename, "Motivo": "XML inválido ou não reconhecido"})
                    elif filename.lower().endswith(".pdf"):
                        ignoradas.append({"Arquivo": filename, "Motivo": "PDF dentro de ZIP registrado"})

        # Se for XML direto
        elif nome.endswith(".xml"):
            res = processar_conteudo_xml(arq.read())
            if res:
                registros.append(res)
            else:
                ignoradas.append({"Arquivo": arq.name, "Motivo": "XML não reconhecido"})

        # Se for PDF direto
        elif nome.endswith(".pdf"):
            ignoradas.append({"Arquivo": arq.name, "Motivo": "PDF recebido para arquivo"})

    return registros, ignoradas


# ==========================================
# INTERFACE DA PÁGINA SIEG XML (STREAMLIT)
# ==========================================
def pagina_sieg_xml():
    st.title("📄 SIEG XML / PDF PARA Importação")
    st.write("Envia teus ficheiros XML, PDF ou ZIP recebidos do SIEG para extração e processamento.")

    arquivos = st.file_uploader(
        "Selecione os ficheiros",
        type=["xml", "pdf", "zip"],
        accept_multiple_files=True
    )

    if arquivos:
        if st.button("🚀 Processar Ficheiros SIEG", type="primary"):
            with st.spinner("Processando ficheiros..."):
                registros, ignoradas = extrair_xml(arquivos)

                st.success("Processamento concluído com sucesso!")

                c1, c2 = st.columns(2)
                c1.metric("Notas Processadas", len(registros))
                c2.metric("Ignoradas / Outros", len(ignoradas))

                if registros:
                    st.markdown("### 📝 Notas Extraídas")
                    df_reg = pd.DataFrame(registros)
                    st.dataframe(df_reg, use_container_width=True)

                    csv = df_reg.to_csv(index=False).encode("utf-8-sig")
                    st.download_button(
                        "📥 Baixar Resultado em CSV",
                        data=csv,
                        file_name="sieg_xml_processados.csv",
                        mime="text/csv"
                    )

                if ignoradas:
                    st.markdown("### ⚠️ Ficheiros Ignorados / Avisos")
                    st.dataframe(pd.DataFrame(ignoradas), use_container_width=True)
