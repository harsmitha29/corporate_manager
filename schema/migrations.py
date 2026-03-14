"""
schema/migrations.py
All CREATE TABLE and ALTER TABLE statements.
Run once at startup via run_migrations().
"""
from extensions import get_db, logger

MIGRATION_SQL = [

    # ── Core tables ────────────────────────────────────────────────

    """CREATE TABLE IF NOT EXISTS tbl_appraisal (
        id               INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        user_id          INT NOT NULL,
        cycle_number     INT NOT NULL,
        months_completed INT NOT NULL DEFAULT 0,
        appraisal_points FLOAT NOT NULL DEFAULT 0.0,
        calculated_at    DATETIME NOT NULL DEFAULT NOW(),
        UNIQUE KEY uq_user_cycle (user_id, cycle_number),
        INDEX idx_user (user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",

    """CREATE TABLE IF NOT EXISTS tbl_audit_log (
        id         BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        user_id    INT NOT NULL,
        action     VARCHAR(100) NOT NULL,
        detail     VARCHAR(500),
        created_at DATETIME NOT NULL DEFAULT NOW(),
        INDEX idx_user    (user_id),
        INDEX idx_action  (action),
        INDEX idx_created (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",

    """CREATE TABLE IF NOT EXISTS tbl_leaves (
        id             INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        user_id        INT NOT NULL,
        leave_type     ENUM('Annual','Sick','Casual','Unpaid') NOT NULL,
        start_date     DATE NOT NULL,
        end_date       DATE NOT NULL,
        days_count     TINYINT UNSIGNED NOT NULL,
        reason         TEXT NOT NULL,
        status         ENUM('Pending','Approved','Rejected','Cancelled') NOT NULL DEFAULT 'Pending',
        admin_comments TEXT,
        applied_at     DATETIME NOT NULL DEFAULT NOW(),
        reviewed_at    DATETIME,
        INDEX idx_user_year (user_id, start_date),
        INDEX idx_status    (status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",

    """ALTER TABLE tbl_attendance
       MODIFY COLUMN status
         ENUM('Present','Completed','Weekend','Holiday','Absent','On Leave','Late','Half Day')
         NOT NULL DEFAULT 'Absent';""",

    """CREATE TABLE IF NOT EXISTS tbl_departments (
        id          INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        dept_name   VARCHAR(100) NOT NULL UNIQUE,
        dept_code   VARCHAR(20)  NOT NULL UNIQUE,
        description VARCHAR(300),
        is_active   TINYINT(1) NOT NULL DEFAULT 1,
        created_at  DATETIME NOT NULL DEFAULT NOW(),
        INDEX idx_active (is_active)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",

    # ── tbl_users extra columns ─────────────────────────────────────
    "ALTER TABLE tbl_users ADD COLUMN dept_id     INT UNSIGNED NULL;",
    "ALTER TABLE tbl_users ADD COLUMN designation VARCHAR(100) NULL;",
    "ALTER TABLE tbl_users ADD COLUMN employee_id VARCHAR(20)  NULL;",
    "ALTER TABLE tbl_users ADD COLUMN phone       VARCHAR(20)  NULL;",
    "ALTER TABLE tbl_users ADD COLUMN gender      ENUM('Male','Female','Other') NULL;",
    "ALTER TABLE tbl_users ADD COLUMN emergency_contact VARCHAR(100) NULL;",

    # ── Regularizations ─────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS tbl_regularizations (
        id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        user_id         INT NOT NULL,
        reg_date        DATE NOT NULL,
        requested_in    TIME NULL,
        requested_out   TIME NULL,
        reason          TEXT NOT NULL,
        status          ENUM('Pending','Approved','Rejected') NOT NULL DEFAULT 'Pending',
        admin_comment   TEXT,
        reviewed_by     INT NULL,
        applied_at      DATETIME NOT NULL DEFAULT NOW(),
        reviewed_at     DATETIME NULL,
        INDEX idx_user   (user_id),
        INDEX idx_status (status),
        INDEX idx_date   (reg_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",

    # ── tbl_attendance extra columns ────────────────────────────────
    "ALTER TABLE tbl_attendance ADD COLUMN work_type     ENUM('office','wfh','field') NOT NULL DEFAULT 'office';",
    "ALTER TABLE tbl_attendance ADD COLUMN overtime_hours FLOAT NOT NULL DEFAULT 0.0;",

    # ── Daily updates ────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS tbl_daily_updates (
        update_id     INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        user_id       INT NOT NULL,
        update_date   DATE NOT NULL,
        project_title VARCHAR(200) NOT NULL,
        work_summary  TEXT NOT NULL,
        remarks       TEXT,
        updated_at    DATETIME NOT NULL DEFAULT NOW() ON UPDATE NOW(),
        UNIQUE KEY uq_user_date (user_id, update_date),
        INDEX idx_user (user_id),
        INDEX idx_date (update_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",

    # ── Self-assessment ──────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS tbl_self_assessment (
        id               INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        user_id          INT NOT NULL,
        cycle_number     INT NOT NULL,
        employee_rating  FLOAT NOT NULL DEFAULT 0.0,
        employee_comment TEXT,
        admin_rating     FLOAT NULL,
        admin_comment    TEXT,
        status           ENUM('Pending','Approved') NOT NULL DEFAULT 'Pending',
        created_at       DATETIME NOT NULL DEFAULT NOW(),
        reviewed_at      DATETIME NULL,
        UNIQUE KEY uq_user_cycle (user_id, cycle_number),
        INDEX idx_user   (user_id),
        INDEX idx_status (status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",

    """CREATE TABLE IF NOT EXISTS tbl_appraisal_review (
        id                INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        assessment_id     INT UNSIGNED NOT NULL,
        user_id           INT NOT NULL,
        reviewed_by       INT NOT NULL,
        q_job_knowledge   TINYINT UNSIGNED,
        q_quality_of_work TINYINT UNSIGNED,
        q_productivity    TINYINT UNSIGNED,
        q_teamwork        TINYINT UNSIGNED,
        q_communication   TINYINT UNSIGNED,
        q_initiative      TINYINT UNSIGNED,
        q_punctuality     TINYINT UNSIGNED,
        q_learning        TINYINT UNSIGNED,
        manager_comments  TEXT,
        final_score       FLOAT NOT NULL DEFAULT 0.0,
        reviewed_at       DATETIME NOT NULL DEFAULT NOW(),
        UNIQUE KEY uq_assessment (assessment_id),
        INDEX idx_user     (user_id),
        INDEX idx_reviewer (reviewed_by)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",

    # ── Security: unique attendance record per user per date (Task 1.6) ──
    """ALTER TABLE tbl_attendance
       ADD CONSTRAINT uq_user_date UNIQUE (user_id, attendance_date);""",

    # ── Task 2.3: Soft-delete support — is_active column on tbl_users ──
    """ALTER TABLE tbl_users
       ADD COLUMN is_active TINYINT(1) NOT NULL DEFAULT 1
       AFTER employee_id;""",

    # Back-fill existing rows so nothing breaks after the column is added
    """UPDATE tbl_users SET is_active=1 WHERE is_active IS NULL;""",

    # ── Task 2.6: Performance indexes ─────────────────────────────────
    """ALTER TABLE tbl_attendance
       ADD INDEX idx_att_date (attendance_date);""",

    """ALTER TABLE tbl_leaves
       ADD INDEX idx_leaves_status (status);""",

    """ALTER TABLE tbl_regularizations
       ADD INDEX idx_reg_status (status);""",

    # ── Company holidays ─────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS tbl_company_holidays (
        id           INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        holiday_date DATE NOT NULL UNIQUE,
        holiday_name VARCHAR(150) NOT NULL,
        holiday_type ENUM('National','Regional','Company','Optional') NOT NULL DEFAULT 'Company',
        description  VARCHAR(300),
        created_by   INT NOT NULL,
        created_at   DATETIME NOT NULL DEFAULT NOW(),
        updated_at   DATETIME NOT NULL DEFAULT NOW() ON UPDATE NOW(),
        INDEX idx_date (holiday_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",
]


def run_migrations() -> None:
    """Execute every statement in MIGRATION_SQL, skipping ones that already ran."""
    conn = get_db()
    cur  = conn.cursor()
    for sql in MIGRATION_SQL:
        try:
            cur.execute(sql)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.warning("Migration skipped (already applied?): %s", exc)
    cur.close()
    conn.close()
    logger.info("Migrations complete.")