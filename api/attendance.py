"""
api/attendance.py
Attendance routes:
  GET  /user/dashboard
  GET  /stream/attendance-status  (SSE)
  GET  /get-attendance-status
  POST /check_in
  POST /checkout
  GET  /face-auth/<action>
  POST /attendance/wfh
  POST /attendance/wfh-checkout
  POST /admin/attendance/correct/<id>
  GET  /admin/attendance-overview
  GET  /admin/team-summary
  GET  /admin/run-nightly-job
"""
import csv
import io
import json
import time
from collections import defaultdict
from datetime import date, timedelta, datetime
from datetime import datetime as _dt
from math import ceil

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, session, jsonify, Response, stream_with_context,
)

from config import CFG
from extensions import get_db, logger, csrf
from schema.models import AttendanceStatus, today_local, now_local
from services.utils import audit_log, login_required, admin_required, api_ok, api_err
from services.attendance import (
    fill_missing_records_for_user, fill_missing_records_for_all_users,
    mark_absent_and_holidays_for_all_users,
    auto_close_pending_checkouts_for_all_users,
    get_pending_checkout, calc_work_hours, derive_status,
    is_late_checkin, stamp_overtime,
)
from services.holiday import (
    is_non_working_day, is_working_day, is_weekend,
    get_holiday_name, get_day_status,
)
from services.leave import get_leave_balance, get_monthly_summary

try:
    from face_utils.recognizer import recognize_user_face_from_base64
except ImportError:
    recognize_user_face_from_base64 = None

attendance_bp = Blueprint("attendance", __name__)


# ── User Dashboard ───────────────────────────────────────────────────
@attendance_bp.route("/user/dashboard")
@login_required
def user_dashboard():
    user_id = session["user_id"]
    today   = today_local()

    fill_missing_records_for_user(user_id)

    page     = int(request.args.get("page", 1))
    per_page = 10
    offset   = (page - 1) * per_page

    conn = get_db()
    cur  = conn.cursor(dictionary=True)

    cur.execute("""
        SELECT a.attendance_date, a.status, a.check_in, a.check_out,
               du.work_summary,
               ROUND(
                 CASE WHEN a.check_in IS NOT NULL AND a.check_out IS NOT NULL
                      THEN TIMESTAMPDIFF(MINUTE,
                             CAST(CONCAT(a.attendance_date,' ',a.check_in)  AS DATETIME),
                             CAST(CONCAT(a.attendance_date,' ',a.check_out) AS DATETIME)
                           ) / 60.0
                      ELSE NULL END, 2
               ) AS hours_worked
        FROM tbl_attendance a
        LEFT JOIN tbl_daily_updates du ON a.user_id=du.user_id AND a.attendance_date=du.update_date
        WHERE a.user_id=%s
        ORDER BY a.attendance_date DESC
        LIMIT %s OFFSET %s
    """, (user_id, per_page, offset))
    attendance = cur.fetchall()

    cur.execute("SELECT COUNT(*) AS total FROM tbl_attendance WHERE user_id=%s", (user_id,))
    total_pages = ceil(cur.fetchone()["total"] / per_page) or 1

    cur.execute("""
        SELECT a.attendance_date, a.status, a.check_in, a.check_out, du.work_summary
        FROM tbl_attendance a
        LEFT JOIN tbl_daily_updates du ON a.user_id=du.user_id AND a.attendance_date=du.update_date
        WHERE a.user_id=%s AND a.attendance_date=%s LIMIT 1
    """, (user_id, today))
    today_attendance = cur.fetchone()

    cur.execute(
        "SELECT u.first_name, u.last_name, u.email, u.joining_date, "
        "u.designation, u.employee_id, d.dept_name "
        "FROM tbl_users u LEFT JOIN tbl_departments d ON d.id=u.dept_id "
        "WHERE u.user_id=%s", (user_id,)
    )
    emp_info = cur.fetchone() or {}
    cur.close(); conn.close()

    leave_balance = get_leave_balance(user_id, today.year)
    monthly       = get_monthly_summary(user_id, today.year, today.month)

    conn2 = get_db()
    cur2  = conn2.cursor(dictionary=True)
    cur2.execute("""
        SELECT sa.cycle_number, sa.employee_rating, sa.employee_comment,
               sa.admin_rating, sa.admin_comment, sa.status,
               ap.appraisal_points
        FROM tbl_self_assessment sa
        LEFT JOIN tbl_appraisal ap ON ap.user_id=sa.user_id AND ap.cycle_number=sa.cycle_number
        WHERE sa.user_id=%s
        ORDER BY sa.cycle_number DESC LIMIT 1
    """, (user_id,))
    latest_assessment = cur2.fetchone()
    cur2.close(); conn2.close()

    return render_template(
        "user_dashboard.html",
        attendance=attendance, today=today,
        today_attendance=today_attendance,
        page=page, per_page=per_page, total_pages=total_pages,
        leave_balance=leave_balance, monthly=monthly,
        emp_info=emp_info,
        latest_assessment=latest_assessment,
    )


