"""
Lightweight key-value store backed by the system_settings table.
Used for runtime-editable config (e.g. folder structure template).
All DB access is synchronous so it can be called from executor threads
and from Streamlit.
"""

from __future__ import annotations

import logging
from sqlalchemy import create_engine, text

logger = logging.getLogger("core.system_settings")

# ── Known keys and their defaults ─────────────────────────────────────────────

FOLDER_STRUCTURE_KEY = "folder_structure"
FOLDER_STRUCTURE_DEFAULT = "{company}/{year}/{month}-{month_name}/{category}/{supplier}"

DEFAULTS: dict[str, str] = {
    FOLDER_STRUCTURE_KEY: FOLDER_STRUCTURE_DEFAULT,
}

# Available tokens for folder structure (used in UI hints)
FOLDER_TOKENS = [
    ("{company}",    "Company name resolved from NIF (e.g. Acme Lda)"),
    ("{year}",       "4-digit year of the email (e.g. 2025)"),
    ("{month}",      "2-digit month (e.g. 04)"),
    ("{month_name}", "Month name (e.g. April)"),
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


def _ensure_engine(engine_or_url):
    """Accept either a SQLAlchemy engine or a connection URL string."""
    if isinstance(engine_or_url, str):
        url = engine_or_url.replace("+asyncpg", "")
        return create_engine(url)
    return engine_or_url
