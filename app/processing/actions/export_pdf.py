"""
ExportPdfAction

Exports the email body as a PDF and copies any attached PDFs to a structured
path on disk. Path supports these template variables resolved from the email:

  {year}   — received_at year  (e.g. 2026)
  {month}  — received_at month (e.g. 04)
  {day}    — received_at day   (e.g. 05)

Example config:
  {"type": "export_pdf", "path": "Company/{year}/{month}/Payments/"}

Dependencies: weasyprint (email body → PDF)
"""

import logging
import re
import shutil
from pathlib import Path
from datetime import datetime, timezone

from app.processing.actions.base import EmailAction

logger = logging.getLogger("action.export_pdf")


def _resolve_path(template: str, received_at: datetime | None, storage_root: str) -> Path:
    now = received_at or datetime.now(timezone.utc)
    resolved = template.format(
        year=now.strftime("%Y"),
        month=now.strftime("%m"),
        day=now.strftime("%d"),
    )
    return Path(storage_root) / resolved


def _safe_filename(value: str, fallback: str) -> str:
    value = (value or fallback).strip()
    value = re.sub(r'[\\/*?:"<>|]', "_", value)
    return value[:120]


class ExportPdfAction(EmailAction):
    """Exports email body to PDF and copies PDF attachments to a structured folder.
    If TOOL_SERVER_URL is configured and the email is classified as Invoices,
    also extracts QR invoice data and persists it to the invoices table.
    """

    def __init__(self, config: dict):
        self.path_template = config["path"]
        self.session_factory = config.get("session_factory")

    async def execute(self, email, account, settings) -> bool:
        try:
            import weasyprint
        except ImportError:
            logger.error(
                "weasyprint is not installed — cannot export PDF. "
                "Add 'weasyprint' to requirements.txt."
            )
            return False

        dest = _resolve_path(self.path_template, email.received_at, settings.storage_root)
        dest.mkdir(parents=True, exist_ok=True)

        safe_subject = _safe_filename(email.subject, f"email_{email.id}")

        # 1. Export email body as PDF
        try:
            html_content = email.body_html or f"<pre>{email.body_text or '(no content)'}</pre>"
            pdf_path = dest / f"{safe_subject}.pdf"
            weasyprint.HTML(string=html_content).write_pdf(str(pdf_path))
            logger.info(f"Exported email {email.id} body → {pdf_path}")
        except Exception as e:
            logger.error(f"Failed to export email {email.id} body to PDF: {e}")

        # 2. Copy existing PDF attachments from storage
        copied_pdfs: list[Path] = []
        if email.raw_path:
            att_src = Path(email.raw_path).parent / "attachments"
            if att_src.exists():
                att_dest = dest / "attachments"
                att_dest.mkdir(parents=True, exist_ok=True)
                for att_file in att_src.glob("*.pdf"):
                    target = att_dest / att_file.name
                    try:
                        shutil.copy2(att_file, target)
                        logger.info(f"Copied attachment {att_file.name} → {target}")
                        copied_pdfs.append(att_file)
                    except Exception as e:
                        logger.error(f"Failed to copy attachment {att_file.name}: {e}")

        # 3. QR invoice extraction — only if Tool Server is configured
        if settings.tool_server_url and self.session_factory:
            label = getattr(email, "classification_label", "") or ""
            if "invoice" in label.lower() or "invoic" in label.lower() or "fatura" in label.lower():
                from app.invoices.extractor import extract_qr_from_pdf, persist_invoice
                # Try attached PDFs first, then the exported body PDF
                pdf_candidates = copied_pdfs or ([pdf_path] if pdf_path.exists() else [])
                for pdf in pdf_candidates:
                    try:
                        results = await extract_qr_from_pdf(
                            str(pdf),
                            settings.tool_server_url,
                            settings.tool_server_api_key,
                        )
                        for invoice_data in results:
                            await persist_invoice(self.session_factory, email.id, invoice_data)
                        if results:
                            break  # first PDF with QR data is enough
                    except Exception as e:
                        logger.warning(f"Invoice QR extraction failed for {pdf}: {e}")

        return True
