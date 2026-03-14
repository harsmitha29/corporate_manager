import os
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# Load .env file if present (no-op if missing, env vars already set)
load_dotenv()


class Config:
    SECRET_KEY           = os.environ.get("SECRET_KEY", "CHANGE_ME_IN_PROD")
    SESSION_HOURS        = int(os.environ.get("SESSION_HOURS", 8))

    # Database — credentials come from .env, never hardcoded
    DB_HOST              = os.environ.get("DB_HOST",     "localhost")
    DB_USER              = os.environ.get("DB_USER",     "root")
    DB_PASSWORD          = os.environ.get("DB_PASSWORD", "")
    DB_NAME              = os.environ.get("DB_NAME",     "corporate_manager")
    DB_POOL_SIZE         = int(os.environ.get("DB_POOL_SIZE", 10))
    DB_POOL_NAME         = "corp_pool"

    # Timezone & work hours
    TIMEZONE             = ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Kolkata"))
    WORK_START_HOUR      = int(os.environ.get("WORK_START_HOUR",   9))
    WORK_START_MINUTE    = int(os.environ.get("WORK_START_MINUTE", 0))
    WORK_END_HOUR        = int(os.environ.get("WORK_END_HOUR",    18))
    WORK_END_MINUTE      = int(os.environ.get("WORK_END_MINUTE",   0))
    GRACE_MINUTES        = int(os.environ.get("GRACE_MINUTES",    15))
    MIN_WORK_HOURS       = float(os.environ.get("MIN_WORK_HOURS",  8.0))

    # Leave entitlements
    ANNUAL_LEAVE_DAYS    = int(os.environ.get("ANNUAL_LEAVE_DAYS", 12))
    SICK_LEAVE_DAYS      = int(os.environ.get("SICK_LEAVE_DAYS",    6))
    CASUAL_LEAVE_DAYS    = int(os.environ.get("CASUAL_LEAVE_DAYS",  6))

    # Scheduler — nightly job timing
    ABSENT_CUTOFF_HOUR   = 23
    ABSENT_CUTOFF_MINUTE = 59


CFG = Config()
