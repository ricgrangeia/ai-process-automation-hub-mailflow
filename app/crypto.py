import base64
import hashlib
from cryptography.fernet import Fernet

def _fernet_from_master(master_key: str) -> Fernet:
    # master_key deve ser estável e secreta; derivamos 32 bytes
    digest = hashlib.sha256(master_key.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)

def encrypt_secret(master_key: str, plain: str) -> str:
    f = _fernet_from_master(master_key)
    return f.encrypt(plain.encode("utf-8")).decode("utf-8")

def decrypt_secret(master_key: str, token: str) -> str:
    f = _fernet_from_master(master_key)
    return f.decrypt(token.encode("utf-8")).decode("utf-8")