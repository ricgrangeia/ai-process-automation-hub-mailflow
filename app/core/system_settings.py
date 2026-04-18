"""
Lightweight key-value store backed by the system_settings table.
Used for runtime-editable config (e.g. folder structure template).
All DB access is synchronous so it can be called from executor threads
and from Streamlit.
"""

from __future__ import annotations

import json
import logging
from sqlalchemy import create_engine, text

logger = logging.getLogger("core.system_settings")

# ── Known keys and their defaults ─────────────────────────────────────────────

FOLDER_STRUCTURE_KEY = "folder_structure"
FOLDER_STRUCTURE_DEFAULT = "{company}/{year}/{month}-{month_name}/{category}/{supplier}"

MONTH_LOCALE_KEY = "month_locale"
MONTH_LOCALE_DEFAULT = "en"

FILE_NAME_KEY = "file_name_template"
FILE_NAME_DEFAULT = "{original}"

INBOX_KEYWORDS_KEY = "inbox_filter_keywords"

# Plain-string keywords that humans can read and edit.
# Regex-based amount patterns live in detector.py and are never exposed here.
DEFAULT_PLAIN_KEYWORDS: list[str] = [
    # English
    "invoice", "receipt", "payment", "paid", "billing", "statement",
    "transaction", "transfer", "wire transfer", "bank transfer",
    "order confirmation", "purchase",
    # Portuguese
    "fatura", "recibo", "pagamento", "pago", "transferência", "mbway",
    "multibanco", "referência de pagamento", "comprovativo",
    "débito", "crédito", "extrato", "liquidação",
]

DEFAULTS: dict[str, str] = {
    FOLDER_STRUCTURE_KEY: FOLDER_STRUCTURE_DEFAULT,
    MONTH_LOCALE_KEY:     MONTH_LOCALE_DEFAULT,
    FILE_NAME_KEY:        FILE_NAME_DEFAULT,
}

# Available tokens for file name template (superset of folder tokens)
FILE_NAME_TOKENS = [
    # ── Original name ──
    ("{original}",       "Original attachment filename without extension (e.g. FT2025-0001)"),
    # ── Invoice-specific ──
    ("{document_type}",  "AT document type code (e.g. FT, FR, RG)"),
    ("{invoice_number}", "Invoice number from the document"),
    ("{seller_nif}",     "Seller NIF"),
    ("{atcud}",          "ATCUD code"),
    ("{total}",          "Total invoice amount (e.g. 123.45)"),
    # ── Shared with folder structure ──
    ("{company}",        "Company name resolved from NIF (e.g. Acme Lda)"),
    ("{category}",       "Export path / document category (e.g. Faturas)"),
    ("{supplier}",       "Seller / supplier name (safe for filenames)"),
    ("{seller}",         "Same as {supplier}"),
    ("{year}",           "4-digit year"),
    ("{month}",          "2-digit month"),
    ("{month_name}",     "Month name in the configured language (e.g. April / Abril)"),
    ("{day}",            "2-digit day"),
    ("{date}",           "Invoice date as YYYY-MM-DD"),
]

# Supported month-name locales: code → (label, [Jan..Dec])
MONTH_LOCALES: dict[str, tuple[str, list[str]]] = {
    "en": ("English",    ["January","February","March","April","May","June",
                          "July","August","September","October","November","December"]),
    "pt": ("Português",  ["Janeiro","Fevereiro","Março","Abril","Maio","Junho",
                          "Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"]),
    "es": ("Español",    ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
                          "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]),
    "fr": ("Français",   ["Janvier","Février","Mars","Avril","Mai","Juin",
                          "Juillet","Août","Septembre","Octobre","Novembre","Décembre"]),
    "de": ("Deutsch",    ["Januar","Februar","März","April","Mai","Juni",
                          "Juli","August","September","Oktober","November","Dezember"]),
    "it": ("Italiano",   ["Gennaio","Febbraio","Marzo","Aprile","Maggio","Giugno",
                          "Luglio","Agosto","Settembre","Ottobre","Novembre","Dicembre"]),
    "nl": ("Nederlands", ["Januari","Februari","Maart","April","Mei","Juni",
                          "Juli","Augustus","September","Oktober","November","December"]),
}

# Available tokens for folder structure (used in UI hints)
FOLDER_TOKENS = [
    ("{company}",    "Company name resolved from NIF (e.g. Acme Lda)"),
    ("{year}",       "4-digit year of the email (e.g. 2025)"),
    ("{month}",      "2-digit month (e.g. 04)"),
    ("{month_name}", "Month name in the configured language (e.g. April / Abril / Avril)"),
    ("{category}",   "Export path from the rule (e.g. Faturas, Water)"),
    ("{supplier}",   "Sender company / person name"),
]


def get_setting(engine_or_url, key: str) -> str:
    """Return the stored value for key, or the built-in default."""
    default = DEFAULTS.get(key, "")
    try:
        eng = _ensure_engine(engine_or_url)
        with eng.connect() as conn:
            row = conn.execute(
                text("SELECT value FROM system_settings WHERE key = :k"),
                {"k": key},
            ).mappings().first()
        if row and row["value"] is not None:
            return row["value"]
    except Exception as e:
        logger.warning(f"system_settings read failed for '{key}': {e}")
    return default


def set_setting(engine_or_url, key: str, value: str) -> None:
    """Upsert a key-value pair."""
    try:
        eng = _ensure_engine(engine_or_url)
        with eng.connect() as conn:
            conn.execute(
                text("""
                    INSERT INTO system_settings (key, value, updated_at)
                    VALUES (:k, :v, NOW())
                    ON CONFLICT (key) DO UPDATE
                        SET value = EXCLUDED.value,
                            updated_at = NOW()
                """),
                {"k": key, "v": value},
            )
            conn.commit()
    except Exception as e:
        logger.error(f"system_settings write failed for '{key}': {e}")
        raise


def get_inbox_keywords(engine_or_url) -> list[str]:
    """Return the active inbox filter keyword list.

    Falls back to DEFAULT_PLAIN_KEYWORDS if the setting has never been saved.
    """
    raw = get_setting(engine_or_url, INBOX_KEYWORDS_KEY)
    if not raw:
        return list(DEFAULT_PLAIN_KEYWORDS)
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(k) for k in parsed if k]
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"inbox_filter_keywords parse error: {e} — using defaults")
    return list(DEFAULT_PLAIN_KEYWORDS)


def set_inbox_keywords(engine_or_url, keywords: list[str]) -> None:
    """Persist the inbox filter keyword list."""
    cleaned = [k.strip().lower() for k in keywords if k and k.strip()]
    set_setting(engine_or_url, INBOX_KEYWORDS_KEY, json.dumps(cleaned, ensure_ascii=False))


def _ensure_engine(engine_or_url):
    """Accept either a SQLAlchemy engine or a connection URL string."""
    if isinstance(engine_or_url, str):
        url = engine_or_url.replace("+asyncpg", "")
        return create_engine(url)
    return engine_or_url
