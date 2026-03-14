"""
api/reports.py
Reporting and data-export routes:
  GET /api/report/monthly
  GET /api/report/team
  GET /api/admin/live-status
  GET /api/admin/pending-counts
  GET /api/export/attendance
"""
import csv
import io

from flask import Blueprint, request, session, Response

from extensions import get_db
from schema.models import today_local, now_local, AttendanceStatus
from services.utils import login_required, admin_required, api_ok, api_err
from services.leave import get_monthly_summary
from services.holiday import is_weekend

reports_bp = Blueprint("reports", __name__)


# ── Monthly report (JSON) ────────────────────────────────────────────
@reports_bp.route("/api/report/monthly")
@login_required
def api_monthly_report():
    today     = today_local()
    year      = request.args.get("year",    today.year,  type=int)
    month     = request.args.get("month",   today.month, type=int)
    target_id = request.args.get("user_id", session["user_id"], type=int)

    if session["role"] != "admin" and target_id != session["user_id"]:
        return api_err("Unauthorized", 403)

    summary = get_monthly_summary(target_id, year, month)
    return api_ok({"report": summary, "year": year, "month": month})


# ── Team report (JSON) ───────────────────────────────────────────────
@reports_bp.route("/api/report/team")
@admin_required
def api_team_report():
    today = today_local()
    year  = request.args.get("year",  today.year,  type=int)
    month = request.args.get("month", today.month, type=int)

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT user_id,first_name,last_name FROM tbl_users "
            "WHERE LOWER(role_type)='employee' AND is_active=1 AND joining_date IS NOT NULL"
        )
        employees = cur.fetchall()
    finally:
        cur.close(); conn.close()

    report = []
    for emp in employees:
        s = get_monthly_summary(emp["user_id"], year, month)
        report.append({
            "user_id": emp["user_id"],
            "name":    f"{emp['first_name']} {emp['last_name']}",
            "summary": s,
        })
    return api_ok({"team_report": report, "year": year, "month": month})


