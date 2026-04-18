"""
ExportPdfAction

Copies PDF attachments to a structured archive:

  {files_root}/{CompanyName}/{year}/{MM-MonthName}/{category}/{SupplierName}/

Company resolution (via companies table):
  - nif_buyer in companies  → "purchase" invoice  → saved under buyer company
  - nif_seller in companies → "sale" invoice      → saved under seller company
  - both in companies       → inter-company        → saved under BOTH company folders
  - neither found           → fallback to COMPANY_NAME env var

Category = the "export path" configured per rule (e.g. "Faturas", "Water", "Energy").
Supplier = sender_name from email (LLM-identified company/person name).

Deduplication: files are only copied if no file with the same name already exists
at the destination. Same-content check via MD5 within the same dest folder.
"""

import hashlib
import logging
import os
import re
import shutil
from pathlib import Path
from datetime import datetime, timezone

from app.processing.actions.base import EmailAction
from app.core.system_settings import (
    get_setting,
    FOLDER_STRUCTURE_KEY,
    FOLDER_STRUCTURE_DEFAULT,
    MONTH_LOCALE_KEY,
    MONTH_LOCALE_DEFAULT,
    MONTH_LOCALES,
    FILE_NAME_KEY,
    FILE_NAME_DEFAULT,
)

logger = logging.getLogger("action.export_pdf")


def _get_month_name(month: int, locale: str) -> str:
    """Return the month name for 1-based month index in the given locale code."""
    _, names = MONTH_LOCALES.get(locale) or MONTH_LOCALES.get(MONTH_LOCALE_DEFAULT)
    return names[month - 1]


def _resolve_filename(att_name: str, template: str, tokens: dict) -> str:
    """
    Build a destination filename from a template.

    The template uses the same {token} syntax as the folder structure.
    The file extension is always taken from the original attachment name.
    {original} expands to the original filename stem (no extension).

    Example:
        att_name = "FT2025-0001.pdf"
        template = "{document_type}_{invoice_number}_{seller_nif}"
        → "FT_FT2025-0001_508517592.pdf"
    """
    stem = Path(att_name).stem
    ext  = Path(att_name).suffix or ".pdf"
    resolved = {**tokens, "original": _safe_name(stem, stem)}
    tpl = (template or FILE_NAME_DEFAULT).strip()
    try:
        name = tpl.format_map(resolved)
    except KeyError as e:
        logger.warning(f"Unknown token {e} in file name template — using original name")
        name = stem
    name = _safe_name(name, stem)
    return name + ext


def _safe_name(value: str, fallback: str = "Unknown") -> str:
    value = (value or fallback).strip()
    value = re.sub(r'[\\/*?:"<>|]', "_", value)
    return value[:80] or fallback


def _resolve_dest(
    files_root: str,
    company_name: str,
    category: str,
    supplier_name: str,
    received_at: datetime | None,
    template: str | None = None,
    month_locale: str | None = None,
) -> Path:
    now        = received_at or datetime.now(timezone.utc)
    year       = now.strftime("%Y")
    month      = now.strftime("%m")
    month_name = _get_month_name(now.month, month_locale or MONTH_LOCALE_DEFAULT)
    tokens = {
        "company":    _safe_name(company_name, "Company"),
        "year":       year,
        "month":      month,
        "month_name": month_name,
        "category":   _safe_name(category, "Attachments"),
        "supplier":   _safe_name(supplier_name, "Unknown"),
    }
    tpl = (template or FOLDER_STRUCTURE_DEFAULT).strip("/")
    try:
        rel_path = tpl.format_map(tokens)
    except KeyError as e:
        logger.warning(f"Unknown token {e} in folder template — falling back to default")
        rel_path = FOLDER_STRUCTURE_DEFAULT.format_map(tokens)
    return Path(files_root) / Path(rel_path)


