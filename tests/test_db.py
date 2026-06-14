"""Tests for db.py — schema init and helpers."""

from __future__ import annotations

import pytest

from manager_os.db import _ALL_TABLES, content_hash, get_connection, init_schema, list_tables


def test_init_schema_creates_all_tables() -> None:
    conn = get_connection(":memory:")
    tables = list_tables(conn)
    for expected_table in _ALL_TABLES:
        assert expected_table in tables, f"Missing table: {expected_table}"


def test_init_schema_idempotent() -> None:
    conn = get_connection(":memory:")
    # Should not raise on second call
    init_schema(conn)
    init_schema(conn)
    assert len(list_tables(conn)) == len(_ALL_TABLES)


def test_content_hash_deterministic() -> None:
    h1 = content_hash("hello world")
    h2 = content_hash("hello world")
    assert h1 == h2


def test_content_hash_different_inputs() -> None:
    assert content_hash("a") != content_hash("b")


def test_content_hash_is_sha256() -> None:
    import hashlib
    expected = hashlib.sha256("test".encode()).hexdigest()
    assert content_hash("test") == expected


def test_content_hash_empty_string() -> None:
    h = content_hash("")
    assert isinstance(h, str)
    assert len(h) == 64  # SHA-256 hex = 64 chars


def test_get_connection_returns_connection() -> None:
    conn = get_connection(":memory:")
    result = conn.execute("SELECT 1").fetchone()
    assert result == (1,)


def test_signals_table_has_expected_columns() -> None:
    conn = get_connection(":memory:")
    rows = conn.execute("DESCRIBE signals").fetchall()
    col_names = {row[0] for row in rows}
    expected_cols = {
        "id", "signal_date", "source", "source_path", "entity_type",
        "entity_name", "signal_type", "severity", "summary",
        "why_it_matters", "requires_manager_attention", "owner",
        "due_date", "confidence", "status", "created_at", "updated_at",
    }
    assert expected_cols.issubset(col_names)
