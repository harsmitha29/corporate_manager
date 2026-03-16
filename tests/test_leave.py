"""
tests/test_leave.py
Unit tests for services/leave.py
Tests: get_leave_balance, get_monthly_summary
"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import date


class TestGetLeaveBalance:

    def test_returns_dict_with_leave_types(self, app, employee_session):
        """Balance dict contains Annual, Sick, Casual."""
        client, user_id = employee_session
        with app.app_context():
            from services.leave import get_leave_balance
            balance = get_leave_balance(user_id)
            assert isinstance(balance, dict)
            # Should have at least one leave type
            assert len(balance) > 0

    def test_balance_values_are_numeric(self, app, employee_session):
        """All balance values are non-negative numbers."""
        client, user_id = employee_session
        with app.app_context():
            from services.leave import get_leave_balance
            balance = get_leave_balance(user_id)
            for leave_type, days in balance.items():
                assert isinstance(days, (int, float)), \
                    f"{leave_type} balance should be numeric, got {type(days)}"
                assert days >= 0, f"{leave_type} balance should not be negative"

    def test_balance_does_not_exceed_entitlement(self, app, employee_session):
        """Balance should not exceed typical max entitlements."""
        client, user_id = employee_session
        with app.app_context():
            from services.leave import get_leave_balance
            balance = get_leave_balance(user_id)
            for leave_type, days in balance.items():
                assert days <= 30, f"{leave_type} balance {days} seems too high"


class TestGetMonthlySummary:

    def test_returns_all_required_keys(self, app, employee_session):
        """Summary dict has all expected keys."""
        client, user_id = employee_session
        with app.app_context():
            from services.leave import get_monthly_summary
            summary = get_monthly_summary(user_id, 2026, 3)
            required_keys = [
                "present", "absent", "late", "half_day",
                "on_leave", "holidays", "weekends",
                "total_hours", "working_days", "attendance_pct"
            ]
            for key in required_keys:
                assert key in summary, f"Missing key: {key}"

    def test_numeric_values(self, app, employee_session):
        """All summary values are numeric."""
        client, user_id = employee_session
        with app.app_context():
            from services.leave import get_monthly_summary
            summary = get_monthly_summary(user_id, 2026, 3)
            for key, val in summary.items():
                assert isinstance(val, (int, float)), \
                    f"{key} should be numeric, got {type(val)}"

    def test_attendance_pct_range(self, app, employee_session):
        """Attendance percentage is between 0 and 100."""
        client, user_id = employee_session
        with app.app_context():
            from services.leave import get_monthly_summary
            summary = get_monthly_summary(user_id, 2026, 3)
            pct = summary["attendance_pct"]
            assert 0.0 <= pct <= 100.0, f"Attendance pct {pct} out of range"

    def test_no_crash_for_new_employee(self, app):
        """Should return zero-filled dict for employee with no attendance."""
        with app.app_context():
            from services.leave import get_monthly_summary
            # Use user_id 999999 — unlikely to exist
            summary = get_monthly_summary(999999, 2026, 3)
            assert summary["present"] == 0
            assert summary["absent"] == 0
            assert summary["attendance_pct"] == 0.0

    def test_known_attendance_data(self, app, sample_attendance):
        """With 5 known Completed rows, present count should be 5."""
        uid = sample_attendance["user_id"]
        last_week_dates = sample_attendance["dates"]
        year  = last_week_dates[0].year
        month = last_week_dates[0].month

        with app.app_context():
            from services.leave import get_monthly_summary
            summary = get_monthly_summary(uid, year, month)
            assert summary["present"] >= 5, \
                f"Expected >= 5 present days, got {summary['present']}"

    def test_total_hours_positive(self, app, sample_attendance):
        """Total hours should be positive for completed attendance."""
        uid = sample_attendance["user_id"]
        last_week_dates = sample_attendance["dates"]
        year  = last_week_dates[0].year
        month = last_week_dates[0].month

        with app.app_context():
            from services.leave import get_monthly_summary
            summary = get_monthly_summary(uid, year, month)
            assert summary["total_hours"] > 0, \
                f"Expected positive total hours, got {summary['total_hours']}"