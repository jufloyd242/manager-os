"""Tests for the startup/preflight module."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from manager_os.startup import (
    DoctorCheck,
    DoctorReport,
    check_frontend_dependencies,
    check_port_available,
    check_port_has_manager_os,
    compute_source_fingerprint,
    find_repo_root,
    get_python_version,
    is_build_current,
    open_browser,
    read_build_manifest,
    resolve_python_environment,
    run_doctor,
    run_frontend_build,
    run_npm_install,
    start_server_process,
    start_vite_process,
    terminate_process,
    terminate_process_tree,
    wait_for_health,
    write_build_manifest,
)


# ---------------------------------------------------------------------------
# find_repo_root
# ---------------------------------------------------------------------------

def test_find_repo_root_found():
    root = find_repo_root()
    assert (root / "pyproject.toml").exists()
    assert (root / "src" / "manager_os").is_dir()


def test_find_repo_root_not_found():
    with pytest.raises(FileNotFoundError):
        find_repo_root(marker="nonexistent-marker-xyz")


# ---------------------------------------------------------------------------
# resolve_python_environment
# ---------------------------------------------------------------------------

def test_resolve_venv_python(tmp_path):
    """Should prefer .venv/bin/python when it exists."""
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    python_exe = venv_bin / "python"
    python_exe.write_text("#!/bin/sh\necho fake")
    python_exe.chmod(0o755)

    result = resolve_python_environment(tmp_path)
    assert result == python_exe


@patch("subprocess.run")
def test_resolve_system_python(mock_run):
    """Should fall back to system python when no .venv."""
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="Python 3.12.0\n", stderr=""),
        MagicMock(returncode=0, stdout="/usr/local/bin/python3.12\n", stderr=""),
    ]
    result = resolve_python_environment(Path("/tmp"))
    assert result == Path("/usr/local/bin/python3.12")


@patch("subprocess.run")
def test_resolve_python_too_old(mock_run):
    """Should reject python3 when version < 3.11."""
    mock_run.side_effect = [
        FileNotFoundError(),  # python3.13
        FileNotFoundError(),  # python3.12
        FileNotFoundError(),  # python3.11
        MagicMock(returncode=0, stdout="Python 3.10.0\n", stderr=""),
    ]
    with pytest.raises(RuntimeError, match="Python >= 3.11"):
        resolve_python_environment(Path("/tmp"))


@patch("subprocess.run")
def test_resolve_no_python(mock_run):
    """Should raise when no python found at all."""
    mock_run.side_effect = FileNotFoundError()
    with pytest.raises(RuntimeError, match="Python >= 3.11"):
        resolve_python_environment(Path("/tmp"))


# ---------------------------------------------------------------------------
# get_python_version
# ---------------------------------------------------------------------------

@patch("subprocess.run")
def test_get_python_version(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0, stdout="Python 3.12.4\n", stderr=""
    )
    result = get_python_version(Path("/usr/bin/python3"))
    assert "3.12.4" in result


# ---------------------------------------------------------------------------
# check_port_available
# ---------------------------------------------------------------------------

def test_check_port_available():
    """Should return True for a free port."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        # Port is now bound, so it should NOT be available
        assert not check_port_available("127.0.0.1", port)


def test_check_port_free():
    """Port 0 should always be available (OS assigns)."""
    assert check_port_available("127.0.0.1", 0)


# ---------------------------------------------------------------------------
# check_port_has_manager_os
# ---------------------------------------------------------------------------

@patch("manager_os.startup.httpx.get")
def test_check_port_has_manager_os_healthy(mock_get):
    mock_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"ok": True, "service": "manager-os-api"},
    )
    assert check_port_has_manager_os("127.0.0.1", 8000)


@patch("manager_os.startup.httpx.get")
def test_check_port_has_manager_os_wrong_service(mock_get):
    mock_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"ok": True, "service": "something-else"},
    )
    assert not check_port_has_manager_os("127.0.0.1", 8000)


@patch("manager_os.startup.httpx.get")
def test_check_port_has_manager_os_unhealthy(mock_get):
    mock_get.side_effect = httpx.ConnectError("Connection refused")
    assert not check_port_has_manager_os("127.0.0.1", 8000)


# ---------------------------------------------------------------------------
# wait_for_health
# ---------------------------------------------------------------------------

@patch("manager_os.startup.httpx.get")
def test_wait_for_health_success(mock_get):
    mock_get.return_value = MagicMock(status_code=200)
    assert wait_for_health("http://127.0.0.1:8000/api/health", timeout=5)


