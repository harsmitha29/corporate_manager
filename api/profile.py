"""
api/profile.py
User profile self-service routes:
  GET/POST /profile/change-password
  GET/POST /profile
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, session

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

        if not row or row["password"] != current:
            flash("Current password is incorrect", "danger")
            return redirect(url_for("profile.change_password"))
        errs = validate_password(new_pwd)
        if errs:
            flash(f"New password must contain: {', '.join(errs)}", "danger")
            return redirect(url_for("profile.change_password"))
        if new_pwd != confirm:
            flash("Passwords do not match", "danger")
            return redirect(url_for("profile.change_password"))
        if new_pwd == current:
            flash("New password must differ from current password", "danger")
            return redirect(url_for("profile.change_password"))

        conn = get_db()
        cur  = conn.cursor()
        cur.execute("UPDATE tbl_users SET password=%s WHERE user_id=%s", (new_pwd, user_id))
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
