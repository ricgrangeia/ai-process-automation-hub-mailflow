"""
i18n — minimal internationalisation helper.

Usage:
    from app.core.i18n import t

    prompt = t("prompt.pdf.payment.system")
    msg    = t("telegram.buttons.approve", folder=folder)

File layout (locales/ at project root):

    locales/
      en/
        prompt.pdf.payment.system.txt   ← LLM prompts (one file each, prefix "prompt.")
        prompt.pdf.invoice.merge.txt
        ...
        ui.toml                         ← all UI strings (buttons, messages, labels)
      pt/
        prompt.pdf.payment.system.txt
        ...
        ui.toml

Rules:
  - Keys starting with "prompt." → read from locales/{lang}/{key}.txt
  - All other keys               → read from locales/{lang}/ui.toml

Language is selected via the LANGUAGE env var (default: "en").
Falls back to "en" if the key/file is missing in the selected language.

Adding a new language:
  1. Copy locales/en/ → locales/{lang}/
  2. Translate the files
  3. Set LANGUAGE={lang} in .env / docker-compose
"""

from __future__ import annotations

import logging
import os
import sys
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger("i18n")

_LOCALES_DIR = Path(__file__).parent.parent.parent / "locales"

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # pip install tomli for Python < 3.11
        except ImportError:
            tomllib = None  # type: ignore


@lru_cache(maxsize=16)
def _load_toml(lang: str) -> dict:
    path = _LOCALES_DIR / lang / "ui.toml"
    if not path.exists():
        if lang != "en":
            path = _LOCALES_DIR / "en" / "ui.toml"
        if not path.exists():
            return {}
    if tomllib is None:
        logger.error("tomllib not available — install tomli for Python < 3.11")
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def _load_txt(lang: str, key: str) -> str | None:
    """Read a prompt .txt file. Returns None if not found."""
    path = _LOCALES_DIR / lang / f"{key}.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def _toml_get(data: dict, key: str):
    """Navigate a.b.c style key into nested dict."""
    parts = key.split(".")
    node = data
    for p in parts:
        if not isinstance(node, dict) or p not in node:
            return None
        node = node[p]
    return node


def t(key: str, **kwargs) -> str:
    """
    Return the translated string for key in the active language.
    Falls back to 'en' if the key/file is missing.
    Keyword arguments are substituted via {name} replacement.
    """
    lang = os.environ.get("LANGUAGE", "en").lower().strip()

    # ── Prompt files ──────────────────────────────────────────────────────────
    if key.startswith("prompt."):
        text = _load_txt(lang, key) or _load_txt("en", key)
        if text is None:
            logger.warning(f"Missing prompt file for key: '{key}'")
            return key
        return _substitute(text, kwargs)

    # ── UI strings (TOML) ─────────────────────────────────────────────────────
    text = _toml_get(_load_toml(lang), key) or _toml_get(_load_toml("en"), key)
    if text is None:
        logger.warning(f"Missing i18n key: '{key}'")
        return key
    return _substitute(str(text), kwargs)


def _substitute(text: str, kwargs: dict) -> str:
    """Replace {name} tokens with provided values. Safe with JSON-like content."""
    for k, v in kwargs.items():
        text = text.replace(f"{{{k}}}", str(v))
    return text