# ── Attendance status helpers ────────────────────────────────────────
def _get_status_dict(user_id: int) -> dict:
    today = today_local()
    conn  = get_db()
    cur   = conn.cursor(dictionary=True)
    try:
        if is_non_working_day(today):
            label = "Weekend" if is_weekend(today) else get_holiday_name(today)
            return {"status": "holiday", "message": f"Today is {label}. Attendance not required."}

        cur.execute(
            "SELECT check_in, check_out FROM tbl_attendance "
            "WHERE user_id=%s AND attendance_date=%s", (user_id, today)
        )
        record = cur.fetchone()
        if record:
            if record["check_in"] and record["check_out"]:
                return {"status": "completed"}
            if record["check_in"]:
                return {"status": "checked_in"}

        pending = get_pending_checkout(user_id, conn)
        if pending:
            from services.holiday import is_last_day_before_break
            pd = pending["attendance_date"]
            if not is_last_day_before_break(pd):
                return {
                    "status": "pending_checkout", "button": "check_out",
                    "date":   str(pd),
                    "message": f"Please check out for {pd.strftime('%d %b %Y')} first.",
                }
        return {"status": "not_checked_in"}
    finally:
        cur.close(); conn.close()


@attendance_bp.route("/get-attendance-status")
@login_required
def get_attendance_status():
    return jsonify(_get_status_dict(session["user_id"]))


