"""
api/holidays.py
Company holiday calendar routes:
  GET  /admin/holidays
  POST /admin/holidays/add
  POST /admin/holidays/<id>/edit
  POST /admin/holidays/<id>/delete
  GET  /api/holidays
"""
import mysql.connector
from datetime import datetime as _dt

from flask import Blueprint, render_template, request, redirect, url_for, flash, session

from extensions import get_db, logger
from schema.models import today_local
from services.utils import audit_log, login_required, admin_required, api_ok
from services.holiday import (
    reload_holiday_cache_from_db,
    HOLIDAY_CACHE, is_weekend,
)

holidays_bp = Blueprint("holidays", __name__)

HOLIDAY_TYPES = ["National", "Regional", "Company", "Optional"]


# ── Helper: fix past Absent rows when a holiday is added/edited ──────
def _stamp_holiday_on_attendance(conn, holiday_date, holiday_name):
    """
    When a holiday is added or edited, any existing tbl_attendance rows
    on that date that are currently marked Absent get corrected to Holiday.
    Also clears check_in / check_out since it was a holiday.
    Called AFTER reload_holiday_cache_from_db() so the cache is already fresh.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE tbl_attendance
               SET status    = 'Holiday',
                   check_in  = NULL,
                   check_out = NULL
             WHERE attendance_date = %s
               AND status IN ('Absent', 'Present', 'Late', 'Half Day')
            """,
            (holiday_date,)
        )
        rows_fixed = cur.rowcount
        conn.commit()
        if rows_fixed:
            logger.info(
                "Retroactive holiday fix: %d attendance rows corrected to Holiday on %s (%s)",
                rows_fixed, holiday_date, holiday_name
            )
    finally:
        cur.close()


# ── Helper: revert Holiday rows back to Absent when holiday is deleted ─
def _revert_holiday_on_attendance(conn, holiday_date, holiday_name):
    """
    When a holiday is deleted, any tbl_attendance rows on that date
    that were marked Holiday get reverted to Absent so the nightly job
    or admin can re-evaluate them.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE tbl_attendance
               SET status = 'Absent'
             WHERE attendance_date = %s
               AND status = 'Holiday'
            """,
            (holiday_date,)
        )
        rows_fixed = cur.rowcount
        conn.commit()
        if rows_fixed:
            logger.info(
                "Holiday deleted: %d attendance rows reverted to Absent on %s (%s)",
                rows_fixed, holiday_date, holiday_name
            )
    finally:
        cur.close()


# ────────────────────────────────────────────────────────────────────

@holidays_bp.route("/admin/holidays")
@admin_required
def admin_holidays():
    year = request.args.get("year", today_local().year, type=int)
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT id, holiday_date, holiday_name, holiday_type, description, created_at "
        "FROM tbl_company_holidays WHERE YEAR(holiday_date)=%s ORDER BY holiday_date ASC",
        (year,)
    )
    holidays   = cur.fetchall()
    today_year = today_local().year
    year_range = list(range(today_year - 1, today_year + 3))
    cur.close(); conn.close()
    return render_template(
        "admin_holidays.html",
        holidays=holidays,
        selected_year=year,
        year_range=year_range,
        holiday_types=HOLIDAY_TYPES,
    )


@holidays_bp.route("/admin/holidays/add", methods=["POST"])
@admin_required
def admin_holiday_add():
    h_date = request.form.get("holiday_date", "").strip()
    h_name = request.form.get("holiday_name", "").strip()
    h_type = request.form.get("holiday_type", "Company").strip()
    h_desc = request.form.get("description",  "").strip()

    if not h_date or not h_name:
        flash("Date and name are required", "danger")
        return redirect(url_for("holidays.admin_holidays"))
    if h_type not in HOLIDAY_TYPES:
        h_type = "Company"

    try:
        parsed = _dt.strptime(h_date, "%Y-%m-%d").date()
    except ValueError:
        flash("Invalid date format", "danger")
        return redirect(url_for("holidays.admin_holidays"))

    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO tbl_company_holidays "
            "(holiday_date, holiday_name, holiday_type, description, created_by) "
            "VALUES (%s,%s,%s,%s,%s)",
            (parsed, h_name, h_type, h_desc, session["user_id"])
        )
        conn.commit()

        # 1. Refresh the in-memory cache so is_working_day() is immediately correct
        reload_holiday_cache_from_db()

        # 2. Fix any past Absent rows on this date across ALL employees
        _stamp_holiday_on_attendance(conn, parsed, h_name)

        audit_log(conn, session["user_id"], "ADD_HOLIDAY", f"{parsed}={h_name}")
        conn.commit()
        flash(f"Holiday '{h_name}' added for {parsed.strftime('%d %b %Y')}", "success")
    except mysql.connector.IntegrityError:
        conn.rollback()
        flash("A holiday already exists on that date. Edit it instead.", "warning")
    except Exception:
        conn.rollback()
        logger.exception("Add holiday failed")
        flash("Failed to add holiday", "danger")
    finally:
        cur.close(); conn.close()
    return redirect(url_for("holidays.admin_holidays", year=parsed.year))


