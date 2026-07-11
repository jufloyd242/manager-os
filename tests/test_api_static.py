"""Tests for FastAPI static frontend serving."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException

from manager_os.api.app import create_app


def _build_frontend_dist(tmp_path: Path) -> Path:
    """Create a minimal frontend dist directory for testing."""
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text(
        '<!DOCTYPE html><html><head><title>Manager OS</title></head>'
        '<body><div id="root"></div></body></html>'
    )
    assets = dist / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('hello')")
    (assets / "app.css").write_text("body { color: red; }")
    return dist


# ---------------------------------------------------------------------------
# API-only mode (no frontend_dist)
# ---------------------------------------------------------------------------


def test_api_health_without_frontend():
    """Health endpoint works without frontend."""
    client = TestClient(create_app())
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_api_404_without_frontend():
    """Unknown API path returns JSON 404 without frontend."""
    client = TestClient(create_app())
    resp = client.get("/api/nonexistent")
    assert resp.status_code == 404
    # Should be JSON, not HTML
    assert resp.headers.get("content-type", "").startswith("application/json")


def test_root_404_without_frontend():
    """Root returns 404 when no frontend is configured."""
    client = TestClient(create_app())
    resp = client.get("/")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# With frontend_dist
# ---------------------------------------------------------------------------


def test_api_health_with_frontend(tmp_path):
    """Health endpoint still works with frontend enabled."""
    dist = _build_frontend_dist(tmp_path)
    client = TestClient(create_app(frontend_dist=str(dist)))
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_root_serves_index(tmp_path):
    """Root serves the React index.html."""
    dist = _build_frontend_dist(tmp_path)
    client = TestClient(create_app(frontend_dist=str(dist)))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Manager OS" in resp.text
    assert resp.headers.get("content-type", "").startswith("text/html")


def test_spa_fallback(tmp_path):
    """Non-API paths serve index.html (SPA fallback)."""
    dist = _build_frontend_dist(tmp_path)
    client = TestClient(create_app(frontend_dist=str(dist)))
    resp = client.get("/deals")
    assert resp.status_code == 200
    assert "Manager OS" in resp.text


def test_spa_fallback_nested(tmp_path):
    """Deep non-API paths serve index.html."""
    dist = _build_frontend_dist(tmp_path)
    client = TestClient(create_app(frontend_dist=str(dist)))
    resp = client.get("/forecast/week/3")
    assert resp.status_code == 200
    assert "Manager OS" in resp.text


def test_assets_served(tmp_path):
    """Static assets are served correctly."""
    dist = _build_frontend_dist(tmp_path)
    client = TestClient(create_app(frontend_dist=str(dist)))
    resp = client.get("/assets/app.js")
    assert resp.status_code == 200
    assert resp.text == "console.log('hello')"


def test_assets_css_served(tmp_path):
    """CSS assets are served with correct content type."""
    dist = _build_frontend_dist(tmp_path)
    client = TestClient(create_app(frontend_dist=str(dist)))
    resp = client.get("/assets/app.css")
    assert resp.status_code == 200
    assert "color: red" in resp.text


def test_missing_asset_returns_404(tmp_path):
    """Missing asset returns 404, not index.html."""
    dist = _build_frontend_dist(tmp_path)
    client = TestClient(create_app(frontend_dist=str(dist)))
    resp = client.get("/assets/nonexistent.js")
    assert resp.status_code == 404


def test_api_404_remains_json(tmp_path):
    """API 404 returns JSON, not HTML."""
    dist = _build_frontend_dist(tmp_path)
    client = TestClient(create_app(frontend_dist=str(dist)))
    resp = client.get("/api/nonexistent")
    assert resp.status_code == 404
    assert resp.headers.get("content-type", "").startswith("application/json")


def test_api_path_not_intercepted(tmp_path):
    """API paths are not intercepted by SPA fallback."""
    dist = _build_frontend_dist(tmp_path)
    client = TestClient(create_app(frontend_dist=str(dist)))
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_missing_frontend_dist_raises(tmp_path):
    """Missing frontend dist raises RuntimeError."""
    missing = tmp_path / "nonexistent"
    with pytest.raises(RuntimeError, match="Frontend build directory not found"):
        create_app(frontend_dist=str(missing))


def test_missing_index_html_raises(tmp_path):
    """Missing index.html in dist raises RuntimeError."""
    dist = tmp_path / "dist"
    dist.mkdir()
    with pytest.raises(RuntimeError, match="missing index.html"):
        create_app(frontend_dist=str(dist))


def test_module_level_app_created():
    """Module-level app is created successfully (no crash)."""
    from manager_os.api.app import app as module_app
    assert module_app is not None
    assert hasattr(module_app, "routes")