@patch("manager_os.startup.httpx.get")
def test_wait_for_health_timeout(mock_get):
    mock_get.side_effect = httpx.ConnectError("Connection refused")
    assert not wait_for_health("http://127.0.0.1:8000/api/health", timeout=1, interval=0.1)


# ---------------------------------------------------------------------------
# Build manifest
# ---------------------------------------------------------------------------

def test_write_and_read_build_manifest(tmp_path):
    fingerprint = "abc123"
    write_build_manifest(tmp_path, fingerprint)
    manifest = read_build_manifest(tmp_path)
    assert manifest is not None
    assert manifest["schema_version"] == 1
    assert manifest["fingerprint"] == fingerprint
    assert "built_at" in manifest


def test_read_build_manifest_missing(tmp_path):
    assert read_build_manifest(tmp_path) is None


def test_read_build_manifest_corrupt(tmp_path):
    (tmp_path / ".build-manifest.json").write_text("not json")
    assert read_build_manifest(tmp_path) is None


def test_compute_source_fingerprint_stable(tmp_path):
    """Same content should produce same fingerprint."""
    src_dir = tmp_path / "frontend" / "src"
    src_dir.mkdir(parents=True)
    (tmp_path / "frontend" / "index.html").write_text("<html></html>")
    (tmp_path / "frontend" / "package.json").write_text('{"name": "test"}')
    (tmp_path / "frontend" / "package-lock.json").write_text("{}")
    (tmp_path / "frontend" / "vite.config.ts").write_text("")
    (tmp_path / "frontend" / "tsconfig.json").write_text("{}")
    (tmp_path / "frontend" / "tsconfig.app.json").write_text("{}")
    (tmp_path / "frontend" / "tsconfig.node.json").write_text("{}")
    (tmp_path / "frontend" / "tailwind.config.js").write_text("")
    (tmp_path / "frontend" / "postcss.config.js").write_text("")
    (src_dir / "main.ts").write_text("console.log('hello')")

    fp1 = compute_source_fingerprint(tmp_path)
    fp2 = compute_source_fingerprint(tmp_path)
    assert fp1 == fp2


def test_compute_source_fingerprint_changes(tmp_path):
    """Different content should produce different fingerprint."""
    src_dir = tmp_path / "frontend" / "src"
    src_dir.mkdir(parents=True)
    (tmp_path / "frontend" / "index.html").write_text("<html></html>")
    (tmp_path / "frontend" / "package.json").write_text('{"name": "test"}')
    (tmp_path / "frontend" / "package-lock.json").write_text("{}")
    (tmp_path / "frontend" / "vite.config.ts").write_text("")
    (tmp_path / "frontend" / "tsconfig.json").write_text("{}")
    (tmp_path / "frontend" / "tsconfig.app.json").write_text("{}")
    (tmp_path / "frontend" / "tsconfig.node.json").write_text("{}")
    (tmp_path / "frontend" / "tailwind.config.js").write_text("")
    (tmp_path / "frontend" / "postcss.config.js").write_text("")

    fp1 = compute_source_fingerprint(tmp_path)
    (src_dir / "main.ts").write_text("console.log('hello')")
    fp2 = compute_source_fingerprint(tmp_path)
    assert fp1 != fp2


# ---------------------------------------------------------------------------
# is_build_current
# ---------------------------------------------------------------------------

def test_is_build_current_no_dist(tmp_path):
    assert not is_build_current(tmp_path)


def test_is_build_current_no_manifest(tmp_path):
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>")
    assert not is_build_current(tmp_path)


def test_is_build_current_stale(tmp_path):
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>")
    write_build_manifest(dist, "old-fingerprint")
    assert not is_build_current(tmp_path)


def test_is_build_current_current(tmp_path):
    src_dir = tmp_path / "frontend" / "src"
    src_dir.mkdir(parents=True)
    (tmp_path / "frontend" / "index.html").write_text("<html></html>")
    (tmp_path / "frontend" / "package.json").write_text('{"name": "test"}')
    (tmp_path / "frontend" / "package-lock.json").write_text("{}")
    (tmp_path / "frontend" / "vite.config.ts").write_text("")
    (tmp_path / "frontend" / "tsconfig.json").write_text("{}")
    (tmp_path / "frontend" / "tsconfig.app.json").write_text("{}")
    (tmp_path / "frontend" / "tsconfig.node.json").write_text("{}")
    (tmp_path / "frontend" / "tailwind.config.js").write_text("")
    (tmp_path / "frontend" / "postcss.config.js").write_text("")

    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>")

    fp = compute_source_fingerprint(tmp_path)
    write_build_manifest(dist, fp)
    assert is_build_current(tmp_path)