@holidays_bp.route("/admin/holidays/<int:holiday_id>/edit", methods=["POST"])
@admin_required
def admin_holiday_edit(holiday_id):
    h_date = request.form.get("holiday_date", "").strip()
    h_name = request.form.get("holiday_name", "").strip()
    h_type = request.form.get("holiday_type", "Company").strip()
    h_desc = request.form.get("description",  "").strip()

    if not h_date or not h_name:
        flash("Date and name are required", "danger")
        return redirect(url_for("holidays.admin_holidays"))
    try:
        parsed = _dt.strptime(h_date, "%Y-%m-%d").date()
    except ValueError:
        flash("Invalid date format", "danger")
        return redirect(url_for("holidays.admin_holidays"))

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        # Fetch the OLD date before updating — needed to revert if date changed
        cur.execute(
            "SELECT holiday_date FROM tbl_company_holidays WHERE id=%s",
            (holiday_id,)
        )
        old_row  = cur.fetchone()
        old_date = old_row["holiday_date"] if old_row else None

        cur.execute(
            "UPDATE tbl_company_holidays "
            "SET holiday_date=%s, holiday_name=%s, holiday_type=%s, description=%s WHERE id=%s",
            (parsed, h_name, h_type, h_desc, holiday_id)
        )
        conn.commit()

        # 1. Refresh cache
        reload_holiday_cache_from_db()

        # 2. If the date changed, revert the OLD date's Holiday rows back to Absent
        if old_date and old_date != parsed:
            _revert_holiday_on_attendance(conn, old_date, f"(was) {h_name}")

        # 3. Stamp the NEW date — fix any Absent rows on the new date
        _stamp_holiday_on_attendance(conn, parsed, h_name)

        audit_log(conn, session["user_id"], "EDIT_HOLIDAY", f"id={holiday_id} {parsed}={h_name}")
        conn.commit()
        flash("Holiday updated successfully", "success")
    except mysql.connector.IntegrityError:
        conn.rollback()
        flash("Another holiday already exists on that date.", "warning")
    except Exception:
        conn.rollback()
        logger.exception("Edit holiday failed")
        flash("Failed to update holiday", "danger")
    finally:
        cur.close(); conn.close()
    return redirect(url_for("holidays.admin_holidays", year=parsed.year))


@holidays_bp.route("/admin/holidays/<int:holiday_id>/delete", methods=["POST"])
@admin_required
def admin_holiday_delete(holiday_id):
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT holiday_date, holiday_name FROM tbl_company_holidays WHERE id=%s",
            (holiday_id,)
        )
        row = cur.fetchone()
        if not row:
            flash("Holiday not found", "danger")
            return redirect(url_for("holidays.admin_holidays"))

        year         = row["holiday_date"].year if hasattr(row["holiday_date"], "year") else today_local().year
        holiday_date = row["holiday_date"]
        holiday_name = row["holiday_name"]

        cur.execute("DELETE FROM tbl_company_holidays WHERE id=%s", (holiday_id,))
        conn.commit()

        # 1. Refresh cache — this date is no longer a holiday
        reload_holiday_cache_from_db()

        # 2. Revert any Holiday rows on this date back to Absent
        _revert_holiday_on_attendance(conn, holiday_date, holiday_name)

        audit_log(conn, session["user_id"], "DELETE_HOLIDAY",
                  f"id={holiday_id} {holiday_name}")
        conn.commit()
        flash(f"Holiday '{holiday_name}' deleted", "success")
    except Exception:
        conn.rollback()
        logger.exception("Delete holiday failed")
        flash("Failed to delete holiday", "danger")
    finally:
        cur.close(); conn.close()
    return redirect(url_for("holidays.admin_holidays", year=year))


@holidays_bp.route("/api/holidays")
@login_required
def api_holidays():
    year = request.args.get("year", today_local().year, type=int)
    data = [
        {"date": str(d), "name": n, "weekend": is_weekend(d)}
        for d, n in sorted(HOLIDAY_CACHE.items())
        if d.year == year
    ]
    return api_ok({"holidays": data, "year": year, "count": len(data)})