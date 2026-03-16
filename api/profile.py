"""
api/profile.py
User profile self-service routes:
  GET/POST /profile/change-password
  GET/POST /profile
"""
import re
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import get_db
from services.utils import audit_log, login_required, validate_password

profile_bp = Blueprint("profile", __name__)


@profile_bp.route("/profile/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current = request.form.get("current_password",  "").strip()
        new_pwd = request.form.get("new_password",      "").strip()
        confirm = request.form.get("confirm_password",  "").strip()
        user_id = session["user_id"]

        conn = get_db()
        cur  = conn.cursor(dictionary=True)
        cur.execute("SELECT password FROM tbl_users WHERE user_id=%s", (user_id,))
        row = cur.fetchone()
        cur.close(); conn.close()

        # Task 5.5: use check_password_hash — never compare plaintext
        if not row or not check_password_hash(row["password"], current):
            flash("Current password is incorrect", "danger")
            return redirect(url_for("profile.change_password"))
        errs = validate_password(new_pwd)
        if errs:
            flash(f"New password must contain: {', '.join(errs)}", "danger")
            return redirect(url_for("profile.change_password"))
        if new_pwd != confirm:
            flash("Passwords do not match", "danger")
            return redirect(url_for("profile.change_password"))
        if check_password_hash(row["password"], new_pwd):
            flash("New password must differ from current password", "danger")
            return redirect(url_for("profile.change_password"))

        # Task 5.5: hash new password before saving — never store plaintext
        hashed = generate_password_hash(new_pwd)
        conn = get_db()
        cur  = conn.cursor()
        cur.execute("UPDATE tbl_users SET password=%s WHERE user_id=%s", (hashed, user_id))
        conn.commit()
        audit_log(conn, user_id, "PASSWORD_CHANGE", "self-service")
        conn.commit()
        cur.close(); conn.close()
        session.clear()
        flash("Password changed successfully. Please login again.", "success")
        return redirect(url_for("auth.login"))

    return render_template("change_password.html")


@profile_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user_id = session["user_id"]
    conn    = get_db()
    cur     = conn.cursor(dictionary=True)

    if request.method == "POST":
        phone     = request.form.get("phone",             "").strip() or None
        gender    = request.form.get("gender",            "").strip() or None
        emergency = request.form.get("emergency_contact", "").strip() or None

        # Task 5.4: phone format validation — digits only, 10–15 characters
        if phone:
            digits = re.sub(r"[\s\-\+\(\)]", "", phone)
            if not digits.isdigit() or not (10 <= len(digits) <= 15):
                flash("Phone number must be 10–15 digits (spaces, dashes, + allowed)", "danger")
                cur.close(); conn.close()
                return redirect(url_for("profile.profile"))

        cur.execute(
            "UPDATE tbl_users SET phone=%s, gender=%s, emergency_contact=%s WHERE user_id=%s",
            (phone, gender, emergency, user_id)
        )
        conn.commit()
        audit_log(conn, user_id, "PROFILE_UPDATE", "self-service")
        conn.commit()
        cur.close(); conn.close()
        flash("Profile updated successfully", "success")
        return redirect(url_for("profile.profile"))

    cur.execute(
        "SELECT u.first_name, u.last_name, u.email, u.joining_date, "
        "u.designation, u.employee_id, u.phone, u.gender, u.emergency_contact, "
        "d.dept_name "
        "FROM tbl_users u LEFT JOIN tbl_departments d ON d.id=u.dept_id "
        "WHERE u.user_id=%s", (user_id,)
    )
    user = cur.fetchone() or {}
    cur.close(); conn.close()
    return render_template("profile.html", user=user)