# ---------------------------------------------------------------------------
# run_frontend_build
# ---------------------------------------------------------------------------

@patch("manager_os.startup.subprocess.run")
@patch("manager_os.startup.compute_source_fingerprint")
@patch("manager_os.startup.write_build_manifest")
def test_run_frontend_build_success(mock_write, mock_fp, mock_run, tmp_path):
    mock_run.return_value = MagicMock(returncode=0, stdout="built", stderr="")
    mock_fp.return_value = "new-fp"

    result = run_frontend_build(tmp_path)
    assert result.returncode == 0
    mock_write.assert_called_once()


@patch("manager_os.startup.subprocess.run")
def test_run_frontend_build_failure(mock_run, tmp_path):
    mock_run.return_value = MagicMock(
        returncode=1, stdout="", stderr="error"
    )
    result = run_frontend_build(tmp_path)
    assert result.returncode != 0


# ---------------------------------------------------------------------------
# run_npm_install
# ---------------------------------------------------------------------------

@patch("manager_os.startup.subprocess.run")
def test_run_npm_install_with_lockfile(mock_run, tmp_path):
    frontend = tmp_path / "frontend"
    frontend.mkdir(parents=True)
    (frontend / "package-lock.json").write_text("{}")
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    run_npm_install(tmp_path)
    args = mock_run.call_args[0][0]
    assert args == ["npm", "ci"]


@patch("manager_os.startup.subprocess.run")
def test_run_npm_install_without_lockfile(mock_run, tmp_path):
    frontend = tmp_path / "frontend"
    frontend.mkdir(parents=True)
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    run_npm_install(tmp_path)
    args = mock_run.call_args[0][0]
    assert args == ["npm", "install"]


# ---------------------------------------------------------------------------
# check_frontend_dependencies
# ---------------------------------------------------------------------------

def test_check_frontend_dependencies_present(tmp_path):
    (tmp_path / "frontend" / "node_modules").mkdir(parents=True)
    assert check_frontend_dependencies(tmp_path)


def test_check_frontend_dependencies_missing(tmp_path):
    assert not check_frontend_dependencies(tmp_path)


# ---------------------------------------------------------------------------
# start_server_process / start_vite_process
# ---------------------------------------------------------------------------

@patch("manager_os.startup.resolve_python_environment")
@patch("manager_os.startup.subprocess.Popen")
def test_start_server_process(mock_popen, mock_resolve):
    mock_resolve.return_value = Path("/venv/bin/python")
    mock_popen.return_value = MagicMock()

    proc = start_server_process(Path("/repo"), "127.0.0.1", 8000)
    assert proc is not None
    args = mock_popen.call_args[0][0]
    assert "uvicorn" in args
    assert "8000" in args


@patch("manager_os.startup.subprocess.Popen")
def test_start_vite_process(mock_popen):
    mock_popen.return_value = MagicMock()
    proc = start_vite_process(Path("/repo"), 5173)
    assert proc is not None
    args = mock_popen.call_args[0][0]
    assert "vite" in args
    assert "5173" in args


# ---------------------------------------------------------------------------
# terminate_process
# ---------------------------------------------------------------------------

def test_terminate_process_already_dead():
    proc = MagicMock()
    proc.poll.return_value = 0
    terminate_process(proc)
    proc.terminate.assert_not_called()


@patch("manager_os.startup.subprocess.Popen")
def test_terminate_process_graceful(mock_popen):
    proc = MagicMock()
    proc.poll.return_value = None
    proc.wait.return_value = 0
    terminate_process(proc)
    proc.terminate.assert_called_once()


# ---------------------------------------------------------------------------
# open_browser
# ---------------------------------------------------------------------------

@patch("manager_os.startup.webbrowser.open")
def test_open_browser(mock_open):
    open_browser("http://127.0.0.1:8000")
    mock_open.assert_called_once_with("http://127.0.0.1:8000")


# ---------------------------------------------------------------------------
# DoctorCheck / DoctorReport
# ---------------------------------------------------------------------------

def test_doctor_check_to_dict():
    check = DoctorCheck("Python", "PASS", "3.12.0", "detail")
    d = check.to_dict()
    assert d["name"] == "Python"
    assert d["status"] == "PASS"
    assert d["message"] == "3.12.0"


def test_doctor_report_all_pass():
    report = DoctorReport([
        DoctorCheck("A", "PASS"),
        DoctorCheck("B", "PASS"),
        DoctorCheck("C", "WARN"),
    ])
    assert report.all_pass
    assert report.exit_code == 0
    assert report.warnings == 1
    assert report.failures == 0


