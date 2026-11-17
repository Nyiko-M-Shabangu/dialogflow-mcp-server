# database.py — PostgreSQL connection and query helpers
#
# Uses psycopg 3 with dict rows and a lazily created connection pool
# (psycopg_pool). Each helper borrows a connection from the pool; the pool
# commits on success and rolls back on error when the connection is returned.
# Use `transaction()` when several statements must succeed or fail together.

from __future__ import annotations

import atexit
import os
import threading
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any, cast

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

# Local development database from docker-compose.yml. Only the stdio servers
# fall back to it; the remote server refuses to start without DATABASE_URL
# (see remote_server._require_database_url).
LOCAL_DEV_DATABASE_URL = "postgresql://mcp:mcp_local_dev@127.0.0.1:5433/dialogflow_mcp"

_TRUTHY = ("1", "true", "yes", "on")


# ── Config ──────────────────────────────────────────────────
def _database_url() -> str:
    return os.environ.get("DATABASE_URL", "").strip() or LOCAL_DEV_DATABASE_URL


def _pool_min_size() -> int:
    try:
        return max(0, int(os.environ.get("DB_POOL_MIN", "0")))
    except ValueError:
        return 0


def _pool_max_size() -> int:
    try:
        return max(1, int(os.environ.get("DB_POOL_MAX", "5")))
    except ValueError:
        return 5


def _pool_timeout() -> float:
    """Seconds to wait for a pooled connection (psycopg_pool defaults to 30s,
    which would stall every detect_intent call while the DB is down)."""
    try:
        return max(0.5, float(os.environ.get("DB_POOL_TIMEOUT", "10")))
    except ValueError:
        return 10.0


def _pool_check_enabled() -> bool:
    """Whether the pool pings a connection each time it is handed out.

    Trade-off: the check costs one extra round trip per checkout, but serverless
    Postgres (Neon) and PaaS proxies close idle connections, and without it the
    first query after a pause fails on a dead connection instead of getting a
    fresh one. On a database close to the server with stable connections, set
    DB_POOL_CHECK=off to save the round trip."""
    return os.environ.get("DB_POOL_CHECK", "on").strip().lower() in _TRUTHY


# ── Connection pool ─────────────────────────────────────────
_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()


def get_pool() -> ConnectionPool:
    """Return the module-level connection pool, creating and opening it on first use.

    Settings are read from the environment when the pool is created."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                min_size = _pool_min_size()
                max_size = _pool_max_size()
                pool: ConnectionPool = ConnectionPool(
                    _database_url(),
                    min_size=min(min_size, max_size),
                    max_size=max_size,
                    timeout=_pool_timeout(),
                    max_idle=240,
                    max_lifetime=1800,
                    check=ConnectionPool.check_connection if _pool_check_enabled() else None,
                    kwargs={"row_factory": dict_row},
                    open=False,
                )
                pool.open()
                _pool = pool
    return _pool


def close_pool() -> None:
    """Close the connection pool (if it was created)."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None


atexit.register(close_pool)


# ── Connection helpers ──────────────────────────────────────
@contextmanager
def get_connection() -> Generator[psycopg.Connection[DictRow], None, None]:
    """Yield a pooled psycopg connection with dict rows.

    The pool commits on success and rolls back if the block raises."""
    with get_pool().connection() as conn:
        # The pool is created with row_factory=dict_row (see get_pool).
        yield cast("psycopg.Connection[DictRow]", conn)


@contextmanager
def transaction() -> Generator[psycopg.Cursor[DictRow], None, None]:
    """Yield a cursor bound to ONE connection and ONE transaction.

    Everything executed on the cursor is committed together when the block
    exits normally, or rolled back together if it raises."""
    with get_connection() as conn, conn.transaction(), conn.cursor() as cur:
        yield cur


# ── Query helpers ───────────────────────────────────────────
def fetch_all(query: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
    """Run a SELECT and return all rows as dicts."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def fetch_one(query: str, params: tuple[Any, ...] | None = None) -> dict[str, Any] | None:
    """Run a SELECT and return the first row, or None."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchone()


def execute(query: str, params: tuple[Any, ...] | None = None) -> int:
    """Run an INSERT/UPDATE/DELETE and return the row count."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.rowcount
