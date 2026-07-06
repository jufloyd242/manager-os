"""Tests verifying resilience of forecast-fetch JSON parsing with noisy output."""

from __future__ import annotations

import json
import hashlib
from unittest.mock import patch, MagicMock
from pathlib import Path

from typer.testing import CliRunner

from manager_os.cli import app

runner = CliRunner()


def test_extract_json_payload_robustness():
    """Test unit behavior of _extract_json_payload on different formats."""
    # We import internally so that when the test runs, if the helper is not defined, it fails appropriately.
    from manager_os.cli import _extract_json_payload
    
    # 1. Clean JSON
    clean_json = '{"ok": true, "rows": []}'
    assert _extract_json_payload(clean_json) == clean_json

    # 2. Markdown block
    md_block = '```json\n{"ok": true, "rows": []}\n```'
    assert _extract_json_payload(md_block) == '{"ok": true, "rows": []}'

    # 3. Noisy markdown block
    noisy_block = 'Initial junk streaming here...\n```json\n{"ok": true, "rows": []}\n```'
    assert _extract_json_payload(noisy_block) == '{"ok": true, "rows": []}'

    # 4. Outermost braces strategy
    outermost = 'Some prefix junk {"ok": true} some suffix junk'
    assert _extract_json_payload(outermost) == '{"ok": true}'


def test_forecast_fetch_with_noisy_stdout(tmp_path):
    """Test forecast-fetch parses correctly when Gemini stdout contains markdown block and junk."""
    local_csv = tmp_path / "forecast.csv"
    
    noisy_gemini_stdout = """
    Initial un-wrapped JSON fragment chunk:
    {"ok": true,
    And here is the complete correct JSON inside blocks:
    ```json
    {
      "ok": true,
      "source": "google_sheet_forecast",
      "source_url": "https://docs.google.com/spreadsheets/d/test/edit?gid=456",
      "sheet_id": "test_forecast_sheet_id",
      "gid": "456",
      "retrieved_at": "2026-06-18T10:00:00Z",
      "rows": [
        ["Week", "Person", "Allocation"],
        ["2024-06-17", "Alice", "100%"]
      ]
    }
    ```
    """

    with patch('subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=noisy_gemini_stdout
        )
        
        result = runner.invoke(app, [
            "forecast-fetch",
            "--force",
            "--output", str(local_csv),
            "--sheet-id", "test_forecast_sheet_id",
            "--gid", "456",
            "--sheet-url", "https://docs.google.com/spreadsheets/d/test/edit?gid=456"
        ])
        
        assert result.exit_code == 0, f"Command failed: {result.output}"
        assert local_csv.exists()
