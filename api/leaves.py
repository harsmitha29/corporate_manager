"""
api/leaves.py
Leave management routes:
  GET/POST /leaves
  POST     /leaves/<id>/cancel
  GET      /admin/leaves
  POST     /admin/leaves/<id>/action
"""
from datetime import timedelta
from datetime import datetime as _dt

from flask import Blueprint, render_template, request, redirect, url_for, flash, session

from extensions import get_db, logger
from schema.models import AttendanceStatus, LeaveType, LeaveStatus, today_local
from services.utils import audit_log, login_required, admin_required
from services.holiday import is_working_day
from services.leave import get_leave_balance

leaves_bp = Blueprint("leaves", __name__)


@leaves_bp.route("/leaves", methods=["GET", "POST"])
@login_required
def leaves():
    user_id = session["user_id"]
    today   = today_local()

    if request.method == "POST":
        leave_type = request.form.get("leave_type", "").strip()
        start_str  = request.form.get("start_date", "").strip()
        end_str    = request.form.get("end_date",   "").strip()
        reason     = request.form.get("reason",     "").strip()

        if leave_type not in LeaveType.ALL:
            flash("Invalid leave type", "danger")
            return redirect(url_for("leaves.leaves"))
        if not start_str or not end_str or not reason:
            flash("All fields are required", "danger")
            return redirect(url_for("leaves.leaves"))

        try:
            start_date = _dt.strptime(start_str, "%Y-%m-%d").date()
            end_date   = _dt.strptime(end_str,   "%Y-%m-%d").date()
        except ValueError:
            flash("Invalid date format", "danger")
            return redirect(url_for("leaves.leaves"))

        if start_date < today:
            flash("Cannot apply leave for past dates", "danger")
            return redirect(url_for("leaves.leaves"))
        if end_date < start_date:
            flash("End date cannot be before start date", "danger")
            return redirect(url_for("leaves.leaves"))

        days_count = sum(
            1 for i in range((end_date - start_date).days + 1)
            if is_working_day(start_date + timedelta(days=i))
        )
        if days_count == 0:
            flash("No working days in the selected range", "danger")
            return redirect(url_for("leaves.leaves"))

        if leave_type != LeaveType.UNPAID:
            balance = get_leave_balance(user_id, today.year)
            if balance.get(leave_type, 0) < days_count:
                flash(f"Insufficient {leave_type} leave balance ({days_count} days needed)", "danger")
                return redirect(url_for("leaves.leaves"))

        conn = get_db()
        cur  = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO tbl_leaves "
                "(user_id,leave_type,start_date,end_date,days_count,reason,status,applied_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,NOW())",
                (user_id, leave_type, start_date, end_date, days_count, reason, LeaveStatus.PENDING)
            )
            conn.commit()
            flash(f"Leave application submitted for {days_count} working day(s)", "success")
            return redirect(url_for("leaves.leaves"))
        except Exception:
            conn.rollback()
            logger.exception("Leave application failed")
            flash("Failed to submit leave", "danger")
            return redirect(url_for("leaves.leaves"))
        finally:
            cur.close(); conn.close()

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT * FROM tbl_leaves WHERE user_id=%s ORDER BY applied_at DESC LIMIT 50",
        (user_id,)
    )
    my_leaves = cur.fetchall()
    cur.close(); conn.close()

    return render_template(
        "leaves.html",
        leaves=my_leaves,
        leave_balance=get_leave_balance(user_id, today.year),
        leave_types=LeaveType.ALL,
        today=today,
    )


