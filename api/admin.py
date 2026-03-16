"""
api/admin.py
Admin management routes:
  GET  /admin/panel
  GET/POST /admin/create-employee
  GET  /admin/view-user/<id>
  GET/POST /admin/edit-user/<id>
  POST /admin/delete-user/<id>
"""
import os
import re
import base64
import random
import string
import mysql.connector
from datetime import date
from datetime import datetime as _dt
from math import ceil

from dateutil.relativedelta import relativedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, session

from config import CFG
from extensions import get_db, logger, csrf
from schema.models import today_local, now_local
from werkzeug.security import generate_password_hash as _gen_hash
from services.utils import audit_log, login_required, admin_required, is_valid_name, is_valid_email, validate_password, api_ok, api_err
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

    cur.execute("""
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
        WHERE """ + where + """
        ORDER BY u.created_at DESC
        LIMIT %s OFFSET %s
    """, tuple(params + [PER_PAGE, offset]))
    users = cur.fetchall()

    cur.execute("SELECT COUNT(*) AS total FROM tbl_users u WHERE " + where, tuple(params) if params else ())
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
                (first, last, email, _gen_hash(pwd), joining_date, dept_id, designation, emp_id)
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

    # Task 6.7: appraisal history for this employee
    cur.execute("""
        SELECT ap.cycle_number, ap.appraisal_points, ap.calculated_at,
               sa.employee_rating, sa.admin_rating, sa.admin_comment,
               sa.status AS sa_status, sa.reviewed_at
        FROM tbl_appraisal ap
        LEFT JOIN tbl_self_assessment sa
               ON sa.user_id=ap.user_id AND sa.cycle_number=ap.cycle_number
        WHERE ap.user_id=%s
        ORDER BY ap.cycle_number DESC
    """, (user_id,))
    appraisal_history = cur.fetchall()
    cur.close(); conn.close()

    return render_template(
        "view_user.html",
        user=user, attendance=attendance,
        page=page, pages=pages, monthly=monthly,
        appraisal_history=appraisal_history,
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
              "employee_id": row.get("employee_id"), "joining_date": row.get("joining_date"),
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

# ── Task 7.4: Admin password reset ──────────────────────────────────

@admin_bp.route("/admin/reset-password/<int:user_id>", methods=["POST"])
@admin_required
def reset_password(user_id):
    """
    Generate a random 10-char temp password, hash it, save it,
    and flash it once to the admin.
    """
    # Generate temp password that satisfies all validate_password() rules:
    # 8+ chars, uppercase, lowercase, digit, special char, no spaces
    specials  = "!@#$%^&*()-_=+"
    alphabet  = string.ascii_letters + string.digits + specials
    while True:
        temp_pwd = (
            random.choice(string.ascii_uppercase) +
            random.choice(string.ascii_lowercase) +
            random.choice(string.digits) +
            random.choice(specials) +
            ''.join(random.choices(alphabet, k=6))
        )
        temp_list = list(temp_pwd)
        random.shuffle(temp_list)
        temp_pwd = ''.join(temp_list)
        # Verify it passes — regenerate if shuffle broke any rule (extremely rare)
        if not any(c == ' ' for c in temp_pwd):
            import re as _re
            if (_re.search(r'[A-Z]', temp_pwd) and _re.search(r'[a-z]', temp_pwd)
                    and _re.search(r'[0-9]', temp_pwd) and _re.search(r'[!@#$%^&*()\-_=+]', temp_pwd)):
                break
    hashed_pwd = _gen_hash(temp_pwd)

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT user_id, first_name, last_name FROM tbl_users WHERE user_id=%s AND is_active=1",
            (user_id,)
        )
        emp = cur.fetchone()
        if not emp:
            flash("Employee not found", "danger")
            return redirect(url_for("admin.admin_panel"))

        cur.execute(
            "UPDATE tbl_users SET password=%s WHERE user_id=%s",
            (hashed_pwd, user_id)
        )
        conn.commit()
        audit_log(conn, session["user_id"], "RESET_PASSWORD", f"target_user={user_id}")
        conn.commit()
        flash(
            f"Password reset for {emp['first_name']} {emp['last_name']}. "
            f"Temporary password: {temp_pwd}  — share this with the employee and ask them to change it.",
            "success"
        )
    except Exception:
        conn.rollback()
        logger.exception("Password reset failed for user %s", user_id)
        flash("Password reset failed", "danger")
    finally:
        cur.close(); conn.close()
    return redirect(url_for("admin.edit_user", user_id=user_id))


