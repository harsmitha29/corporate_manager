"""
services/attendance.py
Attendance business logic:
  - check-in / check-out status derivation
  - fill missing records per user
  - nightly absent-marking job
  - overtime stamping
"""
from datetime import datetime, date, timedelta
from datetime import datetime as _dt

from config import CFG
from extensions import get_db, logger
from schema.models import AttendanceStatus, today_local, now_local
from services.holiday import (
    is_weekend, is_calculated_holiday, is_non_working_day,
    is_working_day, is_last_day_before_break,
)
from services.utils import audit_log


# ── Time helpers ─────────────────────────────────────────────────────
def _expected_start() -> datetime:
    today = today_local()
    return datetime(
        today.year, today.month, today.day,
        CFG.WORK_START_HOUR, CFG.WORK_START_MINUTE,
        tzinfo=CFG.TIMEZONE
    )


def is_late_checkin(check_in_time: str) -> bool:
    grace_end = _expected_start() + timedelta(minutes=CFG.GRACE_MINUTES)
    h, m, s   = map(int, check_in_time.split(":"))
    today     = today_local()
    ci = datetime(today.year, today.month, today.day, h, m, s, tzinfo=CFG.TIMEZONE)
    return ci > grace_end


def calc_work_hours(check_in: str, check_out: str) -> float:
    try:
        fmt = "%H:%M:%S"
        ci  = _dt.strptime(check_in,  fmt)
        co  = _dt.strptime(check_out, fmt)
        return max(round((co - ci).total_seconds() / 3600, 2), 0.0)
    except Exception:
        return 0.0


def derive_status(check_in: str, check_out: str | None) -> str:
    if not check_in:
        return AttendanceStatus.ABSENT
    if not check_out:
        return AttendanceStatus.PRESENT
    hours = calc_work_hours(check_in, check_out)
    if hours < CFG.MIN_WORK_HOURS / 2:
        return AttendanceStatus.HALF_DAY
    if is_late_checkin(check_in):
        return AttendanceStatus.LATE
    return AttendanceStatus.COMPLETED


def _is_past_absent_cutoff() -> bool:
    n = now_local()
    return (n.hour > CFG.ABSENT_CUTOFF_HOUR or
            (n.hour == CFG.ABSENT_CUTOFF_HOUR and n.minute >= CFG.ABSENT_CUTOFF_MINUTE))


# ── Pending checkout ─────────────────────────────────────────────────
def get_pending_checkout(user_id: int, conn):
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT id, attendance_date, check_in
            FROM tbl_attendance
            WHERE user_id = %s
              AND check_in IS NOT NULL
              AND (check_out IS NULL OR check_out = '')
              AND DATE(attendance_date) < CURDATE()
              AND DAYOFWEEK(attendance_date) NOT IN (1, 7)
            ORDER BY attendance_date ASC LIMIT 1
        """, (user_id,))
        record = cur.fetchone()
        if record:
            ad = record["attendance_date"]
            if hasattr(ad, "date"):
                record["attendance_date"] = ad.date()
            if is_calculated_holiday(record["attendance_date"]):
                return None
        return record
    finally:
        cur.close()


# ── Auto-close pending checkouts ─────────────────────────────────────
def auto_close_pending_checkouts_for_all_users() -> None:
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT user_id FROM tbl_users WHERE LOWER(role_type)='employee'")
        for user in cur.fetchall():
            uid = user["user_id"]
            cur.execute("""
                SELECT id, attendance_date FROM tbl_attendance
                WHERE user_id=%s
                  AND check_in IS NOT NULL
                  AND (check_out IS NULL OR check_out='')
                  AND DATE(attendance_date) < CURDATE()
                  AND DAYOFWEEK(attendance_date) NOT IN (1, 7)
                ORDER BY attendance_date ASC
            """, (uid,))
            for record in cur.fetchall():
                att_date = record["attendance_date"]
                if hasattr(att_date, "date"):
                    att_date = att_date.date()
                if is_last_day_before_break(att_date):
                    cur.execute(
                        "UPDATE tbl_attendance SET check_out='23:59:00', status=%s WHERE id=%s",
                        (AttendanceStatus.COMPLETED, record["id"])
                    )
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("auto_close_pending_checkouts failed")
    finally:
        cur.close(); conn.close()


# ── Fill missing attendance records ──────────────────────────────────
def fill_missing_records_for_user(user_id: int) -> None:
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT joining_date FROM tbl_users "
            "WHERE user_id=%s AND joining_date IS NOT NULL", (user_id,)
        )
        row = cur.fetchone()
        if not row:
            return

        today    = today_local()
        raw_date = row["joining_date"]
        if isinstance(raw_date, datetime):
            cur_date = raw_date.date()
        elif isinstance(raw_date, date):
            cur_date = raw_date
        elif isinstance(raw_date, str):
            cur_date = _dt.strptime(raw_date[:10], "%Y-%m-%d").date()
        else:
            logger.error("[Fill] Unexpected joining_date type for user %s", user_id)
            return

        while cur_date < today:
            cur.execute(
                "SELECT id, check_in, check_out, status FROM tbl_attendance "
                "WHERE user_id=%s AND attendance_date=%s",
                (user_id, cur_date)
            )
            record = cur.fetchone()

            if not record:
                if is_weekend(cur_date):
                    status = AttendanceStatus.WEEKEND
                elif is_calculated_holiday(cur_date):
                    status = AttendanceStatus.HOLIDAY
                else:
                    status = AttendanceStatus.ABSENT
                cur.execute(
                    "INSERT INTO tbl_attendance (user_id, attendance_date, status) "
                    "VALUES (%s,%s,%s)", (user_id, cur_date, status)
                )
            else:
                if (record["status"] == AttendanceStatus.ABSENT
                        and is_non_working_day(cur_date)
                        and not record.get("check_in")):
                    correct = AttendanceStatus.WEEKEND if is_weekend(cur_date) else AttendanceStatus.HOLIDAY
                    cur.execute("UPDATE tbl_attendance SET status=%s WHERE id=%s", (correct, record["id"]))

                if (is_non_working_day(cur_date)
                        and record.get("check_in")
                        and record["status"] in (AttendanceStatus.PRESENT, AttendanceStatus.ABSENT)):
                    correct = AttendanceStatus.WEEKEND if is_weekend(cur_date) else AttendanceStatus.HOLIDAY
                    cur.execute(
                        "UPDATE tbl_attendance SET check_in=NULL, check_out=NULL, status=%s WHERE id=%s",
                        (correct, record["id"])
                    )

                if record.get("check_in") and not record.get("check_out") and is_working_day(cur_date):
                    final_status = derive_status(record["check_in"], "23:59:00")
                    cur.execute(
                        "UPDATE tbl_attendance SET check_out='23:59:00', status=%s WHERE id=%s",
                        (final_status, record["id"])
                    )

            cur_date += timedelta(days=1)

        # Handle today
        cur.execute(
            "SELECT id, check_in, check_out, status FROM tbl_attendance "
            "WHERE user_id=%s AND attendance_date=%s", (user_id, today)
        )
        today_rec = cur.fetchone()

        if is_non_working_day(today):
            correct = AttendanceStatus.WEEKEND if is_weekend(today) else AttendanceStatus.HOLIDAY
            if not today_rec:
                cur.execute(
                    "INSERT INTO tbl_attendance (user_id, attendance_date, status) VALUES (%s,%s,%s)",
                    (user_id, today, correct)
                )
            elif today_rec["status"] != correct:
                cur.execute(
                    "UPDATE tbl_attendance SET check_in=NULL, check_out=NULL, status=%s WHERE id=%s",
                    (correct, today_rec["id"])
                )
        elif is_working_day(today):
            past_cutoff = _is_past_absent_cutoff()
            if not today_rec:
                if past_cutoff:
                    cur.execute(
                        "INSERT INTO tbl_attendance (user_id, attendance_date, status) VALUES (%s,%s,%s)",
                        (user_id, today, AttendanceStatus.ABSENT)
                    )
            elif not today_rec.get("check_in") and today_rec["status"] not in (
                AttendanceStatus.HOLIDAY, AttendanceStatus.WEEKEND, AttendanceStatus.ON_LEAVE
            ):
                if past_cutoff:
                    cur.execute(
                        "UPDATE tbl_attendance SET status=%s WHERE id=%s",
                        (AttendanceStatus.ABSENT, today_rec["id"])
                    )
                elif today_rec["status"] == AttendanceStatus.ABSENT:
                    cur.execute("DELETE FROM tbl_attendance WHERE id=%s", (today_rec["id"],))

        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("[Fill] FAILED for user %s", user_id)
    finally:
        cur.close(); conn.close()


def fill_missing_records_for_all_users() -> None:
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT user_id FROM tbl_users "
            "WHERE LOWER(role_type)='employee' AND joining_date IS NOT NULL"
        )
        user_ids = [r["user_id"] for r in cur.fetchall()]
    finally:
        cur.close(); conn.close()
    for uid in user_ids:
        try:
            fill_missing_records_for_user(uid)
        except Exception:
            logger.exception("[FillAll] Failed for user %s", uid)


# ── Nightly job ──────────────────────────────────────────────────────
def mark_absent_and_holidays_for_all_users() -> None:
    conn  = get_db()
    cur   = conn.cursor(dictionary=True)
    today = today_local()
    try:
        cur.execute("""
            SELECT user_id FROM tbl_users
            WHERE LOWER(role_type)='employee'
              AND joining_date IS NOT NULL
              AND joining_date <= %s
        """, (today,))
        for emp in cur.fetchall():
            uid = emp["user_id"]
            cur.execute(
                "SELECT id, check_in, check_out, status FROM tbl_attendance "
                "WHERE user_id=%s AND attendance_date=%s", (uid, today)
            )
            rec = cur.fetchone()

            if is_non_working_day(today):
                correct = AttendanceStatus.WEEKEND if is_weekend(today) else AttendanceStatus.HOLIDAY
                if not rec:
                    cur.execute(
                        "INSERT INTO tbl_attendance (user_id, attendance_date, status) VALUES (%s,%s,%s)",
                        (uid, today, correct)
                    )
                elif rec["status"] != correct:
                    cur.execute(
                        "UPDATE tbl_attendance SET check_in=NULL, check_out=NULL, status=%s WHERE id=%s",
                        (correct, rec["id"])
                    )
            elif is_working_day(today):
                if not rec:
                    cur.execute(
                        "INSERT INTO tbl_attendance (user_id, attendance_date, status) VALUES (%s,%s,%s)",
                        (uid, today, AttendanceStatus.ABSENT)
                    )
                elif not rec.get("check_in") and rec["status"] not in (
                    AttendanceStatus.HOLIDAY, AttendanceStatus.WEEKEND, AttendanceStatus.ON_LEAVE
                ):
                    cur.execute(
                        "UPDATE tbl_attendance SET status=%s WHERE id=%s",
                        (AttendanceStatus.ABSENT, rec["id"])
                    )
                elif rec.get("check_in") and not rec.get("check_out"):
                    final_status = derive_status(str(rec["check_in"]), "23:59:00")
                    cur.execute(
                        "UPDATE tbl_attendance SET check_out='23:59:00', status=%s WHERE id=%s",
                        (final_status, rec["id"])
                    )

        conn.commit()
        logger.info("Nightly job completed for %s.", today)
    except Exception:
        conn.rollback()
        logger.exception("Nightly job failed")
    finally:
        cur.close(); conn.close()


# ── Overtime ─────────────────────────────────────────────────────────
def stamp_overtime(conn, att_id: int, check_in: str, check_out: str) -> None:
    """Calculate and write overtime_hours for a completed attendance record."""
    hours = calc_work_hours(check_in, check_out)
    ot    = max(round(hours - CFG.MIN_WORK_HOURS, 2), 0.0)
    cur   = conn.cursor()
    cur.execute("UPDATE tbl_attendance SET overtime_hours=%s WHERE id=%s", (ot, att_id))
    cur.close()