@leaves_bp.route("/leaves/<int:leave_id>/cancel", methods=["POST"])
@login_required
def cancel_leave(leave_id):
    user_id = session["user_id"]
    conn    = get_db()
    cur     = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT id,status FROM tbl_leaves WHERE id=%s AND user_id=%s",
            (leave_id, user_id)
        )
        lv = cur.fetchone()
        if not lv:
            flash("Leave not found", "danger")
            return redirect(url_for("leaves.leaves"))
        if lv["status"] != LeaveStatus.PENDING:
            flash("Only pending leaves can be cancelled", "danger")
            return redirect(url_for("leaves.leaves"))
        cur.execute("UPDATE tbl_leaves SET status=%s WHERE id=%s", (LeaveStatus.CANCELLED, leave_id))
        conn.commit()
        flash("Leave cancelled", "success")
    except Exception:
        conn.rollback()
        flash("Failed to cancel leave", "danger")
    finally:
        cur.close(); conn.close()
    return redirect(url_for("leaves.leaves"))


@leaves_bp.route("/admin/leaves")
@admin_required
def admin_leaves():
    status_filter = request.args.get("status", LeaveStatus.PENDING)
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT l.*, u.first_name, u.last_name, u.email
        FROM tbl_leaves l
        JOIN tbl_users u ON u.user_id=l.user_id
        WHERE l.status=%s
        ORDER BY l.applied_at DESC
    """, (status_filter,))
    leaves_list = cur.fetchall()
    cur.close(); conn.close()
    return render_template(
        "admin_leaves.html",
        leaves=leaves_list,
        status_filter=status_filter,
        statuses=[LeaveStatus.PENDING, LeaveStatus.APPROVED, LeaveStatus.REJECTED],
    )


@leaves_bp.route("/admin/leaves/<int:leave_id>/action", methods=["POST"])
@admin_required
def admin_leave_action(leave_id):
    action   = request.form.get("action")
    comments = request.form.get("comments", "").strip()

    if action not in ("approve", "reject"):
        flash("Invalid action", "danger")
        return redirect(url_for("leaves.admin_leaves"))

    new_status = LeaveStatus.APPROVED if action == "approve" else LeaveStatus.REJECTED
    conn       = get_db()
    cur        = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM tbl_leaves WHERE id=%s", (leave_id,))
        lv = cur.fetchone()
        if not lv or lv["status"] != LeaveStatus.PENDING:
            flash("Leave not found or already processed", "danger")
            return redirect(url_for("leaves.admin_leaves"))

        cur.execute(
            "UPDATE tbl_leaves SET status=%s, admin_comments=%s, reviewed_at=NOW() WHERE id=%s",
            (new_status, comments, leave_id)
        )

        if new_status == LeaveStatus.APPROVED:
            uid        = lv["user_id"]
            start_date = lv["start_date"]
            end_date   = lv["end_date"]
            if hasattr(start_date, "date"):
                start_date = start_date.date()
            if hasattr(end_date, "date"):
                end_date = end_date.date()

            cur_d = start_date
            while cur_d <= end_date:
                if is_working_day(cur_d):
                    cur.execute(
                        "SELECT id FROM tbl_attendance WHERE user_id=%s AND attendance_date=%s",
                        (uid, cur_d)
                    )
                    rec = cur.fetchone()
                    if rec:
                        cur.execute(
                            "UPDATE tbl_attendance SET status=%s,check_in=NULL,check_out=NULL WHERE id=%s",
                            (AttendanceStatus.ON_LEAVE, rec["id"])
                        )
                    else:
                        cur.execute(
                            "INSERT INTO tbl_attendance (user_id,attendance_date,status) VALUES (%s,%s,%s)",
                            (uid, cur_d, AttendanceStatus.ON_LEAVE)
                        )
                cur_d += timedelta(days=1)

        conn.commit()
        audit_log(conn, session["user_id"], f"LEAVE_{action.upper()}", f"leave_id={leave_id}")
        conn.commit()
        flash(f"Leave {new_status.lower()} successfully", "success")
    except Exception:
        conn.rollback()
        logger.exception("Admin leave action failed")
        flash("Action failed", "danger")
    finally:
        cur.close(); conn.close()
    return redirect(url_for("leaves.admin_leaves"))
