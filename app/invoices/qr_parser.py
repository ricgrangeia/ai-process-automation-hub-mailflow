"""
Portuguese invoice QR code parser (AT/ATCUD format).

QR field mapping (key:value pairs separated by '*'):
  A — NIF seller
  B — NIF buyer
  C — Country of buyer
  D — Document type (FT=invoice, FS=simplified invoice, etc.)
  E — Document status (N=normal)
  F — Document date (YYYYMMDD)
  G — Unique document ID (e.g. FT 2026/1)
  H — ATCUD code
  I1 — Tax country code
  I2 — Taxable base (exempt)
  I3 — Taxable base (reduced rate)
  I4 — VAT reduced rate
  I5 — Taxable base (intermediate rate)
  I6 — VAT intermediate rate
  I7 — Taxable base (normal rate)
  I8 — VAT normal rate
  N  — Total VAT
  O  — Total with VAT (gross total)
  P  — Withheld tax amount
  Q  — 4-char hash
  R  — Program certificate number
  S  — Other info
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger("invoice.qr_parser")


def parse_pt_invoice_qr(raw: str) -> dict:
    """
    Parse a Portuguese AT invoice QR code string.
    Returns a dict with normalised keys. Missing fields are None.
    """
    fields = {}
    for part in raw.split("*"):
        if ":" in part:
            key, _, value = part.partition(":")
            fields[key.strip()] = value.strip()

    def _float(key: str) -> float | None:
        v = fields.get(key)
        if v is None:
            return None
        try:
            return float(v.replace(",", "."))
        except ValueError:
            return None

    def _date(key: str) -> datetime | None:
        v = fields.get(key)
        if not v:
            return None
        try:
            return datetime.strptime(v, "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    # Sum up VAT amounts across all rate brackets
    vat_total = _float("N")
    # Sum up taxable base across all brackets
    taxable_sum = sum(
        filter(None, [_float("I2"), _float("I3"), _float("I5"), _float("I7")])
    ) or None

    return {
        "nif_seller":     fields.get("A"),
        "nif_buyer":      fields.get("B"),
        "invoice_number": fields.get("G"),
        "atcud":          fields.get("H"),
        "invoice_date":   _date("F"),
        "taxable_amount": taxable_sum,
        "vat_amount":     vat_total,
        "total_amount":   _float("O"),
        "raw_qr":         raw,
    }