# ── Live presence board (JSON) ───────────────────────────────────────
@reports_bp.route("/api/admin/live-status")
@admin_required
def api_admin_live_status():
    today = today_local()
    conn  = get_db()
    cur   = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT u.user_id, u.first_name, u.last_name,
                   u.designation, d.dept_name,
                   a.check_in, a.check_out, a.status, a.work_type,
                   CASE
                     WHEN a.check_in IS NOT NULL AND a.check_out IS NULL THEN
                       ROUND(TIMESTAMPDIFF(MINUTE,
                         CAST(CONCAT(a.attendance_date,' ',a.check_in) AS DATETIME), NOW()
                       ) / 60.0, 2)
                     WHEN a.check_in IS NOT NULL AND a.check_out IS NOT NULL THEN
                       ROUND(TIMESTAMPDIFF(MINUTE,
                         CAST(CONCAT(a.attendance_date,' ',a.check_in)  AS DATETIME),
                         CAST(CONCAT(a.attendance_date,' ',a.check_out) AS DATETIME)
                       ) / 60.0, 2)
                     ELSE NULL
                   END AS hours_so_far
            FROM tbl_users u
            LEFT JOIN tbl_departments d ON d.id=u.dept_id
            LEFT JOIN tbl_attendance a ON a.user_id=u.user_id AND a.attendance_date=%s
            WHERE LOWER(u.role_type)='employee' AND u.is_active=1
            ORDER BY d.dept_name, u.first_name
        """, (today,))
        rows = cur.fetchall()

        buckets = {
            "present": [], "wfh": [], "late": [],
            "absent":  [], "on_leave": [], "not_marked": [],
        }
        for r in rows:
            entry = {
                "user_id":    r["user_id"],
                "name":       f"{r['first_name']} {r['last_name']}",
                "dept":       r["dept_name"] or "—",
                "designation": r["designation"] or "",
                "check_in":   str(r["check_in"])  if r["check_in"]  else None,
                "check_out":  str(r["check_out"]) if r["check_out"] else None,
                "status":     r["status"] or "Not Marked",
                "work_type":  r["work_type"] or "office",
                "hours":      r["hours_so_far"],
            }
            s  = (r["status"] or "").lower()
            wt = (r["work_type"] or "office").lower()
            if s in ("present", "completed"):
                buckets["wfh" if wt == "wfh" else "present"].append(entry)
            elif s == "late":
                buckets["late"].append(entry)
            elif s == "absent":
                buckets["absent"].append(entry)
            elif s == "on leave":
                buckets["on_leave"].append(entry)
            else:
                buckets["not_marked"].append(entry)

        return api_ok({
            "buckets": buckets,
            "summary": {k: len(v) for k, v in buckets.items()},
            "as_of":   now_local().strftime("%H:%M:%S"),
            "date":    str(today),
        })
    finally:
        cur.close(); conn.close()


# ── Pending counts (nav badge numbers) ───────────────────────────────
@reports_bp.route("/api/admin/pending-counts")
@admin_required
def api_pending_counts():
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT COUNT(*) AS cnt FROM tbl_leaves          WHERE status='Pending'")
        leaves = cur.fetchone()["cnt"]
        cur.execute("SELECT COUNT(*) AS cnt FROM tbl_regularizations WHERE status='Pending'")
        regs = cur.fetchone()["cnt"]
        cur.execute("SELECT COUNT(*) AS cnt FROM tbl_self_assessment WHERE status='Pending'")
        assessments = cur.fetchone()["cnt"]
        return api_ok({
            "leaves":         leaves,
            "regularizations": regs,
            "assessments":    assessments,
            "total":          leaves + regs + assessments,
        })
    finally:
        cur.close(); conn.close()


# ── CSV export ────────────────────────────────────────────────────────
@reports_bp.route("/api/export/attendance")
@login_required
def export_attendance():
    today      = today_local()
    year       = request.args.get("year",    today.year,  type=int)
    month      = request.args.get("month",   today.month, type=int)
    target_uid = request.args.get("user_id", type=int)
    is_admin   = session.get("role") == "admin"

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        if is_admin:
            uid_filter = "AND a.user_id=%s " if target_uid else ""
            params     = [year, month] + ([target_uid] if target_uid else [])
            cur.execute(f"""
                SELECT u.employee_id, u.first_name, u.last_name, d.dept_name,
                       a.attendance_date, a.check_in, a.check_out, a.status,
                       a.work_type, a.overtime_hours,
                       ROUND(
                         CASE WHEN a.check_in IS NOT NULL AND a.check_out IS NOT NULL
                              THEN TIMESTAMPDIFF(MINUTE,
                                CAST(CONCAT(a.attendance_date,' ',a.check_in)  AS DATETIME),
                                CAST(CONCAT(a.attendance_date,' ',a.check_out) AS DATETIME)
                              ) / 60.0 ELSE 0 END, 2
                       ) AS hours_worked
                FROM tbl_attendance a
                JOIN tbl_users u ON u.user_id=a.user_id
                LEFT JOIN tbl_departments d ON d.id=u.dept_id
                WHERE YEAR(a.attendance_date)=%s AND MONTH(a.attendance_date)=%s
                  AND LOWER(u.role_type)='employee'
                  {uid_filter}
                ORDER BY u.first_name, a.attendance_date
            """, params)
            filename = f"attendance_{year}_{month:02d}.csv"
        else:
            uid = session["user_id"]
            cur.execute("""
                SELECT a.attendance_date, a.check_in, a.check_out, a.status,
                       a.work_type, a.overtime_hours,
                       ROUND(
                         CASE WHEN a.check_in IS NOT NULL AND a.check_out IS NOT NULL
                              THEN TIMESTAMPDIFF(MINUTE,
                                CAST(CONCAT(a.attendance_date,' ',a.check_in)  AS DATETIME),
                                CAST(CONCAT(a.attendance_date,' ',a.check_out) AS DATETIME)
                              ) / 60.0 ELSE 0 END, 2
                       ) AS hours_worked
                FROM tbl_attendance a
                WHERE a.user_id=%s
                  AND YEAR(a.attendance_date)=%s AND MONTH(a.attendance_date)=%s
                ORDER BY a.attendance_date
            """, (uid, year, month))
            filename = f"my_attendance_{year}_{month:02d}.csv"

        rows = cur.fetchall()
    finally:
        cur.close(); conn.close()

    output = io.StringIO()
    writer = csv.writer(output)

    if is_admin and not target_uid:
        writer.writerow([
            "Employee ID", "First Name", "Last Name", "Department",
            "Date", "Check In", "Check Out", "Status",
            "Work Type", "Hours Worked", "Overtime Hours",
        ])
        for r in rows:
            writer.writerow([
                r.get("employee_id") or "",
                r["first_name"], r["last_name"],
                r.get("dept_name") or "",
                str(r["attendance_date"]),
                str(r["check_in"])  if r["check_in"]  else "",
                str(r["check_out"]) if r["check_out"] else "",
                r["status"], r["work_type"],
                r["hours_worked"], r["overtime_hours"],
            ])
    else:
        writer.writerow([
            "Date", "Check In", "Check Out", "Status",
            "Work Type", "Hours Worked", "Overtime Hours",
        ])
        for r in rows:
            writer.writerow([
                str(r["attendance_date"]),
                str(r["check_in"])  if r["check_in"]  else "",
                str(r["check_out"]) if r["check_out"] else "",
                r["status"], r["work_type"],
                r["hours_worked"], r["overtime_hours"],
            ])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )