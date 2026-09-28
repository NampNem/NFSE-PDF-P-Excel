def extrair_xml(caminho_ou_conteudo):
    """Identifica se o XML é uma NFS-e ou um Evento de Cancelamento/Substituição."""
    if isinstance(caminho_ou_conteudo, bytes):
        root = ET.fromstring(caminho_ou_conteudo)
    elif isinstance(caminho_ou_conteudo, str) and caminho_ou_conteudo.endswith(".xml"):
        tree = ET.parse(caminho_ou_conteudo)
        root = tree.getroot()
    else:
        root = ET.fromstring(caminho_ou_conteudo)

    def find_tag(element, tag_name):
        if element is None:
            return None
        for child in element.iter():
            if child.tag.endswith(tag_name):
                return child
        return None

    def get_text(element, tag_name, default=""):
        node = find_tag(element, tag_name)
        return node.text.strip() if (node is not None and node.text) else default

    def get_float(element, tag_name, default=0.0):
        val = get_text(element, tag_name)
        try:
            return float(val) if val else default
        except ValueError:
            return default

    # EVENTO DE CANCELAMENTO / SUBSTITUIÇÃO
    if root.tag.endswith("evento") or find_tag(root, "pedRegEvento") is not None:
        ch_nfse_original = get_text(root, "chNFSe")
        ch_substituta = get_text(root, "chSubstituta")
        desc_evento = get_text(root, "xDesc")
        motivo_subst = get_text(root, "xMotivo")
        data_evento = get_text(root, "dhEvento")
        cnpj_autor = get_text(root, "CNPJAutor")

        return {
            "tipo_xml": "EVENTO",
            "Chave NFS-e Original": ch_nfse_original,
            "Chave NFS-e Substituta": ch_substituta,
            "Descrição Evento": desc_evento,
            "Motivo Cancelamento": motivo_subst,
            "Data Evento": data_evento,
            "CNPJ Autor": cnpj_autor,
        }

    # NFS-E NORMAL
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
    tipo_servico = (
        get_text(root, "xTribNac")
        or get_text(root, "xTribMun")
        or get_text(root, "xDescServ")
    )

    v_serv = get_float(root, "vServ")
    v_liq = get_float(root, "vLiq")
    if v_liq == 0.0 and v_serv > 0.0:
        v_liq = v_serv

    v_pis = get_float(root, "vPis")
    v_cofins = get_float(root, "vCofins")
    v_csll = get_float(root, "vCSLL")
    v_irrf = get_float(root, "vRetIRRF")
    v_inss = get_float(root, "vINSS")
    v_iss = get_float(root, "vISSQN")

    tp_ret_iss = get_text(root, "tpRetISSQN")
    diferenca = round(v_serv - v_liq, 2)

    # TRAVA PRINCIPAL: Se a diferença Bruto - Líquido for 0, NÃO há retenção descontada
    if diferenca == 0.0:
        pis_status = "Sem Retenção"
        cofins_status = "Sem Retenção"
        csll_status = "Sem Retenção"
        irrf_status = "Sem Retenção"
        inss_status = "Sem Retenção"
        iss_retido_flag = "Sem Retenção"
        v_iss_retencao = 0.0
        texto_retenções = "Sem Retenção"
    else:
        # Se a diferença for maior que zero, mapeia quais impostos causaram a retenção
        iss_retido_flag = "Com Retenção" if tp_ret_iss == "2" else "Sem Retenção"
        v_iss_retencao = v_iss if iss_retido_flag == "Com Retenção" else 0.0

        pis_status = "Com Retenção" if v_pis > 0 else "Sem Retenção"
        cofins_status = "Com Retenção" if v_cofins > 0 else "Sem Retenção"
        csll_status = "Com Retenção" if v_csll > 0 else "Sem Retenção"
        irrf_status = "Com Retenção" if v_irrf > 0 else "Sem Retenção"
        inss_status = "Com Retenção" if v_inss > 0 else "Sem Retenção"

        impostos_retidos_lista = []
        if v_pis > 0:
            impostos_retidos_lista.append("PIS")
        if v_cofins > 0:
            impostos_retidos_lista.append("COFINS")
        if v_csll > 0:
            impostos_retidos_lista.append("CSLL")
        if v_irrf > 0:
            impostos_retidos_lista.append("IRRF")
        if v_inss > 0:
            impostos_retidos_lista.append("INSS")
        if tp_ret_iss == "2":
            impostos_retidos_lista.append("ISS")

        texto_retenções = (
            "Retenção " + "/".join(impostos_retidos_lista)
            if impostos_retidos_lista
            else "Sem Retenção"
        )

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
        "PIS Retido?": pis_status,
        "Valor COFINS": v_cofins,
        "COFINS Retido?": cofins_status,
        "CSLL (Retida)": v_csll,
        "CSLL Retida?": csll_status,
        "IRRF": v_irrf,
        "IRRF Retido?": irrf_status,
        "INSS (Previdenciária)": v_inss,
        "INSS Retido?": inss_status,
        "ISS": v_iss,
        "ISS Retenção": v_iss_retencao,
        "ISS Retido?": iss_retido_flag,
        "Valor Líquido": v_liq,
        "Diferença Bruto-Líquido": formatar_valor(diferenca),
        "Retenções Identificadas": texto_retenções,
        "Valor Total Retenções": formatar_valor(diferenca),
        "Status Validação": "OK",
        "Combinações Encontradas": 1,
    }
