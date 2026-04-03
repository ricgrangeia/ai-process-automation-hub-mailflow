import os
import hashlib
from pathlib import Path


def save_raw_email(storage_root: str, tenant_id: int, email_id: int, raw_bytes: bytes) -> str:
    base_path = Path(storage_root) / str(tenant_id) / str(email_id)
    base_path.mkdir(parents=True, exist_ok=True)

    file_path = base_path / "raw.eml"

    with open(file_path, "wb") as f:
        f.write(raw_bytes)

    return str(file_path)


def save_attachment(storage_root: str,
                    tenant_id: int,
                    email_id: int,
                    filename: str,
                    content: bytes) -> str:

    base_path = Path(storage_root) / str(tenant_id) / str(email_id) / "attachments"
    base_path.mkdir(parents=True, exist_ok=True)

    # sanitize filename
    safe_name = os.path.basename(filename)

    # make unique using hash
    hash_part = hashlib.sha256(content).hexdigest()[:12]
    name, ext = os.path.splitext(safe_name)
    final_name = f"{name}_{hash_part}{ext}"

    file_path = base_path / final_name

    with open(file_path, "wb") as f:
        f.write(content)

    return str(file_path)
