"""
Sends NeedsReview notifications to Telegram with inline classification buttons.
Called by processing/worker.py — fire and forget, no bot polling needed here.
"""

import logging
import httpx

logger = logging.getLogger("telegram-notifications")

_DEFAULT_FOLDERS = ["Invoices", "Work", "Personal", "Marketing", "Spam", "Other"]


async def send_worker_started(bot_token: str, chat_id: str) -> None:
    """Sends a startup notification when the AI worker comes online."""
    payload = {
        "chat_id": chat_id,
        "text": "🚀 AI Worker is active — listening for jobs.",
    }
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json=payload)
            r.raise_for_status()
    except Exception as e:
        logger.warning(f"Could not send startup notification: {e}")


async def send_review_request(
    bot_token: str,
    chat_id: str,
    email_id: int,
    subject: str,
    sender: str,
    confidence: float,
    source: str = "llm",
    rule_folder: str | None = None,
    llm_folder: str | None = None,
    folders: list[str] | None = None,
    suggested_folder: str | None = None,
) -> bool:
    """
    Sends an inline-button message asking the user to classify the email.

    Three variants:
    - suggested_folder set: LLM proposed a new folder name — shows suggestion card.
    - source="rule_conflict": rule and LLM disagree — shows both suggestions.
    - anything else: standard low-confidence NeedsReview.

    Callback data format: classify:{email_id}:{folder}
    Returns True if the message was sent successfully.
    """
    subject_display = (subject or "(no subject)")[:80]
    confidence_pct = int(confidence * 100)
    active_folders = folders if folders else _DEFAULT_FOLDERS

    new_folder_row = [
        {"text": "➕ New folder", "callback_data": f"folder_new_request:{email_id}"}
    ]

    if suggested_folder:
        text = (
            f"🆕 AI suggests a new folder\n\n"
            f"From: {sender}\n"
            f"Subject: {subject_display}\n\n"
            f"🧠 AI classified as: {suggested_folder} ({confidence_pct}%)\n"
            f"This folder does not exist yet.\n\n"
            f"Create it and move there, or pick an existing folder:"
        )
        suggest_row = [
            {"text": f"➕ Add '{suggested_folder}' & move",
             "callback_data": f"folder_suggest_add:{email_id}:{suggested_folder}"}
        ]
        existing_buttons = [
            {"text": folder, "callback_data": f"classify:{email_id}:{folder}"}
            for folder in active_folders
        ]
        keyboard = [suggest_row] + [existing_buttons[i:i+2] for i in range(0, len(existing_buttons), 2)] + [new_folder_row]

    elif source == "rule_conflict" and rule_folder and llm_folder:
        text = (
            f"⚠️ Rule Conflict — human input needed\n\n"
            f"From: {sender}\n"
            f"Subject: {subject_display}\n\n"
            f"📚 Learned rule says: {rule_folder}\n"
            f"🧠 AI says: {llm_folder} ({confidence_pct}%)\n\n"
            f"Which is correct?"
        )
        # Conflicting choices shown prominently as full-width rows at the top
        conflict_rows = [
            [{"text": f"✅ {rule_folder}  (rule)", "callback_data": f"classify:{email_id}:{rule_folder}"}],
            [{"text": f"✅ {llm_folder}  (AI · {confidence_pct}%)", "callback_data": f"classify:{email_id}:{llm_folder}"}],
        ]
        # Remaining folders in a 2-column grid, excluding the two already shown
        other_folders = [f for f in active_folders if f not in (rule_folder, llm_folder)]
        other_buttons = [
            {"text": f, "callback_data": f"classify:{email_id}:{f}"}
            for f in other_folders
        ]
        other_rows = [other_buttons[i:i+2] for i in range(0, len(other_buttons), 2)]
        keyboard = conflict_rows + other_rows + [new_folder_row]

    else:
        text = (
            f"🤔 NeedsReview — AI confidence: {confidence_pct}%\n\n"
            f"From: {sender}\n"
            f"Subject: {subject_display}\n\n"
            f"Please classify this email:"
        )
        buttons = [
            {"text": folder, "callback_data": f"classify:{email_id}:{folder}"}
            for folder in active_folders
        ]
        keyboard = [buttons[i:i+2] for i in range(0, len(buttons), 2)] + [new_folder_row]

    payload = {
        "chat_id": chat_id,
        "text": text,
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
