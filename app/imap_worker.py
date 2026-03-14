"""
IMAP Worker Utilities

Responsible for:
- Connecting to IMAP server
- Fetching unseen messages
- Marking messages as seen
- Moving messages between folders
- Creating folders if missing

⚠ All functions here are blocking.
They should be executed using asyncio.to_thread()
from async workers.
"""

import imaplib
import logging
from typing import Iterable, Tuple


logger = logging.getLogger("imap-worker")


# ------------------------------------------------------------------------------
# Connection Helper
# ------------------------------------------------------------------------------

def _connect(host: str, port: int, username: str, password: str):
    """
    Establish secure IMAP SSL connection.
    """
    logger.info(f"Connecting to IMAP server {host}:{port}")
    conn = imaplib.IMAP4_SSL(host, port)
    conn.login(username, password)
    return conn


# ------------------------------------------------------------------------------
# Fetch Unseen Emails
# ------------------------------------------------------------------------------

def fetch_unseen_raw_messages(
    host: str,
    port: int,
    username: str,
    password: str,
    folder: str,
    max_n: int
) -> Iterable[Tuple[str, bytes, str]]:
    """
    Fetch unseen emails.

    Yields:
        (uid, raw_bytes, uid_for_seen)

    - uid: IMAP UID (string)
    - raw_bytes: full RFC822 email content
    - uid_for_seen: used for marking message as seen
    """

    conn = _connect(host, port, username, password)

    try:
        conn.select(folder)

        # Search UNSEEN messages
        status, data = conn.uid("search", None, "UNSEEN")

        if status != "OK":
            logger.warning("Failed to search UNSEEN messages.")
            return

        uids = (data[0] or b"").split()
        uids = uids[:max_n]

        for uid_b in uids:
            uid = uid_b.decode("utf-8", errors="ignore")

            status, msg_data = conn.uid("fetch", uid, "(RFC822)")

            if status != "OK" or not msg_data or not msg_data[0]:
                logger.warning(f"Failed to fetch UID {uid}")
                continue

            raw = msg_data[0][1]

            yield uid, raw, uid

    finally:
        try:
            conn.logout()
        except Exception:
            pass


# ------------------------------------------------------------------------------
# Mark Message as Seen
# ------------------------------------------------------------------------------

def mark_seen(
    host: str,
    port: int,
    username: str,
    password: str,
    folder: str,
    uid: str
) -> None:
    """
    Mark a specific message as Seen using UID.
    """

    conn = _connect(host, port, username, password)

    try:
        conn.select(folder)
        conn.uid("store", uid, "+FLAGS", r"(\Seen)")
    finally:
        try:
            conn.logout()
        except Exception:
            pass


# ------------------------------------------------------------------------------
# Move Message Between Folders
# ------------------------------------------------------------------------------

def move_message(
    host: str,
    port: int,
    username: str,
    password: str,
    source_folder: str,
    target_folder: str,
    uid: str
):
    """
    Move message safely using UID.

    Steps:
    1. Ensure target folder exists
    2. COPY message
    3. Mark original as Deleted
    4. Expunge
    """

    imap = _connect(host, port, username, password)

    try:
        # Ensure target folder exists
        ensure_folder_exists(imap, target_folder)

        # Select source folder
        status, _ = imap.select(source_folder)
        if status != "OK":
            raise Exception(f"Failed to select folder {source_folder}")

        # Copy message
        result = imap.uid("COPY", uid, target_folder)

        if result[0] != "OK":
            raise Exception(
                f"Failed to copy UID {uid} to {target_folder}. Result: {result}"
            )

        # Mark original as deleted
        imap.uid("STORE", uid, "+FLAGS", r"(\Deleted)")
        imap.expunge()

        logger.info(f"Moved UID {uid} → {target_folder}")

    finally:
        try:
            imap.logout()
        except Exception:
            pass


# ------------------------------------------------------------------------------
# Ensure Folder Exists
# ------------------------------------------------------------------------------

def ensure_folder_exists(imap, folder_name: str):
    """
    Ensure folder exists in IMAP.

    If not found, create it.

    Works with Gmail labels as well.
    """

    status, folders = imap.list()

    if status != "OK":
        raise Exception("Failed to list IMAP folders")

    folder_exists = False

    for f in folders:
        decoded = f.decode(errors="ignore")

        # Gmail and others return different formats
        if f'"{folder_name}"' in decoded or decoded.endswith(f" {folder_name}"):
            folder_exists = True
            break

    if not folder_exists:
        logger.info(f"Creating folder: {folder_name}")
        create_status, _ = imap.create(folder_name)

        if create_status != "OK":
            raise Exception(f"Failed to create folder {folder_name}")