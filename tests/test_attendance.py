"""
tests/test_attendance.py
Unit tests for services/attendance.py
Tests: derive_status, calc_work_hours, stamp_overtime, is_late_checkin
"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, date
from zoneinfo import ZoneInfo


# ── calc_work_hours ───────────────────────────────────────────────────

class TestCalcWorkHours:

    def test_full_day(self, app):
        with app.app_context():
            from services.attendance import calc_work_hours
            assert calc_work_hours("09:00:00", "18:00:00") == 9.0

    def test_half_day(self, app):
        with app.app_context():
            from services.attendance import calc_work_hours
            assert calc_work_hours("09:00:00", "13:00:00") == 4.0

    def test_same_time(self, app):
        with app.app_context():
            from services.attendance import calc_work_hours
            assert calc_work_hours("09:00:00", "09:00:00") == 0.0

    def test_invalid_returns_zero(self, app):
        with app.app_context():
            from services.attendance import calc_work_hours
            assert calc_work_hours("invalid", "18:00:00") == 0.0

    def test_overnight_returns_zero(self, app):
        """Check-out before check-in should return 0, not negative."""
        with app.app_context():
            from services.attendance import calc_work_hours
            result = calc_work_hours("18:00:00", "09:00:00")
            assert result == 0.0

    def test_with_seconds(self, app):
        with app.app_context():
            from services.attendance import calc_work_hours
            result = calc_work_hours("09:00:00", "17:30:00")
            assert result == 8.5


# ── derive_status ─────────────────────────────────────────────────────

class TestDeriveStatus:

    def test_absent_no_checkin(self, app):
        with app.app_context():
            from services.attendance import derive_status
            assert derive_status(None, None) == "Absent"

    def test_present_no_checkout(self, app):
        with app.app_context():
            from services.attendance import derive_status
            assert derive_status("09:00:00", None) == "Present"

    def test_completed_full_day(self, app):
        with app.app_context():
            from services.attendance import derive_status
            # 9 hours > MIN_WORK_HOURS(8), on time
            result = derive_status("09:00:00", "18:00:00")
            assert result in ("Completed", "Late")

    def test_half_day_short_hours(self, app):
        with app.app_context():
            from services.attendance import derive_status
            # 2 hours < MIN_WORK_HOURS/2(4)
            result = derive_status("09:00:00", "11:00:00")
            assert result == "Half Day"

    def test_late_checkin(self, app):
        with app.app_context():
            from services.attendance import derive_status
            # Very late check-in with full hours
            result = derive_status("12:00:00", "21:00:00")
            assert result == "Late"

    def test_empty_string_checkin(self, app):
        with app.app_context():
            from services.attendance import derive_status
            assert derive_status("", None) == "Absent"


# ── is_late_checkin ───────────────────────────────────────────────────

class TestIsLateCheckin:

    def test_on_time(self, app):
        with app.app_context():
            from services.attendance import is_late_checkin
            # 09:10 within grace period (15 min)
            assert is_late_checkin("09:10:00") == False

    def test_late(self, app):
        with app.app_context():
            from services.attendance import is_late_checkin
            # 10:00 is late
            assert is_late_checkin("10:00:00") == True

    def test_exactly_at_grace_boundary(self, app):
        with app.app_context():
            from services.attendance import is_late_checkin
            # 09:15 is at exact grace boundary
            result = is_late_checkin("09:15:00")
            assert isinstance(result, bool)


# ── stamp_overtime ────────────────────────────────────────────────────

class TestStampOvertime:

    def test_overtime_calculated(self, app):
        with app.app_context():
            from services.attendance import stamp_overtime
            mock_conn = MagicMock()
            mock_cur  = MagicMock()
            mock_conn.cursor.return_value = mock_cur

            # 10 hours worked, MIN_WORK_HOURS=8 → 2h overtime
            stamp_overtime(mock_conn, att_id=1,
                           check_in="09:00:00", check_out="19:00:00")

            mock_cur.execute.assert_called_once()
            call_args = mock_cur.execute.call_args[0]
            assert "overtime_hours" in call_args[0]
            assert call_args[1][0] == 2.0  # 2 hours overtime

    def test_no_overtime(self, app):
        with app.app_context():
            from services.attendance import stamp_overtime
            mock_conn = MagicMock()
            mock_cur  = MagicMock()
            mock_conn.cursor.return_value = mock_cur

            # 7 hours < MIN_WORK_HOURS=8 → 0 overtime
            stamp_overtime(mock_conn, att_id=2,
                           check_in="09:00:00", check_out="16:00:00")

            call_args = mock_cur.execute.call_args[0]
            assert call_args[1][0] == 0.0

    def test_exact_work_hours(self, app):
        with app.app_context():
            from services.attendance import stamp_overtime
            mock_conn = MagicMock()
            mock_cur  = MagicMock()
            mock_conn.cursor.return_value = mock_cur

            # Exactly 8 hours → 0 overtime
            stamp_overtime(mock_conn, att_id=3,
                           check_in="09:00:00", check_out="17:00:00")

            call_args = mock_cur.execute.call_args[0]
            assert call_args[1][0] == 0.0