"""
api/daily_updates.py
Daily work log routes:
  GET/POST /daily-update
  GET  /all-daily-updates
  GET/POST /edit-daily-update/<id>
  POST /delete-daily-update/<id>
  (+ redirect aliases)
"""
from math import ceil
from datetime import datetime as _dt

from flask import Blueprint, render_template, request, redirect, url_for, flash, session

from extensions import get_db
from schema.models import today_local
from services.utils import login_required

daily_updates_bp = Blueprint("daily_updates", __name__)


@daily_updates_bp.route("/daily-update", methods=["GET", "POST"])
@login_required
def daily_update():
    user_id = session["user_id"]
    today   = today_local()
    conn    = get_db()
    cur     = conn.cursor(dictionary=True)

    if request.method == "POST":
        ds = request.form.get("update_date")
        pt = request.form.get("project_title", "").strip()
        ws = request.form.get("work_summary",  "").strip()
        rm = request.form.get("remarks",       "").strip()

        if not ds or not pt or not ws:
            flash("All required fields must be filled", "danger")
            cur.close(); conn.close()
            return render_template("daily_update.html", today=today, existing=None)

        ud = _dt.strptime(ds, "%Y-%m-%d").date()
        cur.execute(
            "SELECT check_in FROM tbl_attendance WHERE user_id=%s AND attendance_date=%s",
            (user_id, ud)
        )
        att = cur.fetchone()
        if not att or not att["check_in"]:
            flash("Please check in before submitting a daily update", "danger")
            cur.close(); conn.close()
            return render_template("daily_update.html", today=today, existing=None)

        cur.execute(
            "SELECT update_id FROM tbl_daily_updates WHERE user_id=%s AND update_date=%s",
            (user_id, ud)
        )
        ex = cur.fetchone()
        if ex:
            cur.execute(
                "UPDATE tbl_daily_updates "
                "SET project_title=%s, work_summary=%s, remarks=%s, updated_at=NOW() "
                "WHERE update_id=%s",
                (pt, ws, rm, ex["update_id"])
            )
            flash("Daily update updated successfully", "success")
        else:
            cur.execute(
                "INSERT INTO tbl_daily_updates (user_id,update_date,project_title,work_summary,remarks) "
                "VALUES (%s,%s,%s,%s,%s)",
                (user_id, ud, pt, ws, rm)
            )
            flash("Daily update submitted successfully", "success")

        conn.commit()
        cur.close(); conn.close()
        return redirect(url_for("daily_updates.all_daily_updates"))

    cur.close(); conn.close()

    # GET — pre-load today's existing entry
    conn2 = get_db()
    cur2  = conn2.cursor(dictionary=True)
    cur2.execute(
        "SELECT update_id,update_date,project_title,work_summary,remarks "
        "FROM tbl_daily_updates WHERE user_id=%s AND update_date=%s",
        (user_id, today)
    )
    existing = cur2.fetchone()
    cur2.close(); conn2.close()
    return render_template("daily_update.html", today=today, existing=existing)


@daily_updates_bp.route("/all-daily-updates")
@login_required
def all_daily_updates():
    user_id  = session["user_id"]
    page     = int(request.args.get("page", 1))
    per_page = 10
    offset   = (page - 1) * per_page
    conn     = get_db()
    cur      = conn.cursor(dictionary=True)

    cur.execute("SELECT COUNT(*) AS total FROM tbl_daily_updates WHERE user_id=%s", (user_id,))
    total       = cur.fetchone()["total"]
    total_pages = ceil(total / per_page) or 1

    cur.execute(
        "SELECT update_id,update_date,project_title,work_summary,remarks,updated_at "
        "FROM tbl_daily_updates WHERE user_id=%s "
        "ORDER BY update_date DESC LIMIT %s OFFSET %s",
        (user_id, per_page, offset)
    )
    updates = cur.fetchall()
    cur.close(); conn.close()
    return render_template(
        "all_daily_updates.html",
        updates=updates, page=page, total_pages=total_pages, total_records=total
    )


@daily_updates_bp.route("/edit-daily-update/<int:update_id>", methods=["GET", "POST"])
@login_required
def edit_daily_update(update_id):
    user_id = session["user_id"]
    conn    = get_db()
    cur     = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT update_id,update_date,project_title,work_summary,remarks "
        "FROM tbl_daily_updates WHERE update_id=%s AND user_id=%s",
        (update_id, user_id)
    )
    update = cur.fetchone()
    if not update:
        cur.close(); conn.close()
        flash("Update not found", "danger")
        return redirect(url_for("daily_updates.all_daily_updates"))

    if request.method == "POST":
        pt = request.form.get("project_title", "").strip()
        ws = request.form.get("work_summary",  "").strip()
        rm = request.form.get("remarks",       "").strip()
        ud = request.form.get("update_date",   "").strip() or update["update_date"]
        if not pt or not ws:
            flash("Project title and Work Summary are required", "danger")
            return render_template("edit_daily_update.html", update=update)
        cur.execute(
            "UPDATE tbl_daily_updates "
            "SET project_title=%s, work_summary=%s, remarks=%s, update_date=%s, updated_at=NOW() "
            "WHERE update_id=%s AND user_id=%s",
            (pt, ws, rm, ud, update_id, user_id)
        )
        conn.commit()
        cur.close(); conn.close()
        flash("Daily update edited successfully", "success")
        return redirect(url_for("daily_updates.all_daily_updates"))

    cur.close(); conn.close()
    return render_template("edit_daily_update.html", update=update)


@daily_updates_bp.route("/delete-daily-update/<int:update_id>", methods=["POST"])
@login_required
def delete_daily_update(update_id):
    user_id = session["user_id"]
    conn    = get_db()
    cur     = conn.cursor()
    cur.execute("DELETE FROM tbl_daily_updates WHERE update_id=%s AND user_id=%s", (update_id, user_id))
    conn.commit()
    cur.close(); conn.close()
    flash("Daily update deleted", "success")
    return redirect(url_for("daily_updates.all_daily_updates"))


# Redirect aliases for backward compatibility
@daily_updates_bp.route("/daily-update/new")
@login_required
def daily_update_new():
    return redirect(url_for("daily_updates.daily_update"))

@daily_updates_bp.route("/daily-updates/all")
@login_required
def daily_updates_all():
    return redirect(url_for("daily_updates.all_daily_updates"))

@daily_updates_bp.route("/daily-updates/log")
@login_required
def my_daily_updates_log():
    return redirect(url_for("daily_updates.all_daily_updates"))
