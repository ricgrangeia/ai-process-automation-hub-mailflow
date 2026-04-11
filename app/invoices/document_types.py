"""Portuguese AT document type codes and their official descriptions."""

DOCUMENT_TYPES: dict[str, str] = {
    "FT": "Fatura",
    "FR": "Fatura-Recibo",
    "FS": "Fatura Simplificada",
    "ND": "Nota de Débito",
    "NC": "Nota de Crédito",
    "GR": "Guia de Remessa",
    "GT": "Guia de Transporte",
    "GD": "Guia ou Nota de Devolução",
    "RG": "Recibo",
    "RC": "Recibo IVA de Caixa",
    "CM": "Consulta de Mesa",
    "PF": "Fatura Pró-Forma",
    "OR": "Orçamento",
    "NE": "Nota de Encomenda",
}

# Document types that carry payment data (Multibanco / IBAN / MBWay)
PAYMENT_DOC_TYPES: frozenset[str] = frozenset({"FT", "FS"})
