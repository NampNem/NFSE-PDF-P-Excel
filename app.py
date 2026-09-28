import xml.etree.ElementTree as ET


def formatar_valor(v):
    """Mantém o valor como número (float) com 2 casas.
    Formate como R$ só na hora de exibir no Streamlit."""
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return 0.0


def _local(tag):
    """Remove o namespace: '{http://...}vServ' -> 'vServ'."""
    return tag.split("}")[-1] if isinstance(tag, str) else ""


def extrair_xml(caminho_ou_conteudo):
    """Identifica se o XML é uma NFS-e ou um Evento de Cancelamento/Substituição."""
    if isinstance(caminho_ou_conteudo, bytes):
        root = ET.fromstring(caminho_ou_conteudo)
    elif isinstance(caminho_ou_conteudo, str) and caminho_ou_conteudo.lower().endswith(".xml"):
        root = ET.parse(caminho_ou_conteudo).getroot()
    else:
        root = ET.fromstring(caminho_ou_conteudo)

    def find_tag(element, tag_name):
        if element is None:
            return None
        for child in element.iter():
            if _local(child.tag) == tag_name:
                return child
        return None

    def get_text(element, *tag_names, default=""):
        """Aceita vários nomes de tag; devolve o primeiro que tiver texto."""
        for tag_name in tag_names:
            node = find_tag(element, tag_name)
            if node is not None and node.text and node.text.strip():
                return node.text.strip()
        return default

    def get_float(element, *tag_names, default=0.0):
        val = get_text(element, *tag_names)
        if not val:
            return default
        try:
            return float(val.replace(",", ".")) if "," in val and "." not in val else float(val)
        except ValueError:
            return default

    # ------------------------------------------------------------------
    # EVENTO DE CANCELAMENTO / SUBSTITUIÇÃO
    # ------------------------------------------------------------------
    if _local(root.tag) == "evento" or find_tag(root, "pedRegEvento") is not None:
        return {
            "tipo_xml": "EVENTO",
            "Chave NFS-e Original": get_text(root, "chNFSe"),
            "Chave NFS-e Substituta": get_text(root, "chSubstituta"),
            "Descrição Evento": get_text(root, "xDesc"),
            "Motivo Cancelamento": get_text(root, "xMotivo"),
            "Data Evento": get_text(root, "dhEvento"),
            "CNPJ Autor": get_text(root, "CNPJAutor"),
        }

    # ------------------------------------------------------------------
    # NFS-E NORMAL
    # ------------------------------------------------------------------
    inf_nfse_node = find_tag(root, "infNFSe")
    chave_nfse = inf_nfse_node.attrib.get("Id", "") if inf_nfse_node is not None else ""
    if chave_nfse.startswith("NFS"):
        chave_nfse = chave_nfse[3:]

    numero_nfse = get_text(root, "nNFSe")
    data_competencia = get_text(root, "dCompet")

    emit_node = find_tag(root, "emit")
    nome_empresa = get_text(emit_node, "xNome") if emit_node is not None else ""
    cnpj_prestador = get_text(emit_node, "CNPJ") if emit_node is not None else ""

    codigo_tributacao = get_text(root, "cTribNac")
    tipo_servico = get_text(root, "xTribNac", "xTribMun", "xDescServ")

    v_serv = get_float(root, "vServ")
    v_liq = get_float(root, "vLiq")
    if v_liq == 0.0 and v_serv > 0.0:
        v_liq = v_serv

    v_pis = get_float(root, "vPis")
    v_cofins = get_float(root, "vCofins")
    # Padrão nacional: vRetCSLL / vRetIRRF / vRetCP. Mantém nomes antigos como alternativa.
    v_csll = get_float(root, "vRetCSLL", "vCSLL")
    v_irrf = get_float(root, "vRetIRRF", "vIRRF")
    v_inss = get_float(root, "vRetCP", "vINSS")
    v_iss = get_float(root, "vISSQN")

    tp_ret_iss = get_text(root, "tpRetISSQN")
    tp_ret_pis_cofins = get_text(root, "tpRetPisCofins")  # "1" = retido, "2" = não retido

    diferenca = round(v_serv - v_liq, 2)

    # ISS
    iss_retido = tp_ret_iss == "2"
    v_iss_retencao = v_iss if iss_retido else 0.0

    # PIS/COFINS: usa o indicador oficial quando existe.
    # Se não existir, só considera retido quando há diferença Bruto-Líquido.
    if tp_ret_pis_cofins:
        pis_retido = tp_ret_pis_cofins == "1" and v_pis > 0
        cofins_retido = tp_ret_pis_cofins == "1" and v_cofins > 0
    else:
        pis_retido = diferenca > 0 and v_pis > 0
        cofins_retido = diferenca > 0 and v_cofins > 0

    # CSLL / IRRF / INSS: as tags vRet* já indicam valor retido.
    csll_retido = v_csll > 0
    irrf_retido = v_irrf > 0
    inss_retido = v_inss > 0

    def status(flag):
        return "Com Retenção" if flag else "Sem Retenção"

    lista = []
    if pis_retido:
        lista.append("PIS")
    if cofins_retido:
        lista.append("COFINS")
    if csll_retido:
        lista.append("CSLL")
    if irrf_retido:
        lista.append("IRRF")
    if inss_retido:
        lista.append("INSS")
    if iss_retido:
        lista.append("ISS")

    texto_retencoes = "Retenção " + "/".join(lista) if lista else "Sem Retenção"

    # Aviso caso a soma das retenções não bata com a diferença Bruto-Líquido
    total_retido = (
        (v_pis if pis_retido else 0.0)
        + (v_cofins if cofins_retido else 0.0)
        + v_csll + v_irrf + v_inss + v_iss_retencao
    )
    status_validacao = "OK" if abs(total_retido - diferenca) < 0.02 else "Conferir valores"

    return {
        "tipo_xml": "NFSE",
        "Chave NFS-e": chave_nfse,
        "Número da NFS-e": numero_nfse,
        "Data Competência": data_competencia,
        "CNPJ Prestador": cnpj_prestador,
        "Nome da Empresa": nome_empresa,
        "Código Tributação": codigo_tributacao,
        "Tipo de Serviço": tipo_servico,
        "Valor do Serviço": v_serv,
        "Valor PIS": v_pis,
        "PIS Retido?": status(pis_retido),
        "Valor COFINS": v_cofins,
        "COFINS Retido?": status(cofins_retido),
        "CSLL (Retida)": v_csll,
        "CSLL Retida?": status(csll_retido),
        "IRRF": v_irrf,
        "IRRF Retido?": status(irrf_retido),
        "INSS (Previdenciária)": v_inss,
        "INSS Retido?": status(inss_retido),
        "ISS": v_iss,
        "ISS Retenção": v_iss_retencao,
        "ISS Retido?": status(iss_retido),
        "Valor Líquido": v_liq,
        "Diferença Bruto-Líquido": formatar_valor(diferenca),
        "Retenções Identificadas": texto_retencoes,
        "Valor Total Retenções": formatar_valor(diferenca),
        "Status Validação": status_validacao,
        "Combinações Encontradas": 1,
    }
