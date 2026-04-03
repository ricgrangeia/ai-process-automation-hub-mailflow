import imaplib
import logging
import socket
import time
from typing import Iterable, Tuple

logger = logging.getLogger("imap-worker")


# ------------------------------------------------------------------------------
# Connection
# ------------------------------------------------------------------------------

def connect_imap(host: str, port: int, username: str, password: str):

    host = (host or "").strip()

    if not host:
        raise Exception("IMAP host not configured")

    logger.info(f"[{username}] Connecting to IMAP {host}:{port}")

    for attempt in range(3):
        try:
            socket.gethostbyname(host)

            conn = imaplib.IMAP4_SSL(host, port, timeout=20)
            conn.login(username, password)

            return conn

        except Exception as e:
            logger.warning(f"[{username}] IMAP connection attempt {attempt+1}/3 failed: {e}")
            time.sleep(3)

    raise Exception(f"[{username}] IMAP connection failed")


# ------------------------------------------------------------------------------
# Fetch messages
# ------------------------------------------------------------------------------

def fetch_unseen_raw_messages(
    conn,
    folder: str,
    max_n: int
) -> Iterable[Tuple[str, bytes, str]]:

    conn.select(folder, readonly=True)

    status, data = conn.uid("search", None, "UNSEEN")

    if status != "OK":
        logger.warning("Failed to search UNSEEN messages.")
        return []

    uids = (data[0] or b"").split()[:max_n]

    for uid_b in uids:

        uid = uid_b.decode()

        status, msg_data = conn.uid("fetch", uid, "(RFC822)")

        if status != "OK":
            continue

        raw = msg_data[0][1]

        yield uid, raw, uid


# ------------------------------------------------------------------------------
# Mark seen
# ------------------------------------------------------------------------------

def mark_seen(conn, folder: str, uid: str):

    conn.select(folder)
    conn.uid("store", uid, "+FLAGS", r"(\Seen)")


# ------------------------------------------------------------------------------
# Move message
# ------------------------------------------------------------------------------

def move_message(conn, source_folder: str, target_folder: str, uid: str):

    ensure_folder_exists(conn, target_folder)

    status, _ = conn.select(source_folder)

    if status != "OK":
        raise Exception(f"Failed to select folder {source_folder}")

    result = conn.uid("COPY", uid, target_folder)

    if result[0] != "OK":
        logger.error(f"IMAP COPY failed: {result}")
        raise Exception(f"Failed to copy UID {uid}")

    conn.uid("STORE", uid, "+FLAGS", r"(\Deleted)")
    conn.expunge()

    logger.info(f"Moved UID {uid} → {target_folder}")


# ------------------------------------------------------------------------------
# Ensure folder exists
# ------------------------------------------------------------------------------

def ensure_folder_exists(conn, folder_name: str):

    status, folders = conn.list()

    if status != "OK":
        raise Exception("Failed to list IMAP folders")

    for f in folders:
        if folder_name in f.decode():
            return

    logger.info(f"Creating folder: {folder_name}")
    conn.create(folder_name)