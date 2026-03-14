"""
services/appraisal.py
Appraisal cycle management:
  - ensure_appraisal_cycles  — create tbl_appraisal rows for each 6-month cycle
  - calculate_appraisal_score — weighted score (40% attendance + 30% updates + 30% self)
  - _get_current_cycle       — latest cycle number for a user
"""
from datetime import date, datetime as _dt

from dateutil.relativedelta import relativedelta

from extensions import get_db, logger
from schema.models import AttendanceStatus, LeaveStatus


def ensure_appraisal_cycles(user_id: int, cur, conn) -> None:
    """
    Create a tbl_appraisal row for every completed 6-month cycle
    since the employee's joining date. Safe to call repeatedly.
    """
    cur.execute("SELECT joining_date FROM tbl_users WHERE user_id=%s", (user_id,))
    row = cur.fetchone()
    if not row or not row.get("joining_date"):
        return

    jd = row["joining_date"]
    if isinstance(jd, _dt):
        jd = jd.date()
    elif isinstance(jd, str):
        jd = _dt.strptime(jd[:10], "%Y-%m-%d").date()

    today        = date.today()
    cycle_number = 0
    cycle_start  = jd

    while True:
        cycle_number += 1
        cycle_end = jd + relativedelta(months=cycle_number * 6)
        if cycle_end > today:
            break

        months_done = cycle_number * 6
        cur.execute(
            "SELECT id FROM tbl_appraisal WHERE user_id=%s AND cycle_number=%s",
            (user_id, cycle_number)
        )
        if not cur.fetchone():
            score = calculate_appraisal_score(user_id, cycle_start, cycle_end, 0)
            cur.execute(
                "INSERT INTO tbl_appraisal (user_id, cycle_number, months_completed, appraisal_points) "
                "VALUES (%s,%s,%s,%s)",
                (user_id, cycle_number, months_done, score)
            )

        cycle_start = cycle_end

    try:
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("ensure_appraisal_cycles commit failed for user %s", user_id)


def calculate_appraisal_score(
    user_id:     int,
    cycle_start: date,
    cycle_end:   date,
    cycle_number: int,
) -> float:
    """
    Score formula  (0–5):
      40% — attendance percentage
      30% — daily update submission rate
      30% — self-assessment rating (admin rating preferred, else employee rating)
    """
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        # ── Attendance percentage ──────────────────────────────────
        cur.execute("""
            SELECT
                SUM(CASE WHEN status IN ('Present','Completed','Late') THEN 1 ELSE 0 END) AS present_days,
                SUM(CASE WHEN status NOT IN ('Weekend','Holiday','On Leave') THEN 1 ELSE 0 END) AS working_days
            FROM tbl_attendance
            WHERE user_id=%s AND attendance_date BETWEEN %s AND %s
        """, (user_id, cycle_start, cycle_end))
        att_row      = cur.fetchone()
        present_days = att_row["present_days"] or 0
        working_days = att_row["working_days"] or 1
        attendance_pct = present_days / working_days

        # ── Daily update rate ──────────────────────────────────────
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM tbl_daily_updates "
            "WHERE user_id=%s AND update_date BETWEEN %s AND %s",
            (user_id, cycle_start, cycle_end)
        )
        update_cnt = cur.fetchone()["cnt"] or 0
        update_pct = min(update_cnt / max(working_days, 1), 1.0)

        # ── Self-assessment rating ────────────────────────────────
        self_pct = 0.0
        if cycle_number:
            cur.execute(
                "SELECT employee_rating, admin_rating FROM tbl_self_assessment "
                "WHERE user_id=%s AND cycle_number=%s",
                (user_id, cycle_number)
            )
            sa = cur.fetchone()
            if sa:
                rating   = sa["admin_rating"] if sa["admin_rating"] is not None else sa["employee_rating"]
                self_pct = (rating or 0.0) / 5.0

        score = (attendance_pct * 0.40 + update_pct * 0.30 + self_pct * 0.30) * 5.0
        return round(min(score, 5.0), 2)

    finally:
        cur.close(); conn.close()


def _get_current_cycle(user_id: int) -> int | None:
    """Return the latest cycle number from tbl_appraisal for this user."""
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT MAX(cycle_number) AS mc FROM tbl_appraisal WHERE user_id=%s",
            (user_id,)
        )
        row = cur.fetchone()
        return row["mc"] if row and row["mc"] else None
    finally:
        cur.close(); conn.close()
