"""
api/__init__.py
Registers every Blueprint onto the Flask app.
Call register_blueprints(app) from app.py.

NOTE (Task 2.8): api/self_assessment.py and api/daily_updates.py are kept
dormant — their blueprint imports are commented out until Day 6 rewrites
self_assessment cleanly.
"""
from api.auth            import auth_bp
from api.admin           import admin_bp
from api.attendance      import attendance_bp
from api.leaves          import leaves_bp
# from api.daily_updates   import daily_updates_bp   # dormant — unregistered Day 2
# from api.self_assessment import self_assessment_bp  # dormant — re-registered Day 6
from api.regularization  import regularization_bp
from api.departments     import departments_bp
from api.holidays        import holidays_bp
from api.profile         import profile_bp
from api.reports         import reports_bp


def register_blueprints(app) -> None:
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(attendance_bp)
    app.register_blueprint(leaves_bp)
    # daily_updates_bp — dormant until further notice
    # self_assessment_bp — re-registered on Day 6
    app.register_blueprint(regularization_bp)
    app.register_blueprint(departments_bp)
    app.register_blueprint(holidays_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(reports_bp)