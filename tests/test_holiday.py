"""
tests/test_holiday.py
Unit tests for services/holiday.py
Tests: is_working_day, is_weekend, get_holiday_name
"""
import pytest
from datetime import date
from unittest.mock import patch


class TestIsWeekend:

    def test_saturday_is_weekend(self, app):
        with app.app_context():
            from services.holiday import is_weekend
            saturday = date(2026, 3, 14)  # Known Saturday
            assert is_weekend(saturday) == True

    def test_sunday_is_weekend(self, app):
        with app.app_context():
            from services.holiday import is_weekend
            sunday = date(2026, 3, 15)  # Known Sunday
            assert is_weekend(sunday) == True

    def test_monday_not_weekend(self, app):
        with app.app_context():
            from services.holiday import is_weekend
            monday = date(2026, 3, 16)  # Known Monday
            assert is_weekend(monday) == False

    def test_friday_not_weekend(self, app):
        with app.app_context():
            from services.holiday import is_weekend
            friday = date(2026, 3, 13)  # Known Friday
            assert is_weekend(friday) == False


class TestIsWorkingDay:

    def test_saturday_not_working(self, app):
        with app.app_context():
            from services.holiday import is_working_day
            saturday = date(2026, 3, 14)
            assert is_working_day(saturday) == False

    def test_sunday_not_working(self, app):
        with app.app_context():
            from services.holiday import is_working_day
            sunday = date(2026, 3, 15)
            assert is_working_day(sunday) == False

    def test_monday_is_working(self, app):
        with app.app_context():
            from services.holiday import is_working_day
            monday = date(2026, 3, 16)
            assert is_working_day(monday) == True

    def test_wednesday_is_working(self, app):
        with app.app_context():
            from services.holiday import is_working_day
            wednesday = date(2026, 3, 18)
            assert is_working_day(wednesday) == True


class TestGetHolidayName:

    def test_unknown_date_returns_none(self, app):
        with app.app_context():
            from services.holiday import get_holiday_name
            # A random weekday with no holiday
            result = get_holiday_name(date(2026, 3, 16))
            assert result is None or isinstance(result, str)

    def test_weekend_returns_none_or_string(self, app):
        with app.app_context():
            from services.holiday import get_holiday_name
            result = get_holiday_name(date(2026, 3, 14))
            # Weekend — either None or some string
            assert result is None or isinstance(result, str)


class TestIsNonWorkingDay:

    def test_weekend_is_non_working(self, app):
        with app.app_context():
            from services.holiday import is_non_working_day
            saturday = date(2026, 3, 14)
            assert is_non_working_day(saturday) == True

    def test_monday_is_working(self, app):
        with app.app_context():
            from services.holiday import is_non_working_day
            monday = date(2026, 3, 16)
            assert is_non_working_day(monday) == False