@attendance_bp.route("/stream/attendance-status")
@login_required
def stream_attendance_status():
    user_id = session["user_id"]

    def generate():
        last_status = None
        for _ in range(120):
            payload = json.dumps(_get_status_dict(user_id))
            if payload != last_status:
                yield f"data: {payload}\n\n"
                last_status = payload
            time.sleep(1)
        yield 'data: {"status": "stream_end"}\n\n'

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Check-in ─────────────────────────────────────────────────────────
@csrf.exempt
@attendance_bp.route("/check_in", methods=["POST"])
@login_required
def check_in():
    user_id = session["user_id"]
    today   = today_local()
    ip      = request.remote_addr or "unknown"

    day_status = get_day_status(today)
    if day_status:
        label = "Weekend" if is_weekend(today) else get_holiday_name(today)
        conn = get_db()
        cur  = conn.cursor(dictionary=True)
        try:
            cur.execute(
                "SELECT id FROM tbl_attendance WHERE user_id=%s AND attendance_date=%s",
                (user_id, today)
            )
            if not cur.fetchone():
                cur.execute(
                    "INSERT INTO tbl_attendance (user_id,attendance_date,status) VALUES (%s,%s,%s)",
                    (user_id, today, day_status)
                )
                conn.commit()
        finally:
            cur.close(); conn.close()
        return api_ok({"status": "holiday"}, f"Today is {label}. Attendance not required.")

    data = request.get_json()
    img  = (data or {}).get("image")
    if not img:
        return api_err("No image provided")

    if recognize_user_face_from_base64 is None:
        return api_err("Face recognition not available", 500)
    try:
        if not recognize_user_face_from_base64(img, user_id):
            session.clear()
            return api_err("Face not recognised.", 401, {"redirect": url_for("auth.login")})
    except Exception:
        logger.exception("Face recognition failed")
        return api_err("Face recognition service error", 500)

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        from services.holiday import is_last_day_before_break
        pending = get_pending_checkout(user_id, conn)
        if pending:
            pd = pending["attendance_date"]
            if is_last_day_before_break(pd):
                while pending:
                    cur.execute(
                        "UPDATE tbl_attendance SET check_out='23:59:00',status=%s WHERE id=%s",
                        (AttendanceStatus.COMPLETED, pending["id"])
                    )
                    conn.commit()
                    pending = get_pending_checkout(user_id, conn)
                    if pending and not is_last_day_before_break(pending["attendance_date"]):
                        break
            else:
                return api_err(
                    f"Please check out for {pd.strftime('%d %b %Y')} first.",
                    400, {"status": "pending_checkout", "button": "check_out", "date": str(pd)}
                )

        now_str = now_local().strftime("%H:%M:%S")
        cur.execute(
            "SELECT id,check_in,check_out FROM tbl_attendance "
            "WHERE user_id=%s AND attendance_date=%s", (user_id, today)
        )
        record = cur.fetchone()

        if record and record["check_in"] and record["check_out"]:
            return api_ok({"status": "already_done"}, "Attendance already completed for today")
        if record and record["check_in"]:
            return api_ok({"status": "already_checked_in"}, "Already checked in. Please check out.")

        late   = is_late_checkin(now_str)
        status = AttendanceStatus.LATE if late else AttendanceStatus.PRESENT

        if not record:
            cur.execute(
                "INSERT INTO tbl_attendance (user_id,attendance_date,check_in,status) VALUES (%s,%s,%s,%s)",
                (user_id, today, now_str, status)
            )
        else:
            cur.execute(
                "UPDATE tbl_attendance SET check_in=%s,status=%s WHERE id=%s",
                (now_str, status, record["id"])
            )
        conn.commit()
        audit_log(conn, user_id, "CHECK_IN", f"time={now_str} late={late} ip={ip}")
        conn.commit()

        msg = "Check-in successful"
        if late:
            msg += f" (Late — grace {CFG.GRACE_MINUTES}min after {CFG.WORK_START_HOUR:02d}:{CFG.WORK_START_MINUTE:02d})"
        return api_ok({"status": "success", "late": late}, msg)
    except Exception:
        conn.rollback()
        logger.exception("Check-in failed for user %s", user_id)
        return api_err("Server error during check-in", 500)
    finally:
        cur.close(); conn.close()


