"""
services/leave.py
Leave-related service functions:
  - get_leave_balance   — remaining days per leave type for a user/year
  - get_monthly_summary — attendance breakdown for a given month
"""
from extensions import get_db
from config import CFG
from schema.models import AttendanceStatus, LeaveType, LeaveStatus
from services.attendance import calc_work_hours


def get_leave_balance(user_id: int, year: int) -> dict:
    """Return remaining leave days per type for the given user and year."""
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT leave_type, SUM(days_count) AS used
            FROM tbl_leaves
            WHERE user_id=%s AND YEAR(start_date)=%s AND status=%s
            GROUP BY leave_type
        """, (user_id, year, LeaveStatus.APPROVED))
        used_map = {r["leave_type"]: r["used"] for r in cur.fetchall()}
        return {
            LeaveType.ANNUAL:  CFG.ANNUAL_LEAVE_DAYS - used_map.get(LeaveType.ANNUAL, 0),
            LeaveType.SICK:    CFG.SICK_LEAVE_DAYS   - used_map.get(LeaveType.SICK,   0),
            LeaveType.CASUAL:  CFG.CASUAL_LEAVE_DAYS - used_map.get(LeaveType.CASUAL, 0),
        }
    finally:
        cur.close(); conn.close()


def get_monthly_summary(user_id: int, year: int, month: int) -> dict:
    """Return attendance statistics for a specific month."""
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT attendance_date, check_in, check_out, status
            FROM tbl_attendance
            WHERE user_id=%s
              AND YEAR(attendance_date)=%s AND MONTH(attendance_date)=%s
        """, (user_id, year, month))
        rows = cur.fetchall()

        summary = {
            "present": 0, "absent": 0, "late": 0, "half_day": 0,
            "on_leave": 0, "holidays": 0, "weekends": 0,
            "total_hours": 0.0, "working_days": 0,
        }
        for r in rows:
            s = r["status"]
            if s == AttendanceStatus.WEEKEND:
                summary["weekends"]  += 1
            elif s == AttendanceStatus.HOLIDAY:
                summary["holidays"]  += 1
            elif s == AttendanceStatus.ON_LEAVE:
                summary["on_leave"]  += 1
            elif s == AttendanceStatus.ABSENT:
                summary["absent"]    += 1
                summary["working_days"] += 1
            elif s == AttendanceStatus.LATE:
                summary["late"]      += 1
                summary["present"]   += 1
                summary["working_days"] += 1
            elif s == AttendanceStatus.HALF_DAY:
                summary["half_day"]  += 1
                summary["working_days"] += 1
            elif s in (AttendanceStatus.PRESENT, AttendanceStatus.COMPLETED):
                summary["present"]   += 1
                summary["working_days"] += 1

            ci = r.get("check_in")
            co = r.get("check_out")
            if ci and co:
                summary["total_hours"] += calc_work_hours(str(ci), str(co))

        summary["total_hours"] = round(summary["total_hours"], 2)
        summary["attendance_pct"] = (
            round(summary["present"] / summary["working_days"] * 100, 1)
            if summary["working_days"] else 0.0
        )
        return summary
    finally:
        cur.close(); conn.close()