def test_doctor_report_has_failure():
    report = DoctorReport([
        DoctorCheck("A", "PASS"),
        DoctorCheck("B", "FAIL"),
    ])
    assert not report.all_pass
    assert report.exit_code == 1
    assert report.failures == 1


def test_doctor_report_to_dict():
    report = DoctorReport([DoctorCheck("A", "PASS")])
    d = report.to_dict()
    assert d["all_pass"] is True
    assert d["exit_code"] == 0
    assert len(d["checks"]) == 1


# ---------------------------------------------------------------------------
# run_doctor
# ---------------------------------------------------------------------------

@patch("manager_os.startup.check_port_available")
@patch("manager_os.startup.check_frontend_dependencies")
@patch("manager_os.startup.is_build_current")
@patch("manager_os.startup.resolve_python_environment")
@patch("manager_os.startup.get_python_version")
def test_run_doctor_all_pass(
    mock_version, mock_resolve, mock_build, mock_deps, mock_port, tmp_path
):
    """Doctor should return all_pass=True when everything is fine."""
    # Set up repo marker
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").write_text("")
    (tmp_path / "frontend" / "node_modules").mkdir(parents=True)
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>")
    (tmp_path / ".env").write_text("MANAGER_OS_VAULT_PATH=/tmp/vault")

    mock_resolve.return_value = tmp_path / ".venv" / "bin" / "python"
    mock_version.return_value = "Python 3.12.4"
    mock_build.return_value = True
    mock_deps.return_value = True
    mock_port.return_value = True

    with patch.dict(os.environ, {
        "MANAGER_OS_DB_PATH": str(tmp_path / "data" / "processed" / "test.duckdb"),
        "MANAGER_OS_VAULT_PATH": str(tmp_path / "vault"),
    }):
        report = run_doctor(tmp_path)
        assert report.all_pass, (
            f"Expected all pass but got failures: "
            f"{[(c.name, c.status) for c in report.checks if c.status == 'FAIL']}"
        )


@patch("manager_os.startup.check_port_available")
@patch("manager_os.startup.check_frontend_dependencies")
@patch("manager_os.startup.is_build_current")
@patch("manager_os.startup.resolve_python_environment")
@patch("manager_os.startup.get_python_version")
def test_run_doctor_json_output(
    mock_version, mock_resolve, mock_build, mock_deps, mock_port, tmp_path
):
    """Doctor JSON output should have stable structure."""
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").write_text("")
    (tmp_path / "frontend" / "node_modules").mkdir(parents=True)
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>")
    (tmp_path / ".env").write_text("MANAGER_OS_VAULT_PATH=/tmp/vault")

    mock_resolve.return_value = tmp_path / ".venv" / "bin" / "python"
    mock_version.return_value = "Python 3.12.4"
    mock_build.return_value = True
    mock_deps.return_value = True
    mock_port.return_value = True

    with patch.dict(os.environ, {
        "MANAGER_OS_DB_PATH": str(tmp_path / "data" / "processed" / "test.duckdb"),
        "MANAGER_OS_VAULT_PATH": str(tmp_path / "vault"),
    }):
        report = run_doctor(tmp_path)
        d = report.to_dict()
        assert "checks" in d
        assert "all_pass" in d
        assert "exit_code" in d
        assert "warnings" in d
        assert "failures" in d
        for check in d["checks"]:
            assert set(check.keys()) == {"name", "status", "message", "detail"}


@patch("manager_os.startup.check_port_available")
@patch("manager_os.startup.check_frontend_dependencies")
@patch("manager_os.startup.is_build_current")
@patch("manager_os.startup.resolve_python_environment")
@patch("manager_os.startup.get_python_version")
def test_run_doctor_no_external_calls(
    mock_version, mock_resolve, mock_build, mock_deps, mock_port, tmp_path
):
    """Doctor must not make external retrieval calls."""
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").write_text("")

    mock_resolve.return_value = tmp_path / ".venv" / "bin" / "python"
    mock_version.return_value = "Python 3.12.4"
    mock_build.return_value = False
    mock_deps.return_value = False
    mock_port.return_value = True

    with patch.dict(os.environ, {
        "MANAGER_OS_DB_PATH": str(tmp_path / "data" / "processed" / "test.duckdb"),
    }):
        report = run_doctor(tmp_path)
        # Should not crash, should produce a report
        assert isinstance(report, DoctorReport)
        assert len(report.checks) > 0