def _file_hash(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _copy_no_duplicate(src: Path, dest_dir: Path, dest_name: str) -> str:
    """Copy src → dest_dir/{dest_name}.
    Skips if same filename or same MD5 content already exists at destination."""
    dest_file = dest_dir / dest_name
    if dest_file.exists():
        return "duplicate_name"
    src_hash = _file_hash(src)
    for existing in dest_dir.iterdir():
        if existing.is_file() and _file_hash(existing) == src_hash:
            return "duplicate_content"
    shutil.copy2(src, dest_file)
    return "copied"


def _fetch_pdf_attachments(email_id: int | None) -> list[tuple[Path, str]]:
    """Return [(disk_path, original_filename)] for PDF attachments of this email."""
    if not email_id:
        return []
    from sqlalchemy import create_engine, text
    db_url = os.environ.get("DATABASE_URL", "").replace("+asyncpg", "")
    if not db_url:
        return []
    try:
        engine = create_engine(db_url)
        with engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT path, filename FROM attachments
                    WHERE email_id = :eid
                      AND (lower(filename) LIKE '%.pdf' OR lower(path) LIKE '%.pdf')
                """),
                {"eid": email_id},
            ).mappings().all()
        engine.dispose()
        result = []
        for r in rows:
            p = Path(r["path"])
            if p.exists():
                # Use the stored original filename; fall back to the disk filename
                fname = (r["filename"] or p.name).strip() or p.name
                result.append((p, fname))
        return result
    except Exception as e:
        logger.warning(f"Attachment fetch failed for email {email_id}: {e}")
        return []


def _lookup_companies(invoice_nif_buyer: str | None, invoice_nif_seller: str | None) -> list[dict]:
    """
    Query the companies table for matches on nif_buyer and/or nif_seller.
    Returns a list of dicts: [{"name": ..., "role": "buyer"|"seller"}]
    Runs synchronously (called from executor thread).
    """
    from sqlalchemy import create_engine, text
    import os

    db_url = os.environ.get("DATABASE_URL", "").replace("+asyncpg", "")
    if not db_url:
        return []

    nifs = {}
    if invoice_nif_buyer:
        nifs[invoice_nif_buyer] = "buyer"
    if invoice_nif_seller and invoice_nif_seller != invoice_nif_buyer:
        nifs[invoice_nif_seller] = "seller"

    if not nifs:
        return []

    try:
        engine = create_engine(db_url)
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT name, nif FROM companies WHERE active = true AND nif = ANY(:nifs)"),
                {"nifs": list(nifs.keys())},
            ).mappings().all()
        engine.dispose()
        return [{"name": r["name"], "role": nifs[r["nif"]]} for r in rows]
    except Exception as e:
        logger.warning(f"Company lookup failed: {e}")
        return []


class ExportPdfAction(EmailAction):
    """
    Copies PDF attachments to the structured files archive.

    Config keys:
      path      — category name (e.g. "Faturas", "Water"). Required.
      body_pdf  — bool, also export email body as PDF. Default false.
    """

    def __init__(self, config: dict):
        self.category = config.get("path", "Attachments")
        self.body_pdf = config.get("body_pdf", False)

    async def execute(self, email, account, settings) -> bool:
        import asyncio

        files_root       = getattr(settings, "files_root", "/files")
        fallback_company = getattr(settings, "company_name", "Company")

        # Fetch folder structure template + month locale from DB
        db_url = os.environ.get("DATABASE_URL", "").replace("+asyncpg", "")
        folder_template = await asyncio.to_thread(
            get_setting, db_url, FOLDER_STRUCTURE_KEY
        ) if db_url else FOLDER_STRUCTURE_DEFAULT
        month_locale = await asyncio.to_thread(
            get_setting, db_url, MONTH_LOCALE_KEY
        ) if db_url else MONTH_LOCALE_DEFAULT
        file_name_template = await asyncio.to_thread(
            get_setting, db_url, FILE_NAME_KEY
        ) if db_url else FILE_NAME_DEFAULT
        supplier     = getattr(email, "sender_name", None) or (
            (email.from_address or "").split("@")[0] if email.from_address else "Unknown"
        )
        received_at  = getattr(email, "received_at", None)

        # ── Resolve company names from invoice QR NIFs ────────────────────────
        nif_buyer  = getattr(email, "invoice_nif_buyer",  None)
        nif_seller = getattr(email, "invoice_nif_seller", None)

        # If invoice data is on the email object, use it; otherwise look up the DB
        # (invoice data lives in the invoices table, not directly on email — so we
        # do a quick DB query here)
        invoice_nif_buyer  = nif_buyer
        invoice_nif_seller = nif_seller

        if not invoice_nif_buyer and not invoice_nif_seller:
            invoice_nif_buyer, invoice_nif_seller = await asyncio.to_thread(
                _fetch_invoice_nifs, getattr(email, "id", None)
            )

        matched_companies = await asyncio.to_thread(
            _lookup_companies, invoice_nif_buyer, invoice_nif_seller
        )

        # Determine destination company folders
        if matched_companies:
            # buyer  → this is a purchase for that company
            # seller → this is a sale from that company
            # both   → inter-company: save in both
            company_names = [c["name"] for c in matched_companies]
            role_log = ", ".join(f"{c['name']} ({c['role']})" for c in matched_companies)
            logger.info(f"Email {email.id} — matched companies: {role_log}")
        else:
            company_names = [fallback_company]
            logger.info(f"Email {email.id} — no company NIF match, using fallback: {fallback_company}")

        # ── Resolve PDF attachments (original filenames from DB) ─────────────
        att_files: list[tuple[Path, str]] = await asyncio.to_thread(
            _fetch_pdf_attachments, getattr(email, "id", None)
        )
        if not att_files and email.raw_path:
            # Fallback: scan disk (filename will be the storage name, not ideal)
            att_src = Path(email.raw_path).parent / "attachments"
            if att_src.exists():
                att_files = [(p, p.name) for p in att_src.glob("*.pdf")]

        # Build filename tokens available on the AI-worker path (no invoice_data)
        now = received_at or datetime.now(timezone.utc)
        _fn_tokens_base: dict = {
            "document_type":  "",
            "invoice_number": "",
            "seller_nif":     invoice_nif_seller or "",
            "seller":         _safe_name(supplier, "Unknown"),
            "atcud":          "",
            "total":          "",
            "date":           now.strftime("%Y-%m-%d"),
            "year":           now.strftime("%Y"),
            "month":          now.strftime("%m"),
            "month_name":     _get_month_name(now.month, month_locale),
            "day":            now.strftime("%d"),
            "category":       _safe_name(self.category, "Attachments"),
            "supplier":       _safe_name(supplier, "Unknown"),
        }

        for company_name in company_names:
            dest = _resolve_dest(files_root, company_name, self.category, supplier, received_at, folder_template, month_locale)
            dest.mkdir(parents=True, exist_ok=True)

            _fn_tokens = {**_fn_tokens_base, "company": _safe_name(company_name, "Company")}

            copied = 0
            for att_path, att_name in att_files:
                dest_name = _resolve_filename(att_name, file_name_template, _fn_tokens)
                result = _copy_no_duplicate(att_path, dest, dest_name)
                if result == "copied":
                    copied += 1
                    logger.info(f"Archived {att_name} → {dest / dest_name}")
                else:
                    logger.info(f"Skipped {att_name} ({result}) in {dest}")

            # Optional: export email body as PDF
            if self.body_pdf:
                try:
                    import weasyprint
                    html_content = email.body_html or f"<pre>{email.body_text or '(no content)'}</pre>"
                    safe_subject = _safe_name(email.subject or f"email_{email.id}")
                    body_path    = dest / f"{safe_subject}.pdf"
                    if not body_path.exists():
                        weasyprint.HTML(string=html_content).write_pdf(str(body_path))
                        logger.info(f"Exported email body → {body_path}")
                except ImportError:
                    logger.warning("weasyprint not installed — body PDF export skipped")
                except Exception as e:
                    logger.error(f"Body PDF export failed for email {email.id}: {e}")

            logger.info(f"ExportPdf complete — {company_name}, copied={copied}, dest={dest}")

        return True


def _fetch_invoice_nifs(email_id: int | None) -> tuple[str | None, str | None]:
    """Fetch nif_buyer and nif_seller from the invoices table for this email."""
    if not email_id:
        return None, None
    from sqlalchemy import create_engine, text
    import os
    db_url = os.environ.get("DATABASE_URL", "").replace("+asyncpg", "")
    if not db_url:
        return None, None
    try:
        engine = create_engine(db_url)
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT nif_buyer, nif_seller FROM invoices WHERE email_id = :eid LIMIT 1"),
                {"eid": email_id},
            ).mappings().first()
        engine.dispose()
        if row:
            return row["nif_buyer"], row["nif_seller"]
    except Exception as e:
        logger.warning(f"Invoice NIF fetch failed: {e}")
    return None, None
