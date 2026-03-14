"""
services/utils.py
Shared helpers: response builders, validators, audit logging,
and the login_required / admin_required decorators.
"""
import re
from functools import wraps

from flask import session, redirect, url_for, flash, jsonify, request

from extensions import get_db


# ── Response helpers ─────────────────────────────────────────────────
def api_ok(data: dict = None, message: str = "Success", code: int = 200):
    body = {"ok": True, "message": message}
    if data:
        body.update(data)
    return jsonify(body), code


def api_err(message: str, code: int = 400, data: dict = None):
    body = {"ok": False, "message": message}
    if data:
        body.update(data)
    return jsonify(body), code


# ── Validators ───────────────────────────────────────────────────────
EMAIL_REGEX = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_REGEX.match(email))


def is_valid_name(name: str) -> bool:
    return bool(re.match(r"^[A-Za-z\s\-']{2,50}$", name))


def validate_password(pwd: str) -> list[str]:
    errs = []
    if len(pwd) < 8:                         errs.append("at least 8 characters")
    if len(pwd) > 32:                        errs.append("at most 32 characters")
    if not re.search(r"[A-Z]", pwd):         errs.append("one uppercase letter")
    if not re.search(r"[a-z]", pwd):         errs.append("one lowercase letter")
    if not re.search(r"[0-9]", pwd):         errs.append("one number")
    if not re.search(r"[!@#$%^&*()\-_=+\[\]{};:'\",.<>/?`~\\|]", pwd):
        errs.append("one special character")
    if " " in pwd:                           errs.append("no spaces")
    return errs


# ── Audit log ────────────────────────────────────────────────────────
def audit_log(conn, user_id: int, action: str, detail: str = "") -> None:
    """Write one row to tbl_audit_log. Silently swallows errors."""
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO tbl_audit_log (user_id, action, detail, created_at) "
            "VALUES (%s, %s, %s, NOW())",
            (user_id, action[:100], detail[:500])
        )
        cur.close()
    except Exception:
        pass


# ── Auth decorators ──────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            if request.is_json:
                return api_err("Authentication required", 401)
            flash("Please login first", "warning")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if session.get("role") != "admin":
            if request.is_json:
                return api_err("Unauthorized", 403)
            flash("Unauthorized access", "danger")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return wrapper
