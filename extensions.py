"""
extensions.py
Shared infrastructure: logger, database pool, CSRF protection, rate limiter.
Import from here everywhere instead of re-creating these objects.
"""
import logging
import threading
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import mysql.connector
from mysql.connector import pooling
from mysql.connector.pooling import MySQLConnectionPool

from config import CFG

# ── Logger ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("corporate_manager")


# ── Database connection pool ─────────────────────────────────────────
_pool: MySQLConnectionPool | None = None
_pool_lock = threading.Lock()


def _create_pool() -> MySQLConnectionPool:
    return pooling.MySQLConnectionPool(
        pool_name        = CFG.DB_POOL_NAME,
        pool_size        = CFG.DB_POOL_SIZE,
        pool_reset_session = True,
        host             = CFG.DB_HOST,
        user             = CFG.DB_USER,
        password         = CFG.DB_PASSWORD,
        database         = CFG.DB_NAME,
        autocommit       = False,
        connection_timeout = 10,
        charset          = "utf8mb4",
        collation        = "utf8mb4_unicode_ci",
    )


def get_db():
    """Return a connection from the pool (caller must close it)."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = _create_pool()
                logger.info("DB pool created (size=%d)", CFG.DB_POOL_SIZE)
    return _pool.get_connection()


# ── CSRF Protection (Flask-WTF) ──────────────────────────────────────
csrf = CSRFProtect()


# ── Rate Limiter (Flask-Limiter) ─────────────────────────────────────
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],          # No global limit; apply per-route
    storage_uri="memory://",
)
