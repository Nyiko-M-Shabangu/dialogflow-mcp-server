"""Refuse a TEST_DATABASE_URL that could point at a real database.

The DB tests DROP and recreate the `public` schema, so the target must be a
throwaway local database whose name ends in `_test`. This module is pure (no
I/O) so it can be unit-tested; reasons never echo any part of either URL or
any environment value.
"""

from __future__ import annotations

from collections.abc import Mapping

from psycopg.conninfo import conninfo_to_dict

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_COMPOSE_PORT = 5433
_DEFAULT_PORT = 5432


def _norm_host(host: str) -> str:
    host = host.strip().lower()
    return "localhost" if host in _LOCAL_HOSTS else host


def _parse(url: str) -> dict | None:
    try:
        return conninfo_to_dict(url)
    except Exception:  # any parse failure means "unsafe"
        return None


def _port_int(value: object) -> int | None:
    """libpq reads the port as an integer: ' 5433' and '05433' both mean 5433."""
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _identity(info: dict) -> tuple[str, int | None, str]:
    # Fill libpq defaults so a portless or hostless URL still compares equal.
    host = _norm_host(str(info.get("host") or "")) or "localhost"
    raw_port = str(info.get("port") or "").strip()
    port = _DEFAULT_PORT if not raw_port else _port_int(raw_port)
    return (host, port, str(info.get("dbname") or ""))


def unsafe_test_db_reason(
    test_url: str,
    ambient_url: str | None = None,
    postgres_port: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Return why `test_url` is unsafe, or None when it looks like a test DB."""
    info = _parse(test_url)
    if info is None:
        return "TEST_DATABASE_URL cannot be parsed"
    dbname = str(info.get("dbname") or "")
    if not dbname.endswith("_test"):
        return "TEST_DATABASE_URL database name must end in '_test'"
    if "hostaddr" in info or "service" in info:
        return "TEST_DATABASE_URL must not use hostaddr or service"
    if env and (env.get("PGHOSTADDR") or env.get("PGSERVICE")):
        return "PGHOSTADDR and PGSERVICE must not be set when running DB tests"
    host = str(info.get("host") or "")
    raw_port = str(info.get("port") or "").strip()
    if not host:
        return "TEST_DATABASE_URL must name a host explicitly"
    if not raw_port:
        return "TEST_DATABASE_URL must name a port explicitly"
    port = _port_int(raw_port)
    if port is None:
        return "TEST_DATABASE_URL port must be a single number"
    if _norm_host(host) != "localhost":
        return "TEST_DATABASE_URL host must be localhost, 127.0.0.1 or ::1"
    if port == _COMPOSE_PORT:
        return "TEST_DATABASE_URL port 5433 is the compose dev database"
    if postgres_port and postgres_port.strip():
        pg_port = _port_int(postgres_port)
        if pg_port is not None and port == pg_port:
            return "TEST_DATABASE_URL port equals POSTGRES_PORT"
    if ambient_url and ambient_url.strip():
        amb = _parse(ambient_url)
        if amb is not None and _identity(info) == _identity(amb):
            return "TEST_DATABASE_URL is the same database as DATABASE_URL"
    return None
