"""
tests/test_checkin.py
Integration tests for the check-in flow.
POST /check_in with mocked face recognition.
"""
import pytest
import json
from unittest.mock import patch
from datetime import date


class TestCheckInFlow:

    def test_checkin_without_auth_redirects(self, client):
        """Unauthenticated check-in should redirect to login."""
        resp = client.post("/check_in",
                           data=json.dumps({"image": "data:image/jpeg;base64,abc"}),
                           content_type="application/json")
        assert resp.status_code in (302, 401)

    def test_checkin_on_weekend_returns_holiday(self, app, employee_session):
        """Check-in on a weekend should return holiday status."""
        client, user_id = employee_session
        with patch("api.attendance.today_local") as mock_today, \
             patch("api.attendance.is_non_working_day", return_value=True), \
             patch("api.attendance.get_holiday_name", return_value="Weekend"):

            mock_today.return_value = date(2026, 3, 15)  # Sunday

            resp = client.post("/check_in",
                               data=json.dumps({"image": "data:image/jpeg;base64,abc"}),
                               content_type="application/json")
            data = json.loads(resp.data)
            assert data.get("status") == "holiday"

    def test_checkin_face_disabled_marks_present(self, app, employee_session):
        """With face recognition disabled, check-in marks attendance directly."""
        client, user_id = employee_session

        with patch("api.attendance.is_non_working_day", return_value=False), \
             patch("api.attendance.is_working_day", return_value=True), \
             patch("api.attendance.today_local") as mock_today, \
             patch("api.attendance.now_local") as mock_now, \
             patch("api.attendance.CFG") as mock_cfg:

            from datetime import datetime
            from zoneinfo import ZoneInfo
            tz = ZoneInfo("Asia/Kolkata")
            mock_today.return_value = date(2026, 3, 16)
            mock_now.return_value   = datetime(2026, 3, 16, 9, 0, 0, tzinfo=tz)
            mock_cfg.FACE_RECOGNITION_ENABLED = False
            mock_cfg.ABSENT_CUTOFF_HOUR   = 11
            mock_cfg.ABSENT_CUTOFF_MINUTE = 0
            mock_cfg.GRACE_MINUTES        = 15
            mock_cfg.WORK_START_HOUR      = 9
            mock_cfg.WORK_START_MINUTE    = 0
            mock_cfg.TIMEZONE             = tz

            resp = client.post("/check_in",
                               data=json.dumps({"image": "data:image/jpeg;base64,/9j/test"}),
                               content_type="application/json")
            data = json.loads(resp.data)
            # Should succeed or return already checked in
            assert data.get("status") in ("success", "already_checked_in",
                                          "pending_checkout", "auto_checkin", "holiday")

    def test_face_auth_page_checkin(self, client, employee_session):
        """face-auth/checkin page renders correctly."""
        client, _ = employee_session
        resp = client.get("/face-auth/checkin")
        assert resp.status_code == 200
        assert b"Face" in resp.data or b"Check" in resp.data

    def test_face_auth_page_wfh(self, client, employee_session):
        """face-auth/wfh page renders correctly (Day 8 feature)."""
        client, _ = employee_session
        resp = client.get("/face-auth/wfh")
        assert resp.status_code == 200

    def test_face_auth_invalid_action(self, client, employee_session):
        """Invalid action redirects to dashboard."""
        client, _ = employee_session
        resp = client.get("/face-auth/invalid_action")
        assert resp.status_code == 302

    def test_get_attendance_status_authenticated(self, client, employee_session):
        """get-attendance-status returns JSON with status field."""
        client, _ = employee_session
        resp = client.get("/get-attendance-status")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "status" in data


class TestWFHFlow:

    def test_wfh_on_weekend_rejected(self, app, employee_session):
        """WFH on weekend should be rejected."""
        client, user_id = employee_session

        with patch("api.attendance.is_non_working_day", return_value=True), \
             patch("api.attendance.today_local") as mock_today:

            mock_today.return_value = date(2026, 3, 15)  # Sunday

            resp = client.post("/attendance/wfh",
                               data=json.dumps({}),
                               content_type="application/json")
            data = json.loads(resp.data)
            assert data.get("ok") == False

    def test_wfh_without_auth_redirects(self, client):
        """Unauthenticated WFH should redirect."""
        resp = client.post("/attendance/wfh",
                           data=json.dumps({}),
                           content_type="application/json")
        assert resp.status_code in (302, 401)