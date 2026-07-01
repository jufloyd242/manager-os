"""FastAPI dependency functions.

Settings and DB connections are resolved fresh per-request (never cached at
import time) so tests can point each request at a different temp DuckDB path
via the MANAGER_OS_DB_PATH environment variable.
"""

from __future__ import annotations

from collections.abc import Iterator

import duckdb

from manager_os.config import Settings, get_settings
from manager_os.db import get_connection


def get_fresh_settings() -> Settings:
    """Return a freshly loaded Settings instance (reflects current env vars)."""
    return get_settings()


def get_db_connection() -> Iterator[duckdb.DuckDBPyConnection]:
    """Yield a DuckDB connection opened against the current settings.db_path.

    Opened and closed per-request; never held open across requests.
    """
    settings = get_fresh_settings()
    conn = get_connection(settings.db_path)
    try:
        yield conn
    finally:
        conn.close()
