"""
tests/conftest.py
Pytest configuration and shared fixtures for the corporate_manager test suite.
Uses the real MySQL DB in a test schema, rolling back after each test.
"""
import os
import pytest
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

# Point at test config before importing the app
os.environ.setdefault("SECRET_KEY",   "test-secret")
os.environ.setdefault("DB_NAME",      os.environ.get("DB_NAME", "corporate_manager"))
os.environ.setdefault("WTF_CSRF_ENABLED", "false")
os.environ.setdefault("TESTING",      "true")
os.environ.setdefault("FACE_RECOGNITION_ENABLED", "false")

from app import create_app


# ── App / client fixtures ─────────────────────────────────────────────

@pytest.fixture(scope="session")
def app():
    """Create app with testing config."""
    application = create_app("testing")
    application.config.update({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test-secret",
    })
    return application


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


@pytest.fixture
def runner(app):
    """Flask CLI test runner."""
    return app.test_cli_runner()


# ── DB helper ─────────────────────────────────────────────────────────

@pytest.fixture
def db(app):
    """Yield a real DB connection, rollback after test."""
    from extensions import get_db
    with app.app_context():
        conn = get_db()
        conn.start_transaction()
        try:
            yield conn
        finally:
            conn.rollback()
            conn.close()


# ── Auth helpers ──────────────────────────────────────────────────────

@pytest.fixture
def admin_session(client, app):
    """Log in as admin, return client with session."""
    from extensions import get_db
    from werkzeug.security import generate_password_hash

    with app.app_context():
        conn = get_db()
        cur  = conn.cursor(dictionary=True)
        # Find or use first admin user
        cur.execute(
            "SELECT user_id, email FROM tbl_users "
            "WHERE LOWER(role_type)='admin' AND is_active=1 LIMIT 1"
        )
        admin = cur.fetchone()
        cur.close(); conn.close()

        if not admin:
            pytest.skip("No admin user in DB — skipping admin test")

        # Use the known admin email; password may vary — reset it for test
        conn2 = get_db()
        cur2  = conn2.cursor()
        cur2.execute(
            "UPDATE tbl_users SET password=%s WHERE user_id=%s",
            (generate_password_hash("Admin@1234"), admin["user_id"])
        )
        conn2.commit()
        cur2.close(); conn2.close()

    with client.session_transaction() as sess:
        sess["user_id"]    = admin["user_id"]
        sess["role"]       = "admin"
        sess["first_name"] = "Admin"
        sess["last_name"]  = "Test"
        sess["email"]      = admin["email"]
    return client


@pytest.fixture
def employee_session(client, app):
    """Log in as first active employee, return client with session."""
    from extensions import get_db

    with app.app_context():
        conn = get_db()
        cur  = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT user_id, email, first_name, last_name FROM tbl_users "
            "WHERE LOWER(role_type)='employee' AND is_active=1 LIMIT 1"
        )
        emp = cur.fetchone()
        cur.close(); conn.close()

        if not emp:
            pytest.skip("No employee in DB — skipping employee test")

    with client.session_transaction() as sess:
        sess["user_id"]    = emp["user_id"]
        sess["role"]       = "employee"
        sess["first_name"] = emp["first_name"]
        sess["last_name"]  = emp["last_name"]
        sess["email"]      = emp["email"]
    return client, emp["user_id"]


# ── Sample attendance data ────────────────────────────────────────────

@pytest.fixture
def sample_attendance(app):
    """Insert 5 known attendance rows for employee, yield, then clean up."""
    from extensions import get_db

    with app.app_context():
        conn = get_db()
        cur  = conn.cursor(dictionary=True)

        cur.execute(
            "SELECT user_id FROM tbl_users "
            "WHERE LOWER(role_type)='employee' AND is_active=1 LIMIT 1"
        )
        emp = cur.fetchone()
        if not emp:
            cur.close(); conn.close()
            pytest.skip("No employee in DB")

        uid   = emp["user_id"]
        today = date.today()
        rows  = []

        # Mon-Fri last week (5 working days)
        last_mon = today - timedelta(days=today.weekday() + 7)
        for i in range(5):
            d = last_mon + timedelta(days=i)
            cur.execute(
                "INSERT IGNORE INTO tbl_attendance "
                "(user_id, attendance_date, check_in, check_out, status) "
                "VALUES (%s, %s, '09:00:00', '18:00:00', 'Completed')",
                (uid, d)
            )
            rows.append(d)
        conn.commit()
        cur.close(); conn.close()

        yield {"user_id": uid, "dates": rows}

        # Cleanup
        conn2 = get_db()
        cur2  = conn2.cursor()
        for d in rows:
            cur2.execute(
                "DELETE FROM tbl_attendance WHERE user_id=%s AND attendance_date=%s",
                (uid, d)
            )
        conn2.commit()
        cur2.close(); conn2.close()