import asyncio
import logging

from app.processing.actions.base import EmailAction
from app.core.crypto import decrypt_secret
from app.ingestion.imap.client import connect_imap, move_message

logger = logging.getLogger("action.move_folder")


class MoveFolderAction(EmailAction):
    """Moves the email to an IMAP folder."""

    def __init__(self, config: dict):
        self.folder = config["folder"]

    async def execute(self, email, account, settings) -> bool:
        if account.provider != "imap":
            # Outlook — no IMAP move needed, folder is metadata only
            return True

        imap_password = decrypt_secret(settings.master_key, account.password_encrypted)

        def _move():
            conn = connect_imap(
                account.imap_host,
                account.imap_port or 993,
                account.username,
                imap_password,
            )
            try:
                move_message(conn, settings.inbox_folder, self.folder, email.imap_uid)
                return True
            except Exception as e:
                logger.error(f"IMAP move failed for email {email.id}: {e}")
                return False
            finally:
                try:
                    conn.logout()
                except Exception:
                    pass

        return await asyncio.to_thread(_move)