# ── Check-out ─────────────────────────────────────────────────────────
@csrf.exempt
@attendance_bp.route("/checkout", methods=["POST"])
@login_required
def checkout():
    user_id = session["user_id"]
    today   = today_local()
    ip      = request.remote_addr or "unknown"

    data = request.get_json()
    img  = (data or {}).get("image")
    if not img:
        return api_err("No image provided")

    if recognize_user_face_from_base64 is None:
        return api_err("Face recognition not available", 500)
    try:
        if not recognize_user_face_from_base64(img, user_id):
            session.clear()
            return api_err("Face not recognised.", 401, {"redirect": url_for("auth.login")})
    except Exception:
        logger.exception("Face recognition failed")
        return api_err("Face recognition service error", 500)

    now_str = now_local().strftime("%H:%M:%S")
    conn    = get_db()
    cur     = conn.cursor(dictionary=True)
    try:
        pending = get_pending_checkout(user_id, conn)
        if pending:
            final_status = derive_status(str(pending["check_in"]), now_str)
            cur.execute(
                "UPDATE tbl_attendance SET check_out=%s,status=%s WHERE id=%s",
                (now_str, final_status, pending["id"])
            )
            cd = pending["attendance_date"]
            if not is_non_working_day(today):
                cur.execute(
                    "SELECT id,check_in FROM tbl_attendance "
                    "WHERE user_id=%s AND attendance_date=%s", (user_id, today)
                )
                tr = cur.fetchone()
                if not tr:
                    cur.execute(
                        "INSERT INTO tbl_attendance (user_id,attendance_date,check_in,status) VALUES (%s,%s,%s,%s)",
                        (user_id, today, now_str, AttendanceStatus.PRESENT)
                    )
                elif not tr["check_in"]:
                    cur.execute(
                        "UPDATE tbl_attendance SET check_in=%s,status=%s WHERE id=%s",
                        (now_str, AttendanceStatus.PRESENT, tr["id"])
                    )
            conn.commit()
            audit_log(conn, user_id, "CHECKOUT_PENDING", f"for_date={cd} time={now_str} ip={ip}")
            conn.commit()
            return api_ok(
                {"status": "auto_checkin"},
                f"Checked out for {cd.strftime('%d %b %Y')} and checked in for today!"
            )

        if is_non_working_day(today):
            label = "Weekend" if is_weekend(today) else get_holiday_name(today)
            return api_ok({"status": "holiday"}, f"Today is {label}. Check-out not required.")

        cur.execute(
            "SELECT id,check_in,check_out FROM tbl_attendance "
            "WHERE user_id=%s AND attendance_date=%s", (user_id, today)
        )
        record = cur.fetchone()
        if not record or not record["check_in"]:
            return api_err("Check-in required first")
        if record["check_out"]:
            return api_err("Already checked out", 409)

        final_status = derive_status(str(record["check_in"]), now_str)
        hours        = calc_work_hours(str(record["check_in"]), now_str)
        overtime     = max(round(hours - CFG.MIN_WORK_HOURS, 2), 0.0)
        cur.execute(
            "UPDATE tbl_attendance SET check_out=%s, status=%s, overtime_hours=%s WHERE id=%s",
            (now_str, final_status, overtime, record["id"])
        )
        conn.commit()
        audit_log(conn, user_id, "CHECKOUT", f"time={now_str} hours={hours} ip={ip}")
        conn.commit()
        return api_ok(
            {"status": "success", "hours_worked": hours, "final_status": final_status},
            f"Checked out successfully. Hours worked: {hours:.1f}h"
        )
    except Exception:
        conn.rollback()
        logger.exception("Checkout failed for user %s", user_id)
        return api_err("Server error during check-out", 500)
    finally:
        cur.close(); conn.close()


# ── Face auth page ────────────────────────────────────────────────────
@attendance_bp.route("/face-auth/<action>")
@login_required
def face_auth(action):
    if action not in ("checkin", "checkout"):
        return redirect(url_for("attendance.user_dashboard"))
    return render_template("face_auth.html", action=action)


# ── WFH check-in / check-out ─────────────────────────────────────────
@csrf.exempt
@attendance_bp.route("/attendance/wfh", methods=["POST"])
@login_required
def mark_wfh():
    user_id = session["user_id"]
    today   = today_local()

    if is_non_working_day(today):
        return api_err("Today is a non-working day", 400)

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT id, check_in, check_out, work_type FROM tbl_attendance "
            "WHERE user_id=%s AND attendance_date=%s", (user_id, today)
        )
        rec     = cur.fetchone()
        now_str = now_local().strftime("%H:%M:%S")
        late    = is_late_checkin(now_str)
        status  = AttendanceStatus.LATE if late else AttendanceStatus.PRESENT

        if rec and rec["check_in"]:
            cur.execute("UPDATE tbl_attendance SET work_type='wfh' WHERE id=%s", (rec["id"],))
            msg = "Attendance updated to Work From Home"
        elif rec:
            cur.execute(
                "UPDATE tbl_attendance SET check_in=%s, status=%s, work_type='wfh' WHERE id=%s",
                (now_str, status, rec["id"])
            )
            msg = "WFH check-in recorded"
        else:
            cur.execute(
                "INSERT INTO tbl_attendance (user_id,attendance_date,check_in,status,work_type) "
                "VALUES (%s,%s,%s,%s,'wfh')",
                (user_id, today, now_str, status)
            )
            msg = "WFH check-in recorded"

        conn.commit()
        audit_log(conn, user_id, "WFH_CHECKIN", f"time={now_str}")
        conn.commit()
        return api_ok({"status": "wfh", "late": late}, msg)
    except Exception:
        conn.rollback()
        logger.exception("WFH mark failed")
        return api_err("Server error", 500)
    finally:
        cur.close(); conn.close()


