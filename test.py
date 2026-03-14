"""
Run this script once from your project root:
    python fix_migrations.py

It removes 'IF NOT EXISTS' from ALTER TABLE ADD COLUMN statements in app.py,
making them compatible with MySQL 5.7 (as well as 8.0+).

The existing try/except in run_migrations() already handles
"Duplicate column name" errors on subsequent runs — so the behaviour
is identical, just without the invalid-syntax warnings.
"""

import re
import shutil
from pathlib import Path

TARGET = Path("app.py")

if not TARGET.exists():
    raise FileNotFoundError("app.py not found. Run this script from your project root.")

# Backup first
backup = TARGET.with_suffix(".py.bak")
shutil.copy(TARGET, backup)
print(f"Backup written to {backup}")

original = TARGET.read_text(encoding="utf-8")

# Replace:  ADD COLUMN IF NOT EXISTS  →  ADD COLUMN
# (case-insensitive, to be safe)
fixed = re.sub(
    r"ADD COLUMN IF NOT EXISTS",
    "ADD COLUMN",
    original,
    flags=re.IGNORECASE,
)

count = original.count("ADD COLUMN IF NOT EXISTS")
if count == 0:
    print("No occurrences found — nothing changed (already fixed?).")
else:
    TARGET.write_text(fixed, encoding="utf-8")
    print(f"Fixed {count} occurrence(s) of 'ADD COLUMN IF NOT EXISTS' in {TARGET}.")
    print("Done. Restart your Flask app.")