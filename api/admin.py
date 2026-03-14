"""
api/admin.py
Admin management routes:
  GET  /admin/panel
  GET/POST /admin/create-employee
  GET  /admin/view-user/<id>
  GET/POST /admin/edit-user/<id>
  POST /admin/delete-user/<id>
"""
import mysql.connector
from datetime import date
from datetime import datetime as _dt
from math import ceil

from dateutil.relativedelta import relativedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, session

from config import CFG
from extensions import get_db, logger
from schema.models import today_local, now_local
from services.utils import audit_log, login_required, admin_required, is_valid_name, is_valid_email, validate_password
from services.attendance import fill_missing_records_for_user, calc_work_hours
from services.leave import get_monthly_summary
from services.holiday import is_working_day

admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/admin/panel")
@admin_required
def admin_panel():
    # Task 2.5: fill_missing_records_for_all_users() removed from here.
    # It now runs only at startup and in the nightly scheduler.
    PER_PAGE    = 10
    page        = request.args.get("page", 1, type=int)
    year_filter = request.args.get("year", type=int)
    search_q    = request.args.get("q", "").strip()
    dept_filter = request.args.get("dept", type=int)
    offset      = (page - 1) * PER_PAGE

    conn = get_db()
    cur  = conn.cursor(dictionary=True)

    cur.execute(
        "SELECT DISTINCT YEAR(joining_date) AS year FROM tbl_users "
        "WHERE LOWER(role_type)='employee' AND is_active=1 AND joining_date IS NOT NULL ORDER BY year DESC"
    )
    available_years = [r["year"] for r in cur.fetchall()]

    where  = "LOWER(u.role_type)='employee' AND u.is_active=1"
    params = []
    if year_filter:
        where += " AND YEAR(u.joining_date)=%s"
        params.append(year_filter)
    if search_q:
        where += (
            " AND (u.first_name LIKE %s OR u.last_name LIKE %s OR u.email LIKE %s"
            " OR CONCAT(u.first_name,' ',u.last_name) LIKE %s OR u.employee_id LIKE %s)"
        )
        like = f"%{search_q}%"
        params.extend([like, like, like, like, like])
    if dept_filter:
        where += " AND u.dept_id=%s"
        params.append(dept_filter)

    cur.execute(f"""
        SELECT u.user_id, u.first_name, u.last_name, u.email,
               u.joining_date, u.designation, u.employee_id, d.dept_name,
               a.appraisal_points, a.cycle_number,
               a.calculated_at AS last_appraisal_date,
               (SELECT status FROM tbl_attendance
                WHERE user_id=u.user_id AND attendance_date=CURDATE() LIMIT 1) AS today_status
        FROM tbl_users u
        LEFT JOIN tbl_departments d ON d.id = u.dept_id
        LEFT JOIN tbl_appraisal a ON u.user_id = a.user_id
            AND a.cycle_number = (
                SELECT MAX(a2.cycle_number) FROM tbl_appraisal a2 WHERE a2.user_id=u.user_id
            )
        WHERE {where}
        ORDER BY u.created_at DESC
        LIMIT %s OFFSET %s
    """, tuple(params + [PER_PAGE, offset]))
    users = cur.fetchall()

    cur.execute(f"SELECT COUNT(*) AS total FROM tbl_users u WHERE {where}", tuple(params) if params else ())
    total_pages = ceil(cur.fetchone()["total"] / PER_PAGE) or 1

    today = today_local()
    cur.execute("""
        SELECT
            SUM(status IN ('Present','Completed','Late')) AS present_count,
            SUM(status='Absent')   AS absent_count,
            SUM(status='On Leave') AS leave_count
        FROM tbl_attendance a
        JOIN tbl_users u ON u.user_id=a.user_id
        WHERE a.attendance_date=%s AND LOWER(u.role_type)='employee' AND u.is_active=1
    """, (today,))
    stats = cur.fetchone() or {}

    cur.execute("SELECT id, dept_name FROM tbl_departments WHERE is_active=1 ORDER BY dept_name")
    departments = cur.fetchall()
    cur.close(); conn.close()

    return render_template(
        "admin_panel.html",
        users=users, page=page, total_pages=total_pages,
        available_years=available_years, selected_year=year_filter,
        search_q=search_q, today_stats=stats,
        current_year=today_local().year,
        departments=departments, selected_dept=dept_filter,
    )