@csrf.exempt
@csrf.exempt
@attendance_bp.route("/attendance/wfh-checkout", methods=["POST"])
@login_required
def wfh_checkout():
    user_id = session["user_id"]
    today   = today_local()
    now_str = now_local().strftime("%H:%M:%S")

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT id, check_in, check_out, work_type FROM tbl_attendance "
            "WHERE user_id=%s AND attendance_date=%s", (user_id, today)
        )
        rec = cur.fetchone()
        if not rec or not rec["check_in"]:
            return api_err("Please check in first")
        if rec["check_out"]:
            return api_err("Already checked out", 409)
        if rec["work_type"] != "wfh":
            return api_err("This endpoint is for WFH only", 400)

        final_status = derive_status(str(rec["check_in"]), now_str)
        hours        = calc_work_hours(str(rec["check_in"]), now_str)
        overtime     = max(round(hours - CFG.MIN_WORK_HOURS, 2), 0.0)
        cur.execute(
            "UPDATE tbl_attendance SET check_out=%s, status=%s, overtime_hours=%s WHERE id=%s",
            (now_str, final_status, overtime, rec["id"])
        )
        conn.commit()
        audit_log(conn, user_id, "WFH_CHECKOUT", f"time={now_str} hours={hours}")
        conn.commit()
        return api_ok(
            {"status": "success", "hours_worked": hours, "overtime": overtime},
            f"WFH check-out recorded. Hours: {hours:.1f}h"
        )
    except Exception:
        conn.rollback()
        logger.exception("WFH checkout failed")
        return api_err("Server error", 500)
    finally:
        cur.close(); conn.close()


# ── Admin: manual attendance correction ──────────────────────────────
@csrf.exempt
@attendance_bp.route("/admin/attendance/correct/<int:user_id>", methods=["POST"])
@admin_required
def admin_correct_attendance(user_id):
    date_str  = request.form.get("att_date",  "").strip()
    new_in    = request.form.get("check_in",  "").strip()
    new_out   = request.form.get("check_out", "").strip()
    work_type = request.form.get("work_type", "office").strip()
    note      = request.form.get("note",      "").strip()

    if not date_str:
        flash("Date is required", "danger")
        return redirect(url_for("admin.view_user", user_id=user_id))
    try:
        att_date = _dt.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        flash("Invalid date format", "danger")
        return redirect(url_for("admin.view_user", user_id=user_id))

    ci = new_in  if new_in  else None
    co = new_out if new_out else None
    fs = derive_status(ci, co) if ci else AttendanceStatus.ABSENT
    ot = 0.0
    if ci and co:
        hours = calc_work_hours(ci, co)
        ot    = max(round(hours - CFG.MIN_WORK_HOURS, 2), 0.0)

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT id FROM tbl_attendance WHERE user_id=%s AND attendance_date=%s",
            (user_id, att_date)
        )
        rec = cur.fetchone()
        if rec:
            cur.execute(
                "UPDATE tbl_attendance "
                "SET check_in=%s, check_out=%s, status=%s, work_type=%s, overtime_hours=%s "
                "WHERE id=%s",
                (ci, co, fs, work_type, ot, rec["id"])
            )
        else:
            cur.execute(
                "INSERT INTO tbl_attendance "
                "(user_id,attendance_date,check_in,check_out,status,work_type,overtime_hours) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (user_id, att_date, ci, co, fs, work_type, ot)
            )
        conn.commit()
        audit_log(conn, session["user_id"], "ADMIN_CORRECT_ATT",
                  f"user={user_id} date={att_date} in={ci} out={co} note={note}")
        conn.commit()
        flash(f"Attendance corrected for {att_date.strftime('%d %b %Y')}", "success")
    except Exception:
        conn.rollback()
        logger.exception("Admin attendance correction failed")
        flash("Correction failed", "danger")
    finally:
        cur.close(); conn.close()
    return redirect(url_for("admin.view_user", user_id=user_id))


