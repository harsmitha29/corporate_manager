"""
services/holiday.py
Holiday engine: loads from company DB holidays only.
Provides is_weekend / is_working_day / get_holiday_name helpers.
"""
from calendar import monthcalendar
from datetime import date, timedelta, datetime as _dt

from extensions import get_db, logger
from schema.models import AttendanceStatus

# ── In-memory cache (date → holiday_name) ────────────────────────────
HOLIDAY_CACHE: dict = {}


def load_company_holidays_into_cache() -> None:
    """Load ALL holidays from tbl_company_holidays into memory.
    Called once at app startup from app.py.
    """
    try:
        conn = get_db()
        cur  = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT holiday_date, holiday_name FROM tbl_company_holidays "
            "ORDER BY holiday_date"
        )
        for row in cur.fetchall():
            d = row["holiday_date"]
            if hasattr(d, "date"):
                d = d.date()
            HOLIDAY_CACHE[d] = row["holiday_name"]
        cur.close(); conn.close()
        logger.info("[Holidays] %d holidays loaded from DB.", len(HOLIDAY_CACHE))
    except Exception as exc:
        logger.warning("[Holidays] Could not load company holidays from DB: %s", exc)


def reload_holiday_cache_from_db() -> None:
    """Called after every add/edit/delete on tbl_company_holidays.
    Clears and fully reloads the cache from DB.
    """
    HOLIDAY_CACHE.clear()
    load_company_holidays_into_cache()
    logger.info("[Holidays] Holiday cache refreshed from DB.")


# ── Public day-type helpers ──────────────────────────────────────────
def is_weekend(d: date) -> bool:
    return d.weekday() >= 5          # Saturday=5, Sunday=6


def is_calculated_holiday(d: date) -> bool:
    return d in HOLIDAY_CACHE


def is_non_working_day(d: date) -> bool:
    return is_weekend(d) or is_calculated_holiday(d)


def is_working_day(d: date) -> bool:
    return not is_non_working_day(d)


def get_holiday_name(d: date) -> str:
    if is_weekend(d):
        return "Weekend"
    return HOLIDAY_CACHE.get(d, "")


def get_day_status(d: date) -> str | None:
    if is_weekend(d):
        return AttendanceStatus.WEEKEND
    if is_calculated_holiday(d):
        return AttendanceStatus.HOLIDAY
    return None


def is_last_day_before_break(d: date) -> bool:
    return is_non_working_day(d + timedelta(days=1))


# ── Calendar date helpers ────────────────────────────────────────────
def get_nth_weekday_of_month(year: int, month: int, weekday: int, n: int):
    cal   = monthcalendar(year, month)
    count = 0
    for week in cal:
        if week[weekday] != 0:
            count += 1
            if count == n:
                return date(year, month, week[weekday])
    return None


def get_last_weekday_of_month(year: int, month: int, weekday: int):
    cal = monthcalendar(year, month)
    for week in reversed(cal):
        if week[weekday] != 0:
            return date(year, month, week[weekday])
    return None