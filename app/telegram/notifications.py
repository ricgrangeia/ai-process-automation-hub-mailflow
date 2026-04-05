"""
Sends a NeedsReview notification to Telegram with inline classification buttons.
Called by processing/worker.py — fire and forget, no bot polling needed here.
"""

import logging
import httpx

logger = logging.getLogger("telegram-notifications")

FOLDERS = ["Invoices", "Work", "Personal", "Marketing", "Spam", "Other"]


async def send_worker_started(bot_token: str, chat_id: str) -> None:
    """Sends a startup notification when the AI worker comes online."""
    payload = {
        "chat_id": chat_id,
        "text": "🚀 *AI Worker is active* — listening for jobs.",
        "parse_mode": "Markdown",
    }
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json=payload)
            r.raise_for_status()
    except Exception as e:
        logger.warning(f"Could not send startup notification: {e}")


async def send_review_request(bot_token: str, chat_id: str, email_id: int, subject: str, sender: str, confidence: float) -> bool:
    """
    Sends an inline-button message asking the user to classify the email.
    Callback data format: classify:{email_id}:{folder}
    Returns True if the message was sent successfully.
    """
    subject_display = (subject or "(no subject)")[:80]
    confidence_pct = int(confidence * 100)

    text = (
        f"🤔 *NeedsReview* — AI confidence: {confidence_pct}%\n\n"
        f"*From:* `{sender}`\n"
        f"*Subject:* {subject_display}\n\n"
        f"Please classify this email:"
    )

    buttons = [
        {"text": folder, "callback_data": f"classify:{email_id}:{folder}"}
        for folder in FOLDERS
    ]

    # Two buttons per row
    keyboard = [buttons[i:i+2] for i in range(0, len(buttons), 2)]

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "reply_markup": {"inline_keyboard": keyboard},
    }

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json=payload)
            r.raise_for_status()
            return True
    except Exception as e:
        logger.error(f"Failed to send Telegram notification for email {email_id}: {e}")
        return False
