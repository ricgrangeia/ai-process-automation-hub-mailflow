"""
Financial email detector — cheap keyword-based classifier.

Two-stage approach:
  1. Check for PDF attachments → always process (PDF invoice path)
  2. Check for marketing/newsletter signals → always skip (leave untouched)
  3. Scan email body for payment/receipt keywords → if matched, trigger LLM extraction
  4. Anything else → leave untouched (unread, unmoved)

This runs before any LLM call so non-financial emails have zero LLM cost.
"""

import re

# Keywords that indicate a financial email body
PAYMENT_KEYWORDS: list[str] = [
    # Generic
    "invoice", "receipt", "payment", "paid", "billing", "statement",
    "transaction", "transfer", "wire transfer", "bank transfer",
    "order confirmation", "purchase",
    # Portuguese
    "fatura", "recibo", "pagamento", "pago", "transferência", "mbway",
    "multibanco", "referência de pagamento", "comprovativo",
    "débito", "crédito", "extrato", "liquidação",
    # Common amount patterns
    r"\d+[.,]\d{2}\s*(eur|usd|gbp|€|\$|£)",
    r"total\s*:?\s*\d",
    r"amount\s*:?\s*\d",
    r"valor\s*:?\s*\d",
]

_KEYWORD_RE = re.compile(
    "|".join(PAYMENT_KEYWORDS),
    re.IGNORECASE | re.UNICODE,
)

# Marketing / newsletter signals — any of these → not a financial email
_MARKETING_RE = re.compile(
    r"unsubscribe|cancelar\s+subscrição|cancelar\s+inscrição|abmelden"
    r"|list-unsubscribe"          # email header value leaked into body parsers
    r"|opt.?out"
    r"|you('re| are) receiving this"
    r"|esta mensagem foi enviada para"
    r"|if you no longer wish to receive"
    r"|view\s+(this\s+)?(email|message)\s+in\s+(your\s+)?browser"
    r"|ver\s+no\s+navegador",
    re.IGNORECASE | re.UNICODE,
)


def has_pdf_attachments(parsed_email: dict) -> bool:
    """Return True if the email has at least one PDF attachment."""
    attachments = parsed_email.get("attachments") or []
    return any(
        (att.get("filename") or "").lower().endswith(".pdf")
        or (att.get("mime_type") or "").lower() == "application/pdf"
        for att in attachments
    )


def is_marketing_email(parsed_email: dict) -> bool:
    """
    Return True if the email looks like a newsletter or marketing message.
    Checks both the List-Unsubscribe header and body text.
    """
    # Check headers (imap parser may expose them as a dict)
    headers: dict = parsed_email.get("headers") or {}
    if headers.get("list-unsubscribe") or headers.get("List-Unsubscribe"):
        return True

    text = " ".join(filter(None, [
        parsed_email.get("body_text") or "",
        parsed_email.get("body_html") or "",
    ]))
    return bool(_MARKETING_RE.search(text))


def has_financial_keywords(parsed_email: dict) -> bool:
    """Return True if the email body contains payment/invoice keywords."""
    text = " ".join(filter(None, [
        parsed_email.get("subject") or "",
        parsed_email.get("body_text") or "",
    ]))
    return bool(_KEYWORD_RE.search(text))


def classify_email(parsed_email: dict) -> str | None:
    """
    Classify an email for the invoice-worker.

    Returns:
      "pdf_invoice"   — has PDF attachment(s) → extract via tool server
      "financial_body"— no PDF but body has payment keywords → LLM body extraction
      None            — not a financial email → leave untouched
    """
    if has_pdf_attachments(parsed_email):
        return "pdf_invoice"
    if is_marketing_email(parsed_email):
        return None
    if has_financial_keywords(parsed_email):
        return "financial_body"
    return None
