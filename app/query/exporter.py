"""
QueryExporter — builds and sends a summary email with matching emails attached.

Each matched email is attached as its original .eml file (if available on disk).
PDF attachments from the email's attachment folder are also included.
A plain-text summary table is included in the email body.
"""

import logging
import smtplib
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from datetime import datetime, timezone

logger = logging.getLogger("query.exporter")


def _build_summary(emails: list, filters: dict) -> str:
    lines = ["Email Query Results", "=" * 40, ""]

    filter_parts = []
    if filters.get("sender_domain"):
        filter_parts.append(f"From domain: {filters['sender_domain']}")
    if filters.get("folder"):
        filter_parts.append(f"Folder: {filters['folder']}")
    if filters.get("date_from"):
        filter_parts.append(f"From: {filters['date_from']}")
    if filters.get("date_to"):
        filter_parts.append(f"To: {filters['date_to']}")
    if filters.get("keyword"):
        filter_parts.append(f"Keyword: {filters['keyword']}")

    lines.append("Filters: " + " | ".join(filter_parts) if filter_parts else "No filters")
    lines.append(f"Found: {len(emails)} email(s)")
    lines.append("")
    lines.append("-" * 40)

    for i, email in enumerate(emails, 1):
        received = email.received_at.strftime("%Y-%m-%d %H:%M") if email.received_at else "unknown"
        lines.append(f"{i}. {email.subject or '(no subject)'}")
        lines.append(f"   From: {email.from_address or 'unknown'}")
        lines.append(f"   Date: {received}")
        lines.append(f"   Category: {email.classification_label or 'unknown'}")
        lines.append("")

    return "\n".join(lines)


def send_results_email(
    settings,
    emails: list,
    filters: dict,
    query_text: str,
) -> bool:
    """
    Sends an email to the report recipient with:
    - Summary table in body
    - Original .eml files attached
    - PDF attachments from disk included
    """
    if not settings.smtp_host or not settings.report_recipient:
        logger.warning("SMTP not configured — cannot send query results.")
        return False

    msg = MIMEMultipart()
    msg["From"] = settings.smtp_user
    msg["To"] = settings.report_recipient
    msg["Subject"] = f"MailFlow Query: {query_text[:60]}"

    summary = _build_summary(emails, filters)
    msg.attach(MIMEText(summary, "plain", "utf-8"))

    attached_count = 0

    for email in emails:
        # Attach original .eml if stored on disk
        if email.raw_path and Path(email.raw_path).exists():
            try:
                with open(email.raw_path, "rb") as f:
                    part = MIMEBase("message", "rfc822")
                    part.set_payload(f.read())
                    encoders.encode_base64(part)
                    safe_subject = (email.subject or f"email_{email.id}")[:60]
                    safe_subject = "".join(c if c.isalnum() or c in " _-" else "_" for c in safe_subject)
                    part.add_header("Content-Disposition", "attachment", filename=f"{safe_subject}.eml")
                    msg.attach(part)
                    attached_count += 1
            except Exception as e:
                logger.warning(f"Could not attach .eml for email {email.id}: {e}")

        # Attach PDF files from attachment folder
        if email.raw_path:
            att_dir = Path(email.raw_path).parent / "attachments"
            if att_dir.exists():
                for pdf_file in att_dir.glob("*.pdf"):
                    try:
                        with open(pdf_file, "rb") as f:
                            part = MIMEBase("application", "pdf")
                            part.set_payload(f.read())
                            encoders.encode_base64(part)
                            part.add_header("Content-Disposition", "attachment", filename=pdf_file.name)
                            msg.attach(part)
                            attached_count += 1
                    except Exception as e:
                        logger.warning(f"Could not attach PDF {pdf_file.name}: {e}")

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(settings.smtp_user, settings.report_recipient, msg.as_string())

        logger.info(
            f"Sent query results to {settings.report_recipient} — "
            f"{len(emails)} emails, {attached_count} attachments"
        )
        return True

    except Exception as e:
        logger.error(f"Failed to send query results email: {e}")
        return False
