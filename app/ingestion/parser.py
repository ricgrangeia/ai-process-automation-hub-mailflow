import email
import hashlib
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from bs4 import BeautifulSoup


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value

def _get_text_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text("\n", strip=True)

def parse_email(raw_bytes: bytes) -> dict:
    msg: Message = email.message_from_bytes(raw_bytes)

    from_name, from_address = parseaddr(msg.get("From", ""))
    subject = _decode_header(msg.get("Subject", ""))
    message_id = msg.get("Message-Id")
    date_hdr = msg.get("Date")
    received_at = None
    try:
        if date_hdr:
            received_at = parsedate_to_datetime(date_hdr)
    except Exception:
        received_at = None

    body_text = None
    body_html = None
    attachments = []

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = (part.get("Content-Disposition") or "").lower()
            if ctype == "text/plain" and "attachment" not in disp:
                payload = part.get_payload(decode=True) or b""
                body_text = payload.decode(errors="ignore")
            elif ctype == "text/html" and "attachment" not in disp:
                payload = part.get_payload(decode=True) or b""
                body_html = payload.decode(errors="ignore")
            elif "attachment" in disp or part.get_filename():
                filename = part.get_filename()
                payload = part.get_payload(decode=True) or b""
                sha256 = hashlib.sha256(payload).hexdigest()
                attachments.append({
                    "filename": filename,
                    "mime_type": ctype,
                    "content": payload,
                    "sha256": sha256
                })
    else:
        ctype = msg.get_content_type()
        payload = msg.get_payload(decode=True) or b""
        if ctype == "text/html":
            body_html = payload.decode(errors="ignore")
        else:
            body_text = payload.decode(errors="ignore")

    if body_text is None and body_html:
        body_text = _get_text_from_html(body_html)

    return {
        "from_name": from_name,
        "from_address": from_address,
        "subject": subject,
        "message_id": message_id,
        "received_at": received_at,
        "body_text": body_text,
        "body_html": body_html,
        "attachments": attachments,
    }
