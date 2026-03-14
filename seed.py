#!/usr/bin/env python3
"""
seed.py — One-time CLI script to create the first admin account.

Usage:
    python seed.py

Replaces the removed /register route for initial admin setup.
"""
import getpass
import sys
from werkzeug.security import generate_password_hash

# Load .env before importing config/extensions
from dotenv import load_dotenv
load_dotenv()

from extensions import get_db
from services.utils import is_valid_email, validate_password


def main():
    print("=== Corporate Manager — Admin Seed Script ===\n")

    first = input("First name: ").strip()
    last  = input("Last name : ").strip()
    email = input("Email     : ").strip().lower()

    if not is_valid_email(email):
        print("ERROR: Invalid email address.")
        sys.exit(1)

    password = getpass.getpass("Password  : ")
    confirm  = getpass.getpass("Confirm   : ")

    if password != confirm:
        print("ERROR: Passwords do not match.")
        sys.exit(1)

    errs = validate_password(password)
    if errs:
        print("ERROR: Password must contain: " + ", ".join(errs))
        sys.exit(1)

    hashed = generate_password_hash(password)

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT user_id FROM tbl_users WHERE LOWER(email)=%s", (email,))
        if cur.fetchone():
            print(f"ERROR: An account with email '{email}' already exists.")
            sys.exit(1)

        cur.execute("SELECT 1 FROM tbl_users WHERE LOWER(role_type)='admin' LIMIT 1")
        if cur.fetchone():
            print("ERROR: An admin account already exists. Use the admin panel to manage users.")
            sys.exit(1)

        cur.execute(
            "INSERT INTO tbl_users "
            "(first_name, last_name, email, password, role_type, created_at, joining_date) "
            "VALUES (%s, %s, %s, %s, 'admin', NOW(), CURDATE())",
            (first, last, email, hashed)
        )
        conn.commit()
        print(f"\n✓ Admin account created for {first} {last} ({email})")
        print("  You can now log in at /login")
    except Exception as exc:
        conn.rollback()
        print(f"ERROR: {exc}")
        sys.exit(1)
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
