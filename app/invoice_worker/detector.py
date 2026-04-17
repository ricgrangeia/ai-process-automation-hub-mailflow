"""
Financial email detector — cheap keyword-based classifier.

Two-stage approach:
  1. Check for PDF attachments → always process (PDF invoice path)
  2. Check for marketing/newsletter signals → always skip (leave untouched)
  3. Scan email body for payment/receipt keywords → if matched, trigger LLM extraction
  4. Anything else → leave untouched (unread, unmoved)

This runs before any LLM call so non-financial emails have zero LLM cost.

Keyword filtering is split into two layers:
  - _AMOUNT_RE  : regex patterns for monetary amounts — hardcoded, always active
  - _KEYWORD_RE : compiled from the plain-text keyword list — configurable at runtime
                  via system_settings.INBOX_KEYWORDS_KEY (editable in dashboard)
                  Falls back to DEFAULT_PLAIN_KEYWORDS when no DB setting exists.
"""

import re

# ── Amount patterns — hardcoded, never user-editable ──────────────────────────
# These catch price patterns (e.g. "12,50 EUR", "total: 99") regardless of
# whatever keyword list is active.
_AMOUNT_RE = re.compile(
    r"\d+[.,]\d{2}\s*(eur|usd|gbp|€|\$|£)"
    r"|total\s*:?\s*\d"
    r"|amount\s*:?\s*\d"
    r"|valor\s*:?\s*\d",
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

# Bounce / delivery-failure signals — check sender address and subject
_BOUNCE_FROM_RE = re.compile(
    r"^(MAILER-DAEMON|postmaster|Mail\s+Delivery\s+(Sub)?System"
    r"|Delivery\s+Status\s+Notification"
    r"|Auto\s+Submitted|Mail\s+Administrator)@",
    re.IGNORECASE,
)

_BOUNCE_SUBJECT_RE = re.compile(
    r"undelivered|undeliverable"
    r"|delivery\s+(failure|status|notification|report)"
    r"|returned\s+to\s+sender"
    r"|mail\s+delivery(\s+failed)?"
    r"|bounce"
    r"|non.?delivery"
    r"|failure\s+notice"
    r"|could\s+not\s+be\s+delivered",
    re.IGNORECASE | re.UNICODE,
)

# ── Plain-text keywords — configurable via dashboard ──────────────────────────
# Imported here so detector.py can build the default regex without a DB call,
# and so the dashboard can reference the same canonical list.
from app.core.system_settings import DEFAULT_PLAIN_KEYWORDS


def build_keyword_re(keywords: list[str]) -> re.Pattern:
    """Compile a case-insensitive OR-regex from a list of plain-text keywords.

    Each keyword is re.escape'd so no special regex characters leak through.
    Returns a never-matching pattern when the list is empty.
    """
    escaped = [re.escape(kw.strip()) for kw in keywords if kw and kw.strip()]
    if not escaped:
        return re.compile(r"(?!)")   # never matches
    return re.compile("|".join(escaped), re.IGNORECASE | re.UNICODE)


# Default compiled regex — used when the IMAP worker has no DB-loaded override
_KEYWORD_RE: re.Pattern = build_keyword_re(DEFAULT_PLAIN_KEYWORDS)


# ── Public helpers ─────────────────────────────────────────────────────────────

def has_pdf_attachments(parsed_email: dict) -> bool:
    """Return True if the email has at least one PDF attachment."""
    attachments = parsed_email.get("attachments") or []
    return any(
        (att.get("filename") or "").lower().endswith(".pdf")
        or (att.get("mime_type") or "").lower() == "application/pdf"
        for att in attachments
    )


def is_bounce_email(parsed_email: dict) -> bool:
    """Return True if the email is a delivery failure / bounce notification.

    Checked before the PDF attachment test so that a bounce carrying the
    original PDF is never mistaken for a real invoice.
    """
    from_addr = parsed_email.get("from_address") or ""
    subject   = parsed_email.get("subject") or ""

    if _BOUNCE_FROM_RE.search(from_addr):
        return True
    if _BOUNCE_SUBJECT_RE.search(subject):
        return True

    # Postfix / Sendmail delivery-status headers sometimes appear as body text
    headers: dict = parsed_email.get("headers") or {}
    if headers.get("x-postfix-queue-id") or headers.get("X-Postfix-Queue-ID"):
        return True
    # RFC 3834 auto-submitted header used by MDNs and DSNs
    auto_submitted = headers.get("auto-submitted") or headers.get("Auto-Submitted") or ""
    if auto_submitted.lower().startswith("auto-"):
        return True

    return False


def is_marketing_email(parsed_email: dict) -> bool:
    """
    Return True if the email looks like a newsletter or marketing message.
    Checks both the List-Unsubscribe header and body text.
    """
    headers: dict = parsed_email.get("headers") or {}
    if headers.get("list-unsubscribe") or headers.get("List-Unsubscribe"):
        return True

    text = " ".join(filter(None, [
        parsed_email.get("body_text") or "",
        parsed_email.get("body_html") or "",
    ]))
    return bool(_MARKETING_RE.search(text))


def has_financial_keywords(
    parsed_email: dict,
    keyword_re: re.Pattern | None = None,
) -> bool:
    """Return True if the email body/subject contains payment/invoice signals.

    Checks two layers:
      1. _AMOUNT_RE  — hardcoded monetary-amount patterns (always active)
      2. keyword_re  — plain-text keyword list; uses module default if not given
    """
    text = " ".join(filter(None, [
        parsed_email.get("subject") or "",
        parsed_email.get("body_text") or "",
    ]))
    active_kw_re = keyword_re if keyword_re is not None else _KEYWORD_RE
    return bool(_AMOUNT_RE.search(text)) or bool(active_kw_re.search(text))


def classify_email(
    parsed_email: dict,
    keyword_re: re.Pattern | None = None,
) -> str | None:
    """
    Classify an email for the invoice-worker.

    Args:
        parsed_email: dict from app.ingestion.parser.parse_email
        keyword_re:   compiled regex built from the active keyword list;
                      pass None to use the module-level default.

    Returns:
      "pdf_invoice"   — has PDF attachment(s) → extract via tool server
      "financial_body"— no PDF but body has payment keywords → LLM body extraction
      None            — not a financial email → leave untouched
    """
    # Bounces and delivery-failure notifications must be rejected first —
    # before the PDF check — because they often carry the original email
    # (and its PDF) as an attachment.
    if is_bounce_email(parsed_email):
        return None
    if is_marketing_email(parsed_email):
        return None
    if has_pdf_attachments(parsed_email):
        return "pdf_invoice"
    if has_financial_keywords(parsed_email, keyword_re):
        return "financial_body"
    return None
