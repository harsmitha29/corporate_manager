"""
api/departments.py
Department management routes:
  GET  /admin/departments
  POST /admin/departments/add
  POST /admin/departments/<id>/edit
  POST /admin/departments/<id>/toggle
  POST /admin/departments/<id>/delete
  GET  /api/departments
"""
import mysql.connector
from flask import Blueprint, render_template, request, redirect, url_for, flash, session

from extensions import get_db, logger
from services.utils import audit_log, login_required, admin_required, api_ok

departments_bp = Blueprint("departments", __name__)


@departments_bp.route("/admin/departments")
@admin_required
def admin_departments():
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT d.id, d.dept_name, d.dept_code, d.description, d.is_active, d.created_at,
               COUNT(u.user_id) AS emp_count
        FROM tbl_departments d
        LEFT JOIN tbl_users u ON u.dept_id=d.id AND LOWER(u.role_type)='employee'
        GROUP BY d.id
        ORDER BY d.dept_name
    """)
    departments = cur.fetchall()
    cur.close(); conn.close()
    return render_template("admin_departments.html", departments=departments)


@departments_bp.route("/admin/departments/add", methods=["POST"])
@admin_required
def admin_department_add():
    name = request.form.get("dept_name",    "").strip()
    code = request.form.get("dept_code",    "").strip().upper()
    desc = request.form.get("description",  "").strip()

    if not name or not code:
        flash("Department name and code are required", "danger")
        return redirect(url_for("departments.admin_departments"))

    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO tbl_departments (dept_name, dept_code, description) VALUES (%s,%s,%s)",
            (name, code, desc)
        )
        conn.commit()
        audit_log(conn, session["user_id"], "CREATE_DEPT", f"{code}={name}")
        conn.commit()
        flash(f"Department '{name}' created successfully", "success")
    except mysql.connector.IntegrityError:
        conn.rollback()
        flash("Department name or code already exists", "danger")
    except Exception:
        conn.rollback()
        logger.exception("Create department failed")
        flash("Failed to create department", "danger")
    finally:
        cur.close(); conn.close()
    return redirect(url_for("departments.admin_departments"))


@departments_bp.route("/admin/departments/<int:dept_id>/edit", methods=["POST"])
@admin_required
def admin_department_edit(dept_id):
    name = request.form.get("dept_name",   "").strip()
    code = request.form.get("dept_code",   "").strip().upper()
    desc = request.form.get("description", "").strip()

    if not name or not code:
        flash("Department name and code are required", "danger")
        return redirect(url_for("departments.admin_departments"))

    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            "UPDATE tbl_departments SET dept_name=%s, dept_code=%s, description=%s WHERE id=%s",
            (name, code, desc, dept_id)
        )
        conn.commit()
        audit_log(conn, session["user_id"], "EDIT_DEPT", f"id={dept_id} {code}={name}")
        conn.commit()
        flash("Department updated successfully", "success")
    except mysql.connector.IntegrityError:
        conn.rollback()
        flash("Department name or code already exists", "danger")
    except Exception:
        conn.rollback()
        logger.exception("Edit department failed")
        flash("Failed to update department", "danger")
    finally:
        cur.close(); conn.close()
    return redirect(url_for("departments.admin_departments"))


@departments_bp.route("/admin/departments/<int:dept_id>/toggle", methods=["POST"])
@admin_required
def admin_department_toggle(dept_id):
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("UPDATE tbl_departments SET is_active = NOT is_active WHERE id=%s", (dept_id,))
        conn.commit()
        flash("Department status updated", "success")
    except Exception:
        conn.rollback()
        flash("Failed to update department status", "danger")
    finally:
        cur.close(); conn.close()
    return redirect(url_for("departments.admin_departments"))


@departments_bp.route("/admin/departments/<int:dept_id>/delete", methods=["POST"])
@admin_required
def admin_department_delete(dept_id):
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT COUNT(*) AS cnt FROM tbl_users WHERE dept_id=%s", (dept_id,))
        if cur.fetchone()["cnt"] > 0:
            flash("Cannot delete — employees are assigned to this department. "
                  "Reassign them first or deactivate instead.", "danger")
            return redirect(url_for("departments.admin_departments"))

        cur.execute("SELECT dept_name FROM tbl_departments WHERE id=%s", (dept_id,))
        row = cur.fetchone()
        cur.execute("DELETE FROM tbl_departments WHERE id=%s", (dept_id,))
        conn.commit()
        audit_log(conn, session["user_id"], "DELETE_DEPT", f"id={dept_id}")
        conn.commit()
        flash(f"Department '{row['dept_name']}' deleted", "success")
    except Exception:
        conn.rollback()
        logger.exception("Delete department failed")
        flash("Failed to delete department", "danger")
    finally:
        cur.close(); conn.close()
    return redirect(url_for("departments.admin_departments"))


@departments_bp.route("/api/departments")
@login_required
def api_departments():
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT id, dept_name, dept_code FROM tbl_departments WHERE is_active=1 ORDER BY dept_name")
    depts = cur.fetchall()
    cur.close(); conn.close()
    return api_ok({"departments": depts})
