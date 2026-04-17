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

def list_unseen_uids(conn, folder: str) -> list[str]:
    """Return all UNSEEN UIDs in the folder without fetching message bodies."""
    conn.select(folder, readonly=True)
    status, data = conn.uid("search", None, "UNSEEN")
    if status != "OK":
        logger.warning("Failed to search UNSEEN messages.")
        return []
    raw = (data[0] or b"").split()
    return [uid_b.decode() for uid_b in raw]


def fetch_messages_by_uids(
    conn,
    folder: str,
    uids: list[str],
) -> Iterable[Tuple[str, bytes, str]]:
    """Fetch raw RFC822 bytes for a specific list of UIDs."""
    conn.select(folder, readonly=True)
    for uid in uids:
        status, msg_data = conn.uid("fetch", uid, "(RFC822)")
        if status != "OK":
            continue
        raw = msg_data[0][1]
        yield uid, raw, uid


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

    sep = _get_imap_separator(conn)
    target_imap = _normalize_folder(target_folder, sep)

    ensure_folder_exists(conn, target_folder)

    status, _ = conn.select(source_folder)

    if status != "OK":
        raise Exception(f"Failed to select folder {source_folder}")

    result = conn.uid("COPY", uid, target_imap)

    if result[0] != "OK":
        logger.error(f"IMAP COPY failed: {result}")
        raise Exception(f"Failed to copy UID {uid}")

    conn.uid("STORE", uid, "+FLAGS", r"(\Deleted)")
    conn.expunge()

    logger.info(f"Moved UID {uid} → {target_imap}")


# ------------------------------------------------------------------------------
# Rename folder
# ------------------------------------------------------------------------------

def _get_imap_separator(conn) -> str:
    """Return the folder hierarchy separator for this IMAP server (e.g. '/' or '.')."""
    status, raw = conn.list('""', '""')
    if status == "OK" and raw:
        decoded = raw[0].decode() if isinstance(raw[0], bytes) else str(raw[0])
        # e.g. (\Noselect) "/" ""  or  (\Noselect) "." ""
        import re
        m = re.search(r'\) "(.)" ', decoded)
        if m:
            return m.group(1)
    return "/"


def _list_imap_folder_names(conn) -> list[str]:
    """Return the list of folder names from the IMAP LIST response."""
    status, raw = conn.list()
    if status != "OK":
        return []
    names = []
    for entry in raw:
        if not entry:
            continue
        decoded = entry.decode() if isinstance(entry, bytes) else str(entry)
        # LIST response: (\Flags) "sep" "name"  or  (\Flags) "sep" name
        # Split on the separator token and take everything after
        parts = decoded.split('" ')
        if len(parts) >= 2:
            name = parts[-1].strip().strip('"')
        else:
            # Fallback: last whitespace-delimited token
            name = decoded.rsplit(None, 1)[-1].strip().strip('"')
        names.append(name)
    return names


def _normalize_folder(name: str, separator: str) -> str:
    """Convert a canonical folder name (using '/') to the server's separator."""
    if separator == "/":
        return name
    return name.replace("/", separator)


def rename_imap_folder(conn, old_name: str, new_name: str) -> bool:
    """Rename an IMAP folder. Returns True if successful, False if not found or failed."""
    try:
        sep = _get_imap_separator(conn)
        old_imap = _normalize_folder(old_name, sep)
        new_imap = _normalize_folder(new_name, sep)
        folder_names = _list_imap_folder_names(conn)
        logger.debug(f"IMAP sep='{sep}' folders visible: {folder_names}")
        if old_imap not in folder_names:
            logger.info(f"IMAP folder '{old_imap}' not found — skipping rename.")
            return False

        status, _ = conn.rename(old_imap, new_imap)
        if status == "OK":
            logger.info(f"Renamed IMAP folder: {old_imap} → {new_imap}")
            return True
        else:
            logger.warning(f"IMAP RENAME returned non-OK for '{old_imap}', trying CREATE fallback.")
            if new_imap not in folder_names:
                conn.create(new_imap)
                logger.info(f"Created IMAP folder '{new_imap}' as fallback.")
            return False
    except Exception as e:
        logger.warning(f"Failed to rename IMAP folder {old_name} → {new_name}: {e}")
        return False


# ------------------------------------------------------------------------------
# Ensure folder exists
# ------------------------------------------------------------------------------

def ensure_folder_exists(conn, folder_name: str):
    sep = _get_imap_separator(conn)
    imap_name = _normalize_folder(folder_name, sep)
    folder_names = _list_imap_folder_names(conn)
    if imap_name in folder_names:
        return
    logger.info(f"Creating folder: {imap_name} (sep='{sep}')")
    conn.create(imap_name)
