"""
Re-encrypt email account passwords.

Handles two cases:
  --mode plaintext   : passwords are stored as plain text, encrypt them now
  --mode rekey       : passwords were encrypted with OLD_MASTER_KEY, re-encrypt with MASTER_KEY

Usage (run from project root):
  # plaintext passwords → encrypt with current MASTER_KEY
  python scripts/reencrypt_passwords.py --mode plaintext

  # old key → new key
  OLD_MASTER_KEY=old_value python scripts/reencrypt_passwords.py --mode rekey

Env vars required:
  DATABASE_URL   — postgres connection string
  MASTER_KEY     — the NEW (current) master key
  OLD_MASTER_KEY — (rekey mode only) the previous master key
"""

import argparse
import os
import sys

from sqlalchemy import create_engine, text


def fernet_from_key(key: str):
    import base64
    import hashlib
    from cryptography.fernet import Fernet
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["plaintext", "rekey"], required=True)
    parser.add_argument("--dry-run", action="store_true", help="Print what would change without writing")
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    master_key = os.environ.get("MASTER_KEY")

    if not db_url:
        sys.exit("ERROR: DATABASE_URL not set")
    if not master_key:
        sys.exit("ERROR: MASTER_KEY not set")

    new_fernet = fernet_from_key(master_key)

    if args.mode == "rekey":
        old_key = os.environ.get("OLD_MASTER_KEY")
        if not old_key:
            sys.exit("ERROR: OLD_MASTER_KEY not set (required for --mode rekey)")
        old_fernet = fernet_from_key(old_key)

    engine = create_engine(db_url)

    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT id, username, password_encrypted FROM email_accounts WHERE active = true")
        ).fetchall()

        print(f"Found {len(rows)} active accounts.")

        updated = 0
        for row in rows:
            acc_id, username, stored = row

            try:
                if args.mode == "plaintext":
                    plain = stored  # already plaintext
                else:
                    plain = old_fernet.decrypt(stored.encode("utf-8")).decode("utf-8")

                new_token = new_fernet.encrypt(plain.encode("utf-8")).decode("utf-8")

                if args.dry_run:
                    print(f"  [DRY RUN] id={acc_id} ({username}): would re-encrypt OK")
                else:
                    conn.execute(
                        text("UPDATE email_accounts SET password_encrypted = :pw WHERE id = :id"),
                        {"pw": new_token, "id": acc_id}
                    )
                    print(f"  id={acc_id} ({username}): re-encrypted OK")
                    updated += 1

            except Exception as e:
                print(f"  id={acc_id} ({username}): FAILED — {e}")

        if not args.dry_run:
            print(f"\nDone. {updated}/{len(rows)} accounts updated.")
        else:
            print(f"\nDry run complete. No changes written.")


if __name__ == "__main__":
    main()
