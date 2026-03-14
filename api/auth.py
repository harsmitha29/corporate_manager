"""
api/auth.py
Authentication routes: /, /health, /login, /logout
NOTE: /register route has been removed (Task 1.3).
      Use `python seed.py` to create the first admin account.
"""
import mysql.connector
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

from extensions import get_db, logger, limiter
from services.utils import (
    audit_log, is_valid_email,
    login_required,
)
from services.attendance import fill_missing_records_for_user

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/health")
def health_check():
    try:
        conn = get_db()
        cur  = conn.cursor()
        cur.execute("SELECT 1")
        cur.close(); conn.close()
        return jsonify({"status": "ok", "db": "connected"}), 200
    except Exception as exc:
        return jsonify({"status": "error", "db": str(exc)}), 500


@auth_bp.route("/")
def home():
    return redirect(url_for("auth.login"))


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10/minute", methods=["POST"])
def login():
    if request.method == "POST":
        email    = request.form["email"].strip().lower()
        password = request.form["password"].strip()
        ip       = request.remote_addr or "unknown"

        if not is_valid_email(email):
            flash("Please enter a valid email address", "danger")
            return redirect(url_for("auth.login"))

        conn = get_db()
        cur  = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT user_id,first_name,last_name,password,role_type "
            "FROM tbl_users WHERE LOWER(email)=%s", (email,)
        )
        user = cur.fetchone()
        cur.close(); conn.close()

        if not user or not check_password_hash(user["password"], password):
            flash("Invalid credentials", "danger")
            return redirect(url_for("auth.login"))

        session.clear()
        session.permanent = True
        session.update({
            "user_id":    user["user_id"],
            "first_name": user["first_name"],
            "last_name":  user["last_name"],
            "role":       user["role_type"].lower(),
            "email":      email,
        })

        conn2 = get_db()
        audit_log(conn2, user["user_id"], "LOGIN", f"ip={ip}")
        conn2.commit(); conn2.close()

        fill_missing_records_for_user(user["user_id"])

        if session["role"] == "admin":
            return redirect(url_for("admin.admin_panel"))
        return redirect(url_for("attendance.user_dashboard"))

    return render_template("login.html")


@auth_bp.route("/logout", methods=["GET", "POST"])
def logout():
    uid = session.get("user_id")
    session.clear()
    if uid:
        try:
            conn = get_db()
            audit_log(conn, uid, "LOGOUT")
            conn.commit(); conn.close()
        except Exception:
            pass
    return redirect(url_for("auth.login"))


# ── 429 handler specific to the login route ──────────────────────────
from flask import abort
from werkzeug.exceptions import TooManyRequests

@auth_bp.app_errorhandler(429)
def handle_rate_limit(e):
    from flask import request as req, redirect, url_for, flash, jsonify
    if req.is_json or req.path.startswith("/api/"):
        return jsonify({"ok": False, "message": "Too many requests — please wait and try again."}), 429
    flash("Too many login attempts. Please wait a minute and try again.", "warning")
    return redirect(url_for("auth.login")), 429
