"""
schema/models.py
Domain constants — attendance statuses, leave types, leave statuses.
Import these everywhere instead of using raw strings.
"""
from datetime import datetime, date
from zoneinfo import ZoneInfo
from config import CFG


# ── Attendance ───────────────────────────────────────────────────────
class AttendanceStatus:
    PRESENT   = "Present"
    COMPLETED = "Completed"
    WEEKEND   = "Weekend"
    HOLIDAY   = "Holiday"
    ABSENT    = "Absent"
    ON_LEAVE  = "On Leave"
    LATE      = "Late"
    HALF_DAY  = "Half Day"


# ── Leaves ───────────────────────────────────────────────────────────
class LeaveType:
    ANNUAL  = "Annual"
    SICK    = "Sick"
    CASUAL  = "Casual"
    UNPAID  = "Unpaid"
    ALL     = ["Annual", "Sick", "Casual", "Unpaid"]


class LeaveStatus:
    PENDING   = "Pending"
    APPROVED  = "Approved"
    REJECTED  = "Rejected"
    CANCELLED = "Cancelled"


# ── Time helpers ─────────────────────────────────────────────────────
def now_local() -> datetime:
    return datetime.now(tz=CFG.TIMEZONE)


def today_local() -> date:
    return now_local().date()
