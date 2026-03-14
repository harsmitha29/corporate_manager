"""
app.py  — Entry point only.
All business logic lives in:
config.py          — Config class
extensions.py      — DB pool, logger, CSRF, rate limiter
schema/models.py   — Status constants, time helpers
schema/migrations.py — CREATE TABLE SQL + run_migrations()
services/utils.py     — Validators, api_ok/err, decorators, audit_log
services/holiday.py   — Holiday engine, is_working_day etc.
services/attendance.py— Check-in logic, fill_missing, nightly job
services/leave.py     — Leave balance, monthly summary
services/appraisal.py — Cycle management, score calculation
api/auth.py           — /, /login, /logout, /health
api/admin.py          — Admin panel, create/edit/delete employee
api/attendance.py     — Dashboard, check-in, checkout, WFH, overview
api/leaves.py         — Leave application & admin approval
api/regularization.py — Attendance correction requests
api/self_assessment.py— Employee self-rating & admin review
api/departments.py    — Department CRUD
api/holidays.py       — Company holiday calendar
api/profile.py        — Change password, profile update
api/reports.py        — CSV export, live status, pending counts
"""
import os
from datetime import timedelta

from flask import Flask, jsonify, redirect, url_for, flash
from apscheduler.schedulers.background import BackgroundScheduler

from config import CFG
from extensions import logger, csrf, limiter
from flask_wtf.csrf import CSRFError
from schema.migrations import run_migrations
from services.holiday import load_company_holidays_into_cache
from services.attendance import (
    fill_missing_records_for_all_users,
    mark_absent_and_holidays_for_all_users,
    auto_close_pending_checkouts_for_all_users,
)
from api import register_blueprints


BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
FACE_DATASET_DIR = os.path.join(BASE_DIR, "face_utils", "face_dataset", "images")


def create_app(config_object=None):
    """Application factory — required for pytest fixtures and clean imports."""
    app = Flask(__name__)

    # ── Core config ──────────────────────────────────────────────────
    app.secret_key = CFG.SECRET_KEY
    app.permanent_session_lifetime = timedelta(hours=CFG.SESSION_HOURS)
    app.config.update(
        SESSION_COOKIE_HTTPONLY = True,
        SESSION_COOKIE_SAMESITE = "Lax",
        SESSION_COOKIE_SECURE   = os.environ.get("HTTPS", "false").lower() == "true",
        WTF_CSRF_TIME_LIMIT     = 3600,   # CSRF token valid for 1 hour
    )

    # Allow test overrides (e.g. create_app("testing"))
    if config_object == "testing":
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False

    # ── Initialise extensions ────────────────────────────────────────
    csrf.init_app(app)
    limiter.init_app(app)

    # ── Security headers ─────────────────────────────────────────────
    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"]         = "SAMEORIGIN"
        response.headers["Referrer-Policy"]          = "strict-origin-when-cross-origin"
        return response

    # ── Error handlers ───────────────────────────────────────────────
    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"ok": False, "message": "Not found"}), 404

    @app.errorhandler(500)
    def server_error(e):
        return jsonify({"ok": False, "message": "Server error"}), 500

    @app.errorhandler(429)
    def too_many_requests(e):
        return jsonify({"ok": False, "message": "Too many requests — please wait and try again."}), 429

    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        flash("Session expired. Please log in again.", "warning")
        return redirect(url_for("auth.login")), 400

    # ── Register all blueprints ──────────────────────────────────────
    register_blueprints(app)

    return app


# ── Startup & scheduler ──────────────────────────────────────────────
if __name__ == "__main__":
    app = create_app()

    run_migrations()
    logger.info("Database migrations complete.")

    load_company_holidays_into_cache()
    logger.info("Holiday cache loaded from DB.")

    fill_missing_records_for_all_users()
    logger.info("Initial fill completed for all employees.")

    # Check face dataset directory at startup
    if not os.path.isdir(FACE_DATASET_DIR):
        logger.warning(
            "Face dataset directory not found at %s. "
            "Face recognition will not work until images are enrolled.",
            FACE_DATASET_DIR,
        )

    scheduler = BackgroundScheduler(daemon=True, timezone=str(CFG.TIMEZONE))
    scheduler.add_job(
        mark_absent_and_holidays_for_all_users,
        "cron", hour=CFG.ABSENT_CUTOFF_HOUR, minute=CFG.ABSENT_CUTOFF_MINUTE,
        id="nightly_job", replace_existing=True,
    )
    scheduler.add_job(
        auto_close_pending_checkouts_for_all_users,
        "cron",
        # Task 2.7: fix minute-wrap bug — decrement hour when minute wraps below 0
        hour=CFG.ABSENT_CUTOFF_HOUR if (CFG.ABSENT_CUTOFF_MINUTE - 1) >= 0 else (CFG.ABSENT_CUTOFF_HOUR - 1) % 24,
        minute=(CFG.ABSENT_CUTOFF_MINUTE - 1) % 60,
        id="nightly_autoclose", replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started. Timezone: %s", CFG.TIMEZONE)

    app.run(
        host  = "0.0.0.0",
        port  = int(os.environ.get("PORT", 5000)),
        debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true",
        use_reloader = False,
    )