"""Tests for forecast provenance and freshness verification."""

import json
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from manager_os.ingest.forecast import _verify_forecast_provenance


class MockSettings:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_verify_provenance_fails_if_no_metadata(tmp_path):
    csv_path = tmp_path / "forecast.csv"
    csv_path.write_text("a,b\n1,2")
    
    settings = MockSettings(
        forecast_sheet_id="123",
        forecast_sheet_gid="0",
        forecast_stale_after_hours=24
    )
    
    with pytest.raises(RuntimeError, match="missing provenance metadata"):
        _verify_forecast_provenance(str(csv_path), settings)


def test_verify_provenance_fails_if_wrong_source(tmp_path):
    csv_path = tmp_path / "forecast.csv"
    csv_path.write_text("a,b\n1,2")
    content_hash = hashlib.sha256(b"a,b\n1,2").hexdigest()
    
    meta_path = tmp_path / "forecast.csv.meta.json"
    meta_path.write_text(json.dumps({
        "source": "local_csv",
        "sheet_id": "123",
        "gid": "0",
        "content_hash": content_hash,
        "retrieved_at": datetime.now(timezone.utc).isoformat()
    }))
    
    settings = MockSettings(
        forecast_sheet_id="123",
        forecast_sheet_gid="0",
        forecast_stale_after_hours=24
    )
    
    with pytest.raises(RuntimeError, match="expected 'google_sheet_gemini'"):
        _verify_forecast_provenance(str(csv_path), settings)


def test_verify_provenance_fails_if_sheet_id_mismatch(tmp_path):
    csv_path = tmp_path / "forecast.csv"
    csv_path.write_text("a,b\n1,2")
    content_hash = hashlib.sha256(b"a,b\n1,2").hexdigest()
    
    meta_path = tmp_path / "forecast.csv.meta.json"
    meta_path.write_text(json.dumps({
        "source": "google_sheet_gemini",
        "sheet_id": "wrong_id",
        "gid": "0",
        "content_hash": content_hash,
        "retrieved_at": datetime.now(timezone.utc).isoformat()
    }))
    
    settings = MockSettings(
        forecast_sheet_id="123",
        forecast_sheet_gid="0",
        forecast_stale_after_hours=24
    )
    
    with pytest.raises(RuntimeError, match="sheet_id/gid mismatch"):
        _verify_forecast_provenance(str(csv_path), settings)


def test_verify_provenance_fails_if_content_hash_mismatch(tmp_path):
    csv_path = tmp_path / "forecast.csv"
    csv_path.write_text("a,b\n1,2")
    
    meta_path = tmp_path / "forecast.csv.meta.json"
    meta_path.write_text(json.dumps({
        "source": "google_sheet_gemini",
        "sheet_id": "123",
        "gid": "0",
        "content_hash": "wrong_hash",
        "retrieved_at": datetime.now(timezone.utc).isoformat()
    }))
    
    settings = MockSettings(
        forecast_sheet_id="123",
        forecast_sheet_gid="0",
        forecast_stale_after_hours=24
    )
    
    with pytest.raises(RuntimeError, match="content hash does not match"):
        _verify_forecast_provenance(str(csv_path), settings)


def test_verify_provenance_fails_if_stale(tmp_path):
    csv_path = tmp_path / "forecast.csv"
    csv_path.write_text("a,b\n1,2")
    content_hash = hashlib.sha256(b"a,b\n1,2").hexdigest()
    
    stale_time = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    
    meta_path = tmp_path / "forecast.csv.meta.json"
    meta_path.write_text(json.dumps({
        "source": "google_sheet_gemini",
        "sheet_id": "123",
        "gid": "0",
        "content_hash": content_hash,
        "retrieved_at": stale_time
    }))
    
    settings = MockSettings(
        forecast_sheet_id="123",
        forecast_sheet_gid="0",
        forecast_stale_after_hours=24
    )
    
    with pytest.raises(RuntimeError, match="is stale"):
        _verify_forecast_provenance(str(csv_path), settings)


def test_verify_provenance_succeeds_if_valid(tmp_path):
    csv_path = tmp_path / "forecast.csv"
    csv_path.write_text("a,b\n1,2")
    content_hash = hashlib.sha256(b"a,b\n1,2").hexdigest()
    
    meta_path = tmp_path / "forecast.csv.meta.json"
    meta_path.write_text(json.dumps({
        "source": "google_sheet_gemini",
        "sheet_id": "123",
        "gid": "0",
        "content_hash": content_hash,
        "retrieved_at": datetime.now(timezone.utc).isoformat()
    }))
    
    settings = MockSettings(
        forecast_sheet_id="123",
        forecast_sheet_gid="0",
        forecast_stale_after_hours=24
    )
    
    # Should not raise
    _verify_forecast_provenance(str(csv_path), settings)