@admin_bp.route("/admin/create-employee", methods=["GET", "POST"])
@admin_required
def create_employee():
    if request.method == "POST":
        first       = request.form.get("first_name", "").strip()
        last        = request.form.get("last_name",  "").strip()
        email       = request.form.get("email",      "").strip().lower()
        pwd         = request.form.get("password",   "").strip()
        confirm     = request.form.get("confirm_password", "").strip()
        joining     = request.form.get("joining_date", "").strip()
        dept_id     = request.form.get("dept_id") or None
        designation = request.form.get("designation", "").strip() or None
        emp_id      = request.form.get("employee_id", "").strip() or None

        if not is_valid_name(first) or not is_valid_name(last):
            flash("Please enter valid names (letters/spaces/hyphens, 2-50 chars)", "danger")
            return redirect(url_for("admin.create_employee"))
        if not is_valid_email(email):
            flash("Please enter a valid email address", "danger")
            return redirect(url_for("admin.create_employee"))
        errs = validate_password(pwd)
        if errs:
            flash(f"Password must contain: {', '.join(errs)}", "danger")
            return redirect(url_for("admin.create_employee"))
        if pwd != confirm:
            flash("Passwords do not match", "danger")
            return redirect(url_for("admin.create_employee"))

        joining_date = None
        if joining:
            try:
                joining_date = _dt.strptime(joining, "%Y-%m-%d").date()
            except ValueError:
                flash("Invalid joining date format", "danger")
                return redirect(url_for("admin.create_employee"))

        conn = get_db()
        cur  = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT user_id FROM tbl_users WHERE LOWER(email)=%s", (email,))
            if cur.fetchone():
                flash("An account with this email already exists!", "danger")
                return redirect(url_for("admin.create_employee"))
            if emp_id:
                cur.execute("SELECT user_id FROM tbl_users WHERE employee_id=%s", (emp_id,))
                if cur.fetchone():
                    flash("Employee ID already exists!", "danger")
                    return redirect(url_for("admin.create_employee"))
            cur.execute(
                "INSERT INTO tbl_users "
                "(first_name,last_name,email,password,role_type,created_at,joining_date,"
                "dept_id,designation,employee_id) "
                "VALUES (%s,%s,%s,%s,'employee',NOW(),%s,%s,%s,%s)",
                (first, last, email, pwd, joining_date, dept_id, designation, emp_id)
            )
            new_id = cur.lastrowid
            conn.commit()
            audit_log(conn, session["user_id"], "CREATE_EMPLOYEE", f"new_user_id={new_id}")
            conn.commit()
            flash(f"Employee '{first} {last}' created successfully!", "success")
            return redirect(url_for("admin.admin_panel"))
        except mysql.connector.IntegrityError:
            conn.rollback()
            flash("Email or Employee ID already exists!", "danger")
            return redirect(url_for("admin.create_employee"))
        except Exception:
            conn.rollback()
            logger.exception("Create employee failed")
            flash("Failed to create employee.", "danger")
            return redirect(url_for("admin.create_employee"))
        finally:
            cur.close(); conn.close()

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT id, dept_name, dept_code FROM tbl_departments WHERE is_active=1 ORDER BY dept_name")
    departments = cur.fetchall()
    cur.close(); conn.close()
    return render_template("create_employee.html", departments=departments)


@admin_bp.route("/admin/view-user/<int:user_id>")
@admin_required
def view_user(user_id):
    page     = int(request.args.get("page", 1))
    per_page = 10
    offset   = (page - 1) * per_page

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT u.user_id, u.first_name, u.last_name, u.email, u.role_type, "
        "u.created_at, u.joining_date, u.designation, u.employee_id, d.dept_name "
        "FROM tbl_users u LEFT JOIN tbl_departments d ON d.id=u.dept_id "
        "WHERE u.user_id=%s", (user_id,)
    )
    user = cur.fetchone()
    if not user:
        cur.close(); conn.close()
        flash("User not found", "danger")
        return redirect(url_for("admin.admin_panel"))
    cur.close(); conn.close()

    fill_missing_records_for_user(user_id)

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT COUNT(*) AS total FROM tbl_attendance WHERE user_id=%s", (user_id,))
    pages = ceil(cur.fetchone()["total"] / per_page) or 1

    cur.execute("""
        SELECT a.attendance_date, a.check_in, a.check_out, a.status,
               du.work_summary,
               ROUND(TIMESTAMPDIFF(MINUTE,
                 CAST(CONCAT(a.attendance_date,' ',a.check_in)  AS DATETIME),
                 CAST(CONCAT(a.attendance_date,' ',a.check_out) AS DATETIME)
               ) / 60, 2) AS hours_worked
        FROM tbl_attendance a
        LEFT JOIN tbl_daily_updates du ON a.user_id=du.user_id AND a.attendance_date=du.update_date
        WHERE a.user_id=%s
        ORDER BY a.attendance_date DESC
        LIMIT %s OFFSET %s
    """, (user_id, per_page, offset))
    attendance = cur.fetchall()

    n       = now_local()
    monthly = get_monthly_summary(user_id, n.year, n.month)
    cur.close(); conn.close()

    return render_template(
        "view_user.html",
        user=user, attendance=attendance,
        page=page, pages=pages, monthly=monthly,
    )


@admin_bp.route("/admin/edit-user/<int:user_id>", methods=["GET", "POST"])
@admin_required
def edit_user(user_id):
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT user_id,first_name,last_name,email,role_type,created_at "
        "FROM tbl_users WHERE user_id=%s", (user_id,)
    )
    user = cur.fetchone()
    if not user:
        cur.close(); conn.close()
        flash("User not found", "danger")
        return redirect(url_for("admin.admin_panel"))

    if request.method == "POST":
        first       = request.form.get("first_name",  "").strip()
        last        = request.form.get("last_name",   "").strip()
        score       = request.form.get("appraisal_points")
        cycle       = request.form.get("selected_cycle")
        dept_id     = request.form.get("dept_id") or None
        designation = request.form.get("designation", "").strip() or None
        emp_id      = request.form.get("employee_id", "").strip() or None
        phone       = request.form.get("phone", "").strip() or None
        gender      = request.form.get("gender", "").strip() or None

        if not is_valid_name(first) or not is_valid_name(last):
            flash("Invalid name format", "danger")
            return redirect(url_for("admin.edit_user", user_id=user_id))

        cur.execute(
            "UPDATE tbl_users SET first_name=%s, last_name=%s, "
            "dept_id=%s, designation=%s, employee_id=%s, phone=%s, gender=%s "
            "WHERE user_id=%s",
            (first, last, dept_id, designation, emp_id, phone, gender, user_id)
        )
        if score and cycle:
            try:
                s = round(min(max(float(score), 0.0), 5.0), 2)
                cur.execute(
                    "UPDATE tbl_appraisal SET appraisal_points=%s, calculated_at=NOW() "
                    "WHERE user_id=%s AND cycle_number=%s",
                    (s, user_id, int(cycle))
                )
            except ValueError:
                conn.rollback(); cur.close(); conn.close()
                flash("Invalid appraisal score.", "danger")
                return redirect(url_for("admin.edit_user", user_id=user_id))

        conn.commit()
        audit_log(conn, session["user_id"], "EDIT_USER", f"target_user={user_id}")
        conn.commit()
        cur.close(); conn.close()
        flash("User updated successfully", "success")
        return redirect(url_for("admin.admin_panel"))

    # GET — load current data
    cur.execute(
        "SELECT joining_date, dept_id, designation, employee_id, phone, gender "
        "FROM tbl_users WHERE user_id=%s", (user_id,)
    )
    row       = cur.fetchone() or {}
    join_date = row.get("joining_date") or date.today()

    cur.execute(
        "SELECT cycle_number, appraisal_points FROM tbl_appraisal "
        "WHERE user_id=%s ORDER BY cycle_number ASC", (user_id,)
    )
    cycles = cur.fetchall()
    cd  = []
    cs2 = {}
    for c in cycles:
        cd.append({"cycle_number": c["cycle_number"],
                   "date": join_date + relativedelta(months=c["cycle_number"] * 6)})
        cs2[c["cycle_number"]] = c["appraisal_points"]

    cur.execute("SELECT id, dept_name, dept_code FROM tbl_departments WHERE is_active=1 ORDER BY dept_name")
    departments = cur.fetchall()
    cur.close(); conn.close()

    return render_template(
        "edit_user.html",
        user={**user, "cycle_dropdown": cd, "cycle_scores": cs2,
              "dept_id": row.get("dept_id"), "designation": row.get("designation"),
              "employee_id": row.get("employee_id"),
              "phone": row.get("phone"), "gender": row.get("gender")},
        departments=departments,
    )


@admin_bp.route("/admin/delete-user/<int:user_id>", methods=["POST"])
@admin_required
def delete_user(user_id):
    if user_id == session.get("user_id"):
        flash("You cannot delete your own account", "danger")
        return redirect(url_for("admin.admin_panel"))
    conn = get_db()
    cur  = conn.cursor()
    try:
        # Task 2.3: soft-delete — set is_active=0, do NOT hard-delete the user row
        cur.execute(
            "UPDATE tbl_users SET is_active=0 WHERE user_id=%s",
            (user_id,),
        )
        # Task 2.4: remove related records inside a single transaction;
        # add previously missing tbl_regularizations and tbl_appraisal deletes
        cur.execute("DELETE FROM tbl_attendance        WHERE user_id=%s", (user_id,))
        cur.execute("DELETE FROM tbl_daily_updates     WHERE user_id=%s", (user_id,))
        cur.execute("DELETE FROM tbl_leaves            WHERE user_id=%s", (user_id,))
        cur.execute("DELETE FROM tbl_self_assessment   WHERE user_id=%s", (user_id,))
        cur.execute("DELETE FROM tbl_regularizations   WHERE user_id=%s", (user_id,))  # was missing
        cur.execute("DELETE FROM tbl_appraisal         WHERE user_id=%s", (user_id,))  # was missing
        conn.commit()
        audit_log(conn, session["user_id"], "SOFT_DELETE_USER", f"deactivated_user_id={user_id}")
        conn.commit()
        flash("User deactivated successfully", "success")
    except Exception as exc:
        conn.rollback()
        logger.error("delete_user(%s) failed: %s", user_id, exc)
        flash("Failed to deactivate user. Please try again.", "danger")
    finally:
        cur.close(); conn.close()
    return redirect(url_for("admin.admin_panel"))