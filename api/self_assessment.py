"""
api/self_assessment.py
Self-assessment routes:
  GET/POST /self-assessment
  GET      /admin/self-assessments
  POST     /admin/self-assessments/<id>/review
"""
from datetime import datetime
from datetime import datetime as _dt

from dateutil.relativedelta import relativedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, session

from extensions import get_db, logger
from services.utils import audit_log, login_required, admin_required
from services.appraisal import (
    ensure_appraisal_cycles, calculate_appraisal_score, _get_current_cycle,
)

self_assessment_bp = Blueprint("self_assessment", __name__)


@self_assessment_bp.route("/self-assessment", methods=["GET", "POST"])
@login_required
def self_assessment():
    user_id = session["user_id"]

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    ensure_appraisal_cycles(user_id, cur, conn)
    cur.close(); conn.close()

    current_cycle = _get_current_cycle(user_id)

    if request.method == "POST":
        cycle_number     = request.form.get("cycle_number", type=int)
        employee_rating  = request.form.get("employee_rating", type=float)
        employee_comment = request.form.get("employee_comment", "").strip()

        if not cycle_number or not employee_rating:
            flash("Cycle and rating are required", "danger")
            return redirect(url_for("self_assessment.self_assessment"))
        if not (1.0 <= employee_rating <= 5.0):
            flash("Rating must be between 1 and 5", "danger")
            return redirect(url_for("self_assessment.self_assessment"))

        conn = get_db()
        cur  = conn.cursor(dictionary=True)
        try:
            cur.execute(
                "SELECT id, status FROM tbl_self_assessment "
                "WHERE user_id=%s AND cycle_number=%s",
                (user_id, cycle_number)
            )
            existing = cur.fetchone()
            if existing:
                if existing["status"] == "Approved":
                    flash("This cycle's assessment has already been approved by admin.", "warning")
                    return redirect(url_for("self_assessment.self_assessment"))
                cur.execute(
                    "UPDATE tbl_self_assessment "
                    "SET employee_rating=%s, employee_comment=%s, status='Pending' WHERE id=%s",
                    (employee_rating, employee_comment, existing["id"])
                )
                flash("Self-assessment updated successfully", "success")
            else:
                cur.execute(
                    "INSERT INTO tbl_self_assessment "
                    "(user_id, cycle_number, employee_rating, employee_comment, status) "
                    "VALUES (%s,%s,%s,%s,'Pending')",
                    (user_id, cycle_number, employee_rating, employee_comment)
                )
                flash("Self-assessment submitted successfully", "success")

            conn.commit()
            audit_log(conn, user_id, "SELF_ASSESSMENT_SUBMIT",
                      f"cycle={cycle_number} rating={employee_rating}")
            conn.commit()
        except Exception:
            conn.rollback()
            logger.exception("Self-assessment submit failed")
            flash("Failed to submit assessment", "danger")
        finally:
            cur.close(); conn.close()
        return redirect(url_for("self_assessment.self_assessment"))

    # GET
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT sa.*, ap.appraisal_points
        FROM tbl_self_assessment sa
        LEFT JOIN tbl_appraisal ap ON ap.user_id=sa.user_id AND ap.cycle_number=sa.cycle_number
        WHERE sa.user_id=%s
        ORDER BY sa.cycle_number DESC
    """, (user_id,))
    assessments = cur.fetchall()

    cur.execute(
        "SELECT cycle_number FROM tbl_appraisal WHERE user_id=%s ORDER BY cycle_number ASC",
        (user_id,)
    )
    available_cycles = [r["cycle_number"] for r in cur.fetchall()]
    submitted_cycles = {a["cycle_number"] for a in assessments}
    cur.close(); conn.close()

    return render_template(
        "self_assessment.html",
        assessments=assessments,
        available_cycles=available_cycles,
        submitted_cycles=submitted_cycles,
        current_cycle=current_cycle,
    )


@self_assessment_bp.route("/admin/self-assessments")
@admin_required
def admin_self_assessments():
    status_filter = request.args.get("status", "Pending")
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT sa.*, u.first_name, u.last_name, u.email, u.employee_id,
               ap.appraisal_points
        FROM tbl_self_assessment sa
        JOIN tbl_users u ON u.user_id = sa.user_id
        LEFT JOIN tbl_appraisal ap ON ap.user_id=sa.user_id AND ap.cycle_number=sa.cycle_number
        WHERE sa.status=%s
        ORDER BY sa.created_at DESC
    """, (status_filter,))
    assessments = cur.fetchall()
    cur.close(); conn.close()
    return render_template(
        "admin_self_assessments.html",
        assessments=assessments,
        status_filter=status_filter,
        statuses=["Pending", "Approved"],
    )


@self_assessment_bp.route("/admin/self-assessments/<int:sa_id>/review", methods=["POST"])
@admin_required
def admin_review_assessment(sa_id):
    admin_rating  = request.form.get("admin_rating", type=float)
    admin_comment = request.form.get("admin_comment", "").strip()

    if admin_rating is None or not (0.0 <= admin_rating <= 5.0):
        flash("Admin rating must be between 0 and 5", "danger")
        return redirect(url_for("self_assessment.admin_self_assessments"))

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM tbl_self_assessment WHERE id=%s", (sa_id,))
        sa = cur.fetchone()
        if not sa:
            flash("Assessment not found", "danger")
            return redirect(url_for("self_assessment.admin_self_assessments"))

        cur.execute(
            "UPDATE tbl_self_assessment "
            "SET admin_rating=%s, admin_comment=%s, status='Approved', reviewed_at=NOW() WHERE id=%s",
            (admin_rating, admin_comment, sa_id)
        )

        uid          = sa["user_id"]
        cycle_number = sa["cycle_number"]

        cur.execute("SELECT joining_date FROM tbl_users WHERE user_id=%s", (uid,))
        u_row = cur.fetchone()
        if u_row and u_row.get("joining_date"):
            jd = u_row["joining_date"]
            if isinstance(jd, datetime):
                jd = jd.date()
            elif isinstance(jd, str):
                jd = _dt.strptime(jd[:10], "%Y-%m-%d").date()
            cs        = jd + relativedelta(months=(cycle_number - 1) * 6)
            ce        = jd + relativedelta(months=cycle_number * 6)
            new_score = calculate_appraisal_score(uid, cs, ce, cycle_number)
            cur.execute(
                "UPDATE tbl_appraisal SET appraisal_points=%s, calculated_at=NOW() "
                "WHERE user_id=%s AND cycle_number=%s",
                (new_score, uid, cycle_number)
            )

        conn.commit()
        audit_log(conn, session["user_id"], "REVIEW_ASSESSMENT",
                  f"sa_id={sa_id} admin_rating={admin_rating}")
        conn.commit()
        flash("Assessment reviewed and appraisal score updated", "success")
    except Exception:
        conn.rollback()
        logger.exception("Admin assessment review failed")
        flash("Review failed", "danger")
    finally:
        cur.close(); conn.close()
    return redirect(url_for("self_assessment.admin_self_assessments"))
