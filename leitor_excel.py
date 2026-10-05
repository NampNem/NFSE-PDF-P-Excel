"""
Leitor de Excel de NFS-e (relação baixada do Portal Nacional, "Recebidas").

Devolve os dados no mesmo formato do extrair_xml() do sieg_xml.py, para o
resto do sistema (validação, Alterdata, Domínio, ZIP de PDFs) funcionar igual.
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
    origem: caminho do arquivo .xlsx ou objeto BytesIO.
    Retorna (registros, ignoradas).
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

    for row in df.to_dict("records"):
        numero = _numero_nota(row.get("Número NFS-e"))
        if not numero:
            continue

        nome_empresa = _texto(row.get("Nome Prestador"))
        v_serv = _num(row.get("Valor do Serviço (R$)"))

        situacao = _texto(row.get("Situação"))
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

        v_desc = _num(row.get("Desconto Incond. (R$)"))
        v_base = round(v_serv - v_desc, 2)

        v_pis = _num(row.get("PIS - Débito (R$)"))
        v_cofins = _num(row.get("COFINS - Débito (R$)"))

        v_contrib_sociais_ret = _num(row.get("Contrib. Sociais Ret. (R$)"))
        v_csll = round(max(0.0, v_contrib_sociais_ret - v_pis - v_cofins), 2)

        v_irrf = _num(row.get("IRRF (R$)"))
        v_inss = _num(row.get("Contrib. Previd. Ret. (R$)"))
        v_iss = _num(row.get("Valor do ISSQN (R$)"))

        iss_retido_planilha = _texto(row.get("Retenção ISSQN"))[:1] in ("2", "3")

        retencao_informada = (
            v_contrib_sociais_ret
            + v_irrf
            + v_inss
            + (v_iss if iss_retido_planilha else 0.0)
        )
        v_liq = round(v_base - retencao_informada, 2)

        impostos = {
            "IRRF": v_irrf,
            "PIS": v_pis,
            "COFINS": v_cofins,
            "CSLL": v_csll,
            "INSS": v_inss,
            "ISS": v_iss,
        }
        retidos, status_validacao, qtd_comb = validar_retencoes(
            v_base, v_liq, impostos
        )

        lista_ret = [n for n in impostos if retidos[n]]
        texto_retencoes = (
            "Retenção " + "/".join(lista_ret) if lista_ret else "Sem Retenção"
        )
        diferenca = round(v_base - v_liq, 2)

        cod_texto = _texto(row.get("Cód. Tributação Nacional"))
        m = re.match(r"^(\d+)\s*-?\s*(.*)$", cod_texto, re.S)
        if m:
            codigo_tributacao, tipo_servico = m.group(1), m.group(2).strip()
        else:
            codigo_tributacao, tipo_servico = cod_texto, ""
        if not tipo_servico:
            tipo_servico = _texto(row.get("Descrição do Serviço"))

        cnpj_limpo = re.sub(r"\D", "", _texto(row.get("CNPJ/CPF Prestador")))
        if len(cnpj_limpo) > 11:
            cnpj_formatado = cnpj_limpo.zfill(14)
        elif cnpj_limpo:
            cnpj_formatado = cnpj_limpo.zfill(11)
        else:
            cnpj_formatado = ""

        registros.append(
            {
                "tipo_xml": "NFSE",
                "Chave NFS-e": _texto(row.get("Chave NFS-e")),
                "Número da NFS-e": numero,
                "Data Competência": _data_iso(row.get("Data Geração")),
                "CNPJ Prestador": cnpj_formatado,
                "Nome da Empresa": nome_empresa,
                "Código Tributação": codigo_tributacao,
                "Tipo de Serviço": tipo_servico,
                "Valor do Serviço": v_serv,
                "Valor PIS": impostos["PIS"] if retidos["PIS"] else 0.0,
                "PIS Retido?": "Com Retenção" if retidos["PIS"] else "Sem Retenção",
                "Valor COFINS": impostos["COFINS"] if retidos["COFINS"] else 0.0,
                "COFINS Retido?": "Com Retenção" if retidos["COFINS"] else "Sem Retenção",
                "CSLL (Retida)": impostos["CSLL"] if retidos["CSLL"] else 0.0,
                "CSLL Retida?": "Com Retenção" if retidos["CSLL"] else "Sem Retenção",
                "IRRF": impostos["IRRF"] if retidos["IRRF"] else 0.0,
                "IRRF Retido?": "Com Retenção" if retidos["IRRF"] else "Sem Retenção",
                "INSS (Previdenciária)": impostos["INSS"] if retidos["INSS"] else 0.0,
                "INSS Retido?": "Com Retenção" if retidos["INSS"] else "Sem Retenção",
                "ISS": v_iss,
                "ISS Retenção": impostos["ISS"] if retidos["ISS"] else 0.0,
                "ISS Retido?": "Com Retenção" if retidos["ISS"] else "Sem Retenção",
                "Valor Líquido": v_liq,
                "Diferença Bruto-Líquido": formatar_valor(diferenca),
                "Retenções Identificadas": texto_retencoes,
                "Valor Total Retenções": formatar_valor(diferenca),
                "Status Validação": status_validacao,
                "Combinações Encontradas": qtd_comb,
            }
        )

    return registros, ignoradas