# ── Admin: attendance overview ────────────────────────────────────────
@attendance_bp.route("/admin/attendance-overview")
@admin_required
def admin_attendance_overview():
    view_date_str = request.args.get("date", str(today_local()))
    try:
        view_date = _dt.strptime(view_date_str, "%Y-%m-%d").date()
    except ValueError:
        view_date = today_local()

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT u.user_id, u.first_name, u.last_name, u.employee_id,
                   u.designation, d.dept_name,
                   a.check_in, a.check_out, a.status, a.work_type, a.overtime_hours,
                   ROUND(
                     CASE WHEN a.check_in IS NOT NULL AND a.check_out IS NOT NULL
                          THEN TIMESTAMPDIFF(MINUTE,
                            CAST(CONCAT(%s,' ',a.check_in)  AS DATETIME),
                            CAST(CONCAT(%s,' ',a.check_out) AS DATETIME)
                          ) / 60.0
                          ELSE NULL END, 2
                   ) AS hours_worked
            FROM tbl_users u
            LEFT JOIN tbl_departments d ON d.id=u.dept_id
            LEFT JOIN tbl_attendance a ON a.user_id=u.user_id AND a.attendance_date=%s
            WHERE LOWER(u.role_type)='employee'
            ORDER BY d.dept_name, u.first_name
        """, (view_date_str, view_date_str, view_date))
        employees = cur.fetchall()

        summary = defaultdict(int)
        for e in employees:
            s = (e["status"] or "not_marked").lower().replace(" ", "_")
            summary[s] += 1
            if e["work_type"] == "wfh":
                summary["wfh"] += 1

        cur.execute("SELECT id, dept_name FROM tbl_departments WHERE is_active=1 ORDER BY dept_name")
        departments = cur.fetchall()
    finally:
        cur.close(); conn.close()

    return render_template(
        "admin_attendance_overview.html",
        employees=employees, view_date=view_date,
        summary=dict(summary), departments=departments,
        is_non_working=is_non_working_day(view_date),
        holiday_name=get_holiday_name(view_date),
    )


# ── Admin: team monthly summary ───────────────────────────────────────
@attendance_bp.route("/admin/team-summary")
@admin_required
def admin_team_summary():
    today = today_local()
    year  = request.args.get("year",  today.year,  type=int)
    month = request.args.get("month", today.month, type=int)

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT user_id, first_name, last_name, employee_id "
            "FROM tbl_users WHERE LOWER(role_type)='employee' ORDER BY first_name"
        )
        employees = cur.fetchall()
        months    = []
        d         = date(today.year, today.month, 1)
        for _ in range(12):
            months.append({"year": d.year, "month": d.month, "label": d.strftime("%b %Y")})
            d = (d - timedelta(days=1)).replace(day=1)
    finally:
        cur.close(); conn.close()

    report = []
    for emp in employees:
        s = get_monthly_summary(emp["user_id"], year, month)
        report.append({
            "user_id":     emp["user_id"],
            "name":        f"{emp['first_name']} {emp['last_name']}",
            "employee_id": emp.get("employee_id") or "—",
            **s,
        })

    return render_template(
        "admin_team_summary.html",
        report=report, year=year, month=month,
        month_label=date(year, month, 1).strftime("%B %Y"),
        months=months,
    )


# ── Admin: nightly job manual trigger ────────────────────────────────
@attendance_bp.route("/admin/run-nightly-job")
@admin_required
def run_nightly_job():
    try:
        mark_absent_and_holidays_for_all_users()
        auto_close_pending_checkouts_for_all_users()
        return api_ok(message="Nightly job completed.")
    except Exception as exc:
        logger.exception("Manual nightly job trigger failed")
        return api_err(str(exc), 500)
