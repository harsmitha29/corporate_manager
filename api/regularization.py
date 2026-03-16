"""
api/regularization.py
Attendance correction request routes:
  GET/POST /regularization
  GET      /admin/regularizations
  POST     /admin/regularizations/<id>/action
"""
from math import ceil
from datetime import datetime as _dt

from flask import Blueprint, render_template, request, redirect, url_for, flash, session

from config import CFG
from extensions import get_db, logger
from schema.models import today_local
from services.utils import audit_log, login_required, admin_required
from services.attendance import derive_status, calc_work_hours
from services.holiday import is_non_working_day

regularization_bp = Blueprint("regularization", __name__)


@regularization_bp.route("/regularization", methods=["GET", "POST"])
@login_required
def regularization():
    user_id = session["user_id"]
    today   = today_local()

    if request.method == "POST":
        date_str = request.form.get("reg_date",      "").strip()
        time_in  = request.form.get("requested_in",  "").strip()
        time_out = request.form.get("requested_out", "").strip()
        reason   = request.form.get("reason",        "").strip()

        if not date_str or not reason:
            flash("Date and reason are required", "danger")
            return redirect(url_for("regularization.regularization"))
        if not time_in and not time_out:
            flash("Provide at least one corrected time (check-in or check-out)", "danger")
            return redirect(url_for("regularization.regularization"))

        try:
            reg_date = _dt.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            flash("Invalid date", "danger")
            return redirect(url_for("regularization.regularization"))

        if reg_date >= today:
            flash("Regularization is only for past dates", "danger")
            return redirect(url_for("regularization.regularization"))
        if is_non_working_day(reg_date):
            flash("Cannot regularize a weekend or holiday", "danger")
            return redirect(url_for("regularization.regularization"))

        conn = get_db()
        cur  = conn.cursor(dictionary=True)
        try:
            cur.execute(
                "SELECT id FROM tbl_regularizations "
                "WHERE user_id=%s AND reg_date=%s AND status='Pending'",
                (user_id, reg_date)
            )
            if cur.fetchone():
                flash("A pending regularization already exists for that date", "warning")
                return redirect(url_for("regularization.regularization"))

            cur.execute(
                "INSERT INTO tbl_regularizations "
                "(user_id, reg_date, requested_in, requested_out, reason) "
                "VALUES (%s,%s,%s,%s,%s)",
                (user_id, reg_date,
                 time_in  if time_in  else None,
                 time_out if time_out else None,
                 reason)
            )
            conn.commit()
            audit_log(conn, user_id, "REGULARIZATION_REQUEST", f"date={reg_date}")
            conn.commit()
            flash("Regularization request submitted. Pending admin approval.", "success")
        except Exception:
            conn.rollback()
            logger.exception("Regularization submit failed")
            flash("Failed to submit request", "danger")
        finally:
            cur.close(); conn.close()
        return redirect(url_for("regularization.regularization"))

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT * FROM tbl_regularizations WHERE user_id=%s ORDER BY applied_at DESC LIMIT 30",
        (user_id,)
    )
    my_reqs = cur.fetchall()
    cur.close(); conn.close()
    return render_template("regularization.html", requests=my_reqs, today=today)