# ── Task 5.6 & 5.7: Admin face enrolment ────────────────────────────

_ADMIN_DIR_       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FACE_DATASET_DIR  = os.path.join(_ADMIN_DIR_, "face_utils", "face_dataset", "images")


@admin_bp.route("/admin/enrol-face/<int:user_id>", methods=["GET", "POST"])
@csrf.exempt
def enrol_face(user_id):
    """Tasks 5.6 & 5.7: GET renders webcam page, POST saves base64 image."""
    import traceback as _tb

    # inline auth
    if "user_id" not in session:
        if request.method == "POST" or request.is_json:
            return api_err("Authentication required", 401)
        flash("Please login first", "warning")
        return redirect(url_for("auth.login"))
    if session.get("role") != "admin":
        if request.method == "POST" or request.is_json:
            return api_err("Unauthorized", 403)
        flash("Unauthorized access", "danger")
        return redirect(url_for("auth.login"))

    try:
        if request.method == "POST":
            data    = request.get_json() or {}
            img_b64 = data.get("image", "")
            if not img_b64:
                return api_err("No image data received", 400)
            if "," in img_b64:
                img_b64 = img_b64.split(",", 1)[1]
            try:
                img_bytes = base64.b64decode(img_b64)
            except Exception:
                return api_err("Invalid base64 image data", 400)
            user_img_dir = os.path.join(FACE_DATASET_DIR, str(user_id))
            os.makedirs(user_img_dir, exist_ok=True)
            ts       = _dt.now().strftime("%Y%m%d_%H%M%S_%f")
            filename = f"enrol_{ts}.jpg"
            with open(os.path.join(user_img_dir, filename), "wb") as fh:
                fh.write(img_bytes)
            count = len([fn for fn in os.listdir(user_img_dir)
                         if fn.lower().endswith((".jpg",".jpeg",".png"))])
            try:
                ac = get_db()
                audit_log(ac, session["user_id"], "FACE_ENROL",
                          f"enrolled_for={user_id} file={filename}")
                ac.commit(); ac.close()
            except Exception:
                pass
            return api_ok({"enrolled_count": count, "filename": filename},
                          f"Saved. {count} image(s) enrolled.")

        # GET
        conn = get_db()
        cur  = conn.cursor(dictionary=True)
        cur.execute("SELECT user_id, first_name, last_name, employee_id "
                    "FROM tbl_users WHERE user_id=%s AND is_active=1", (user_id,))
        employee = cur.fetchone()
        cur.close(); conn.close()
        if not employee:
            flash("Employee not found", "danger")
            return redirect(url_for("admin.admin_panel"))
        user_img_dir = os.path.join(FACE_DATASET_DIR, str(user_id))
        enrolled_count = 0
        if os.path.isdir(user_img_dir):
            enrolled_count = len([fn for fn in os.listdir(user_img_dir)
                                   if fn.lower().endswith((".jpg",".jpeg",".png"))])
        return render_template("admin_enrol_face.html",
                               employee=employee,
                               enrolled_count=enrolled_count,
                               enrol_pct=min(enrolled_count * 10, 100))

    except Exception:
        detail = _tb.format_exc()
        logger.error("enrol_face failed user=%s — %s", user_id, detail.splitlines()[-1])
        from flask import current_app
        if current_app.debug:
            return "<pre style='color:red;padding:20px'>" + detail + "</pre>", 500
        return api_err("Server error: " + detail.splitlines()[-1], 500)