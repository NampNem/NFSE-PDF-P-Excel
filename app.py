"""
Leitor de Excel de NFS-e (relação baixada do Portal Nacional, "Recebidas").

Devolve os dados NO MESMO FORMATO do extrair_xml() do sieg_xml.py, para o
resto do sistema (validação, Alterdata, Domínio, ZIP de PDFs) funcionar igual.

A validação de retenções usa a MESMA função validar_retencoes() do sieg_xml.py
(ela é recebida como parâmetro, então qualquer mudança futura nela vale aqui).

Atenção: este Excel NÃO tem a coluna "Valor Líquido". Por isso o líquido é
calculado como: Valor do Serviço - Desconto - retenções informadas na planilha.
"""
import numbers
import re
from datetime import datetime

import pandas as pd

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
    # Trata decimais e notação científica trazidos pelo pandas (ex: 9647.0 ou 1e+05)
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


def extrair_nfse_excel(origem, validar_retencoes, formatar_valor):
    """
    origem: caminho do arquivo .xlsx ou um objeto BytesIO.
    Retorna (registros, ignoradas):
      registros -> lista de dicts no formato do extrair_xml (notas normais)
      ignoradas -> notas canceladas/substituídas (não entram nos lançamentos)
    """
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

    for _, row in df.iterrows():

        def g(coluna):
            return row.get(coluna)

        numero = _numero_nota(g("Número NFS-e"))
        if not numero:
            continue  # linha em branco ou linha de TOTAL no fim da planilha

        nome_empresa = _texto(g("Nome Prestador"))
        v_serv = _num(g("Valor do Serviço (R$)"))

        situacao = _texto(g("Situação"))
        if situacao and situacao.lower() != "normal":
            ignoradas.append(
                {
                    "Número da NFS-e": numero,
                    "Fornecedor": nome_empresa,
                    "Valor do Serviço": formatar_valor(v_serv),
                    "Situação": situacao,
                }
            )
            continue

        # ---- valores (mesmos campos que o extrair_xml lê do XML) ----
        v_desc = _num(g("Desconto Incond. (R$)"))
        v_base = round(v_serv - v_desc, 2)

        v_pis = _num(g("PIS - Débito (R$)"))  # vPis
        v_cofins = _num(g("COFINS - Débito (R$)"))  # vCofins
        
        # A coluna da planilha representa o total das contribuições sociais retidas (PCC / CSRF).
        # A CSLL é obtida subtraindo PIS e COFINS do total de contribuições retidas.
        v_contrib_sociais_ret = _num(g("Contrib. Sociais Ret. (R$)"))
        v_csll = round(max(0.0, v_contrib_sociais_ret - v_pis - v_cofins), 2)

        v_irrf = _num(g("IRRF (R$)"))  # vRetIRRF
        v_inss = _num(g("Contrib. Previd. Ret. (R$)"))  # vRetCP
        v_iss = _num(g("Valor do ISSQN (R$)"))  # vISSQN

        # "2 - Retido pelo Tomador" / "3 - Retido pelo Intermediário" => ISS retido
        iss_retido_planilha = _texto(g("Retenção ISSQN"))[:1] in ("2", "3")

        # A planilha não tem "Valor Líquido": calcula usando o total de contribuições retidas + demais retenções.
        retencao_informada = (
            v_contrib_sociais_ret
            + v_irrf
            + v_inss
            + (v_iss if iss_retido_planilha else 0.0)
        )
        v_liq = round(v_base - retencao_informada, 2)

        # Mesma ordem e mesma função de validação do XML
        impostos = {
            "IRRF": v_irrf,
            "PIS": v_pis,
            "COFINS": v_cofins,
            "CSLL": v_csll,
            "INSS": v_inss,
            "ISS": v_iss,
        }
        retidos, status_validacao, qtd_comb = validar_retencoes(v_base, v_liq, impostos)

        def val_ret(nome):
            return impostos[nome] if retidos[nome] else 0.0

        def flag(nome):
            return "Com Retenção" if retidos[nome] else "Sem Retenção"

        lista_ret = [n for n in impostos if retidos[n]]
        texto_retencoes = "Retenção " + "/".join(lista_ret) if lista_ret else "Sem Retenção"
        diferenca = round(v_base - v_liq, 2)

        # ---- código e tipo do serviço: "110401 - Armazenamento, depósito..." ----
        cod_texto = _texto(g("Cód. Tributação Nacional"))
        m = re.match(r"^(\d+)\s*-?\s*(.*)$", cod_texto, re.S)
        if m:
            codigo_tributacao, tipo_servico = m.group(1), m.group(2).strip()
        else:
            codigo_tributacao, tipo_servico = cod_texto, ""
        if not tipo_servico:
            tipo_servico = _texto(g("Descrição do Serviço"))

        # Formatação do CNPJ/CPF garantindo zeros à esquerda
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
                "Diferença Bruto-Líquido": formatar_valor(diferenca),
                "Retenções Identificadas": texto_retencoes,
                "Valor Total Retenções": formatar_valor(diferenca),
                "Status Validação": status_validacao,
                "Combinações Encontradas": qtd_comb,
            }
        )

    return registros, ignoradas