@regularization_bp.route("/admin/regularizations")
@admin_required
def admin_regularizations():
    """Task 5.3: paginated list (20/page) with status tabs showing counts."""
    status_filter = request.args.get("status", "Pending")
    page          = max(1, int(request.args.get("page", 1)))
    per_page      = 20
    offset        = (page - 1) * per_page

    conn = get_db()
    cur  = conn.cursor(dictionary=True)

    # counts for tab badges
    cur.execute("SELECT status, COUNT(*) AS cnt FROM tbl_regularizations GROUP BY status")
    counts = {r["status"]: r["cnt"] for r in cur.fetchall()}

    cur.execute(
        "SELECT COUNT(*) AS total FROM tbl_regularizations WHERE status=%s",
        (status_filter,)
    )
    total       = (cur.fetchone() or {}).get("total", 0)
    total_pages = max(1, ceil(total / per_page))

    cur.execute("""
        SELECT r.*, u.first_name, u.last_name, u.email,
               a.check_in AS current_in, a.check_out AS current_out, a.status AS att_status
        FROM tbl_regularizations r
        JOIN tbl_users u ON u.user_id = r.user_id
        LEFT JOIN tbl_attendance a ON a.user_id=r.user_id AND a.attendance_date=r.reg_date
        WHERE r.status=%s
        ORDER BY r.applied_at DESC
        LIMIT %s OFFSET %s
    """, (status_filter, per_page, offset))
    reqs = cur.fetchall()
    cur.close(); conn.close()

    return render_template(
        "admin_regularizations.html",
        requests=reqs, status_filter=status_filter,
        statuses=["Pending", "Approved", "Rejected"],
        counts=counts,
        page=page, per_page=per_page, total_pages=total_pages,
    )


@regularization_bp.route("/admin/regularizations/<int:reg_id>/action", methods=["POST"])
@admin_required
def admin_regularization_action(reg_id):
    action  = request.form.get("action")
    comment = request.form.get("admin_comment", "").strip()

    if action not in ("approve", "reject"):
        flash("Invalid action", "danger")
        return redirect(url_for("regularization.admin_regularizations"))

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM tbl_regularizations WHERE id=%s", (reg_id,))
        reg = cur.fetchone()
        if not reg or reg["status"] != "Pending":
            flash("Request not found or already processed", "danger")
            return redirect(url_for("regularization.admin_regularizations"))

        new_status = "Approved" if action == "approve" else "Rejected"
        cur.execute(
            "UPDATE tbl_regularizations "
            "SET status=%s, admin_comment=%s, reviewed_by=%s, reviewed_at=NOW() WHERE id=%s",
            (new_status, comment, session["user_id"], reg_id)
        )

        if new_status == "Approved":
            uid      = reg["user_id"]
            reg_date = reg["reg_date"]
            if hasattr(reg_date, "date"):
                reg_date = reg_date.date()
            new_in  = reg["requested_in"]
            new_out = reg["requested_out"]

            cur.execute(
                "SELECT id, check_in, check_out FROM tbl_attendance "
                "WHERE user_id=%s AND attendance_date=%s", (uid, reg_date)
            )
            att = cur.fetchone()
            if att:
                update_in  = str(new_in)  if new_in  else (str(att["check_in"])  if att["check_in"]  else None)
                update_out = str(new_out) if new_out else (str(att["check_out"]) if att["check_out"] else None)
                final_status = derive_status(update_in, update_out)
                ot = 0.0
                if update_in and update_out:
                    hours = calc_work_hours(update_in, update_out)
                    ot    = max(round(hours - CFG.MIN_WORK_HOURS, 2), 0.0)
                cur.execute(
                    "UPDATE tbl_attendance SET check_in=%s, check_out=%s, status=%s, "
                    "overtime_hours=%s WHERE id=%s",
                    (update_in or None, update_out or None, final_status, ot, att["id"])
                )
            else:
                ci = str(new_in)  if new_in  else None
                co = str(new_out) if new_out else None
                fs = derive_status(ci, co)
                cur.execute(
                    "INSERT INTO tbl_attendance "
                    "(user_id, attendance_date, check_in, check_out, status) VALUES (%s,%s,%s,%s,%s)",
                    (uid, reg_date, ci, co, fs)
                )

        conn.commit()
        audit_log(conn, session["user_id"], f"REGULARIZATION_{action.upper()}", f"reg_id={reg_id}")
        conn.commit()
        flash(f"Regularization {new_status.lower()} successfully", "success")
    except Exception:
        conn.rollback()
        logger.exception("Regularization action failed")
        flash("Action failed", "danger")
    finally:
        cur.close(); conn.close()
    return redirect(url_for("regularization.admin_regularizations"))