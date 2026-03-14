#!/usr/bin/env python3
"""
migrate_hash_passwords.py
One-time script: hashes all plaintext passwords in tbl_users.

Run ONCE after pulling this branch:
    python migrate_hash_passwords.py

Safe to re-run — already-hashed rows (starting with 'pbkdf2:' or 'scrypt:')
are detected and skipped automatically.
"""
from dotenv import load_dotenv
load_dotenv()

from werkzeug.security import generate_password_hash
from extensions import get_db, logger


def is_already_hashed(pw: str) -> bool:
    """Werkzeug hashed passwords start with a known method prefix."""
    return pw.startswith(("pbkdf2:", "scrypt:", "bcrypt$", "$2b$", "$argon2"))


def main():
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT user_id, password FROM tbl_users")
    rows = cur.fetchall()

    updated = 0
    skipped = 0
    for row in rows:
        if is_already_hashed(row["password"]):
            skipped += 1
            continue
        hashed = generate_password_hash(row["password"])
        cur.execute(
            "UPDATE tbl_users SET password=%s WHERE user_id=%s",
            (hashed, row["user_id"])
        )
        updated += 1

    conn.commit()
    cur.close()
    conn.close()
    print(f"Done. Updated: {updated} row(s). Skipped (already hashed): {skipped} row(s).")


if __name__ == "__main__":
    main()
