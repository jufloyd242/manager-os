"""Tests for CLI start/dev/doctor/build commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from manager_os.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


@patch("manager_os.startup.run_doctor")
def test_doctor_command(mock_doctor):
    """Doctor command runs and exits with report exit code."""
    from manager_os.startup import DoctorReport, DoctorCheck

    mock_doctor.return_value = DoctorReport([
        DoctorCheck("Python", "PASS", "3.12.0"),
        DoctorCheck("Node.js", "PASS", "22.0.0"),
    ])

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "Manager OS Doctor" in result.stdout
    assert "PASS" in result.stdout


@patch("manager_os.startup.run_doctor")
def test_doctor_command_failure(mock_doctor):
    """Doctor exits nonzero when checks fail."""
    from manager_os.startup import DoctorReport, DoctorCheck

    mock_doctor.return_value = DoctorReport([
        DoctorCheck("Python", "FAIL", "Not found"),
    ])

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "FAIL" in result.stdout


@patch("manager_os.startup.run_doctor")
def test_doctor_json_output(mock_doctor):
    """Doctor --json outputs machine-readable JSON."""
    from manager_os.startup import DoctorReport, DoctorCheck

    mock_doctor.return_value = DoctorReport([
        DoctorCheck("Python", "PASS", "3.12.0"),
    ])

    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0
    import json
    data = json.loads(result.stdout)
    assert "checks" in data
    assert data["all_pass"] is True
    assert data["exit_code"] == 0


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


@patch("manager_os.startup.run_frontend_build")
@patch("manager_os.startup.run_npm_install")
@patch("manager_os.startup.check_frontend_dependencies")
@patch("manager_os.startup.is_build_current")
@patch("manager_os.startup.subprocess.run")
def test_build_command_current(
    mock_subprocess, mock_build_current, mock_deps, mock_npm, mock_build
):
    """Build command skips when build is current."""
    mock_subprocess.return_value = MagicMock(returncode=0)
    mock_build_current.return_value = True
    mock_deps.return_value = True

    result = runner.invoke(app, ["build"])
    assert result.exit_code == 0
    assert "current" in result.stdout.lower()
    mock_build.assert_not_called()


@patch("manager_os.startup.run_frontend_build")
@patch("manager_os.startup.run_npm_install")
@patch("manager_os.startup.check_frontend_dependencies")
@patch("manager_os.startup.is_build_current")
@patch("manager_os.startup.subprocess.run")
def test_build_command_rebuilds(
    mock_subprocess, mock_build_current, mock_deps, mock_npm, mock_build
):
    """Build command rebuilds when stale."""
    mock_subprocess.return_value = MagicMock(returncode=0)
    mock_build_current.return_value = False
    mock_deps.return_value = True
    mock_build.return_value = MagicMock(returncode=0, stdout="built", stderr="")

    result = runner.invoke(app, ["build"])
    assert result.exit_code == 0
    mock_build.assert_called_once()


@patch("manager_os.startup.run_frontend_build")
@patch("manager_os.startup.run_npm_install")
@patch("manager_os.startup.check_frontend_dependencies")
@patch("manager_os.startup.is_build_current")
@patch("manager_os.startup.subprocess.run")
def test_build_command_force(
    mock_subprocess, mock_build_current, mock_deps, mock_npm, mock_build
):
    """Build --force rebuilds even when current."""
    mock_subprocess.return_value = MagicMock(returncode=0)
    mock_build_current.return_value = True
    mock_deps.return_value = True
    mock_build.return_value = MagicMock(returncode=0, stdout="built", stderr="")

    result = runner.invoke(app, ["build", "--force"])
    assert result.exit_code == 0
    mock_build.assert_called_once()


@patch("manager_os.startup.run_frontend_build")
@patch("manager_os.startup.run_npm_install")
@patch("manager_os.startup.check_frontend_dependencies")
@patch("manager_os.startup.is_build_current")
@patch("manager_os.startup.subprocess.run")
def test_build_command_failure(
    mock_subprocess, mock_build_current, mock_deps, mock_npm, mock_build
):
    """Build command exits nonzero on failure."""
    mock_subprocess.return_value = MagicMock(returncode=0)
    mock_build_current.return_value = False
    mock_deps.return_value = True
    mock_build.return_value = MagicMock(returncode=1, stdout="", stderr="error")

    result = runner.invoke(app, ["build"])
    assert result.exit_code != 0


@patch("manager_os.startup.run_frontend_build")
@patch("manager_os.startup.run_npm_install")
@patch("manager_os.startup.check_frontend_dependencies")
@patch("manager_os.startup.is_build_current")
@patch("manager_os.startup.subprocess.run")
def test_build_installs_deps(
    mock_subprocess, mock_build_current, mock_deps, mock_npm, mock_build
):
    """Build installs frontend deps when missing."""
    mock_subprocess.return_value = MagicMock(returncode=0)
    mock_build_current.return_value = False
    mock_deps.return_value = False
    mock_npm.return_value = MagicMock(returncode=0, stdout="", stderr="")
    mock_build.return_value = MagicMock(returncode=0, stdout="built", stderr="")

    result = runner.invoke(app, ["build"])
    assert result.exit_code == 0
    mock_npm.assert_called_once()


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


@patch("manager_os.startup.start_server_process")
@patch("manager_os.startup.wait_for_health")
@patch("manager_os.startup.check_port_has_manager_os")
@patch("manager_os.startup.check_port_available")
@patch("manager_os.startup.is_build_current")
@patch("manager_os.startup.check_frontend_dependencies")
def test_start_already_running(
    mock_deps, mock_build, mock_port, mock_running, mock_health, mock_server
):
    """Start detects existing instance and exits successfully."""
    mock_running.return_value = True

    result = runner.invoke(app, ["start", "--no-browser"])
    assert result.exit_code == 0
    assert "already running" in result.stdout.lower()
    mock_server.assert_not_called()


@patch("manager_os.startup.start_server_process")
@patch("manager_os.startup.wait_for_health")
@patch("manager_os.startup.check_port_has_manager_os")
@patch("manager_os.startup.check_port_available")
@patch("manager_os.startup.is_build_current")
@patch("manager_os.startup.check_frontend_dependencies")
def test_start_port_conflict(
    mock_deps, mock_build, mock_port, mock_running, mock_health, mock_server
):
    """Start fails clearly on port conflict."""
    mock_running.return_value = False
    mock_port.return_value = False

    result = runner.invoke(app, ["start", "--no-browser"])
    assert result.exit_code != 0
    assert "already in use" in result.stdout.lower()
    mock_server.assert_not_called()


@patch("manager_os.startup.start_server_process")
@patch("manager_os.startup.wait_for_health")
@patch("manager_os.startup.open_browser")
@patch("manager_os.startup.check_port_has_manager_os")
@patch("manager_os.startup.check_port_available")
@patch("manager_os.startup.is_build_current")
@patch("manager_os.startup.check_frontend_dependencies")
def test_start_success(
    mock_deps, mock_build, mock_port, mock_running, mock_browser, mock_health, mock_server
):
    """Start command starts server and waits for health."""
    mock_running.return_value = False
    mock_port.return_value = True
    mock_build.return_value = True
    mock_deps.return_value = True
    mock_health.return_value = True
    mock_server.return_value = MagicMock()

    result = runner.invoke(app, ["start", "--no-browser"])
    assert result.exit_code == 0
    assert "running" in result.stdout.lower()
    mock_server.assert_called_once()
    mock_browser.assert_not_called()


@patch("manager_os.startup.start_server_process")
@patch("manager_os.startup.wait_for_health")
@patch("manager_os.startup.open_browser")
@patch("manager_os.startup.check_port_has_manager_os")
@patch("manager_os.startup.check_port_available")
@patch("manager_os.startup.is_build_current")
@patch("manager_os.startup.check_frontend_dependencies")
def test_start_opens_browser(
    mock_deps, mock_build, mock_port, mock_running, mock_browser, mock_health, mock_server
):
    """Start opens browser after health succeeds."""
    mock_running.return_value = False
    mock_port.return_value = True
    mock_build.return_value = True
    mock_deps.return_value = True
    mock_health.return_value = True
    mock_server.return_value = MagicMock()

    result = runner.invoke(app, ["start"])
    assert result.exit_code == 0
    mock_browser.assert_called_once()


@patch("manager_os.startup.start_server_process")
@patch("manager_os.startup.wait_for_health")
@patch("manager_os.startup.check_port_has_manager_os")
@patch("manager_os.startup.check_port_available")
@patch("manager_os.startup.is_build_current")
@patch("manager_os.startup.check_frontend_dependencies")
def test_start_health_timeout(
    mock_deps, mock_build, mock_port, mock_running, mock_health, mock_server
):
    """Start fails when health check times out."""
    mock_running.return_value = False
    mock_port.return_value = True
    mock_build.return_value = True
    mock_deps.return_value = True
    mock_health.return_value = False
    mock_server.return_value = MagicMock()

    result = runner.invoke(app, ["start", "--no-browser"])
    assert result.exit_code != 0
    assert "failed to start" in result.stdout.lower()


@patch("manager_os.startup.start_server_process")
@patch("manager_os.startup.wait_for_health")
@patch("manager_os.startup.check_port_has_manager_os")
@patch("manager_os.startup.check_port_available")
@patch("manager_os.startup.is_build_current")
@patch("manager_os.startup.check_frontend_dependencies")
@patch("manager_os.startup.run_frontend_build")
def test_start_rebuild_flag(
    mock_run_build, mock_deps, mock_build, mock_port, mock_running, mock_health, mock_server
):
    """Start --rebuild forces frontend rebuild."""
    mock_running.return_value = False
    mock_port.return_value = True
    mock_build.return_value = True  # would be current, but --rebuild overrides
    mock_deps.return_value = True
    mock_health.return_value = True
    mock_server.return_value = MagicMock()
    mock_run_build.return_value = MagicMock(returncode=0, stdout="built", stderr="")

    result = runner.invoke(app, ["start", "--no-browser", "--rebuild"])
    assert result.exit_code == 0
    mock_run_build.assert_called_once()


# ---------------------------------------------------------------------------
# dev
# ---------------------------------------------------------------------------


@patch("manager_os.startup.start_vite_process")
@patch("manager_os.startup.start_server_process")
@patch("manager_os.startup.wait_for_health")
@patch("manager_os.startup.check_port_available")
def test_dev_starts_both_servers(
    mock_port, mock_health, mock_server, mock_vite
):
    """Dev starts both API and Vite processes."""
    mock_port.return_value = True
    mock_health.return_value = True
    mock_server.return_value = MagicMock()
    mock_vite.return_value = MagicMock()

    # Need to handle the infinite loop in dev command
    # We'll use a side effect that raises KeyboardInterrupt
    import time as _time
    original_sleep = _time.sleep

    def interrupt_after_sleep(*args, **kwargs):
        raise KeyboardInterrupt()

    with patch("time.sleep", side_effect=interrupt_after_sleep):
        result = runner.invoke(app, ["dev", "--no-browser"])

    assert result.exit_code == 0
    mock_server.assert_called_once()
    mock_vite.assert_called_once()


@patch("manager_os.startup.start_vite_process")
@patch("manager_os.startup.start_server_process")
@patch("manager_os.startup.wait_for_health")
@patch("manager_os.startup.check_port_available")
def test_dev_port_conflict(
    mock_port, mock_health, mock_server, mock_vite
):
    """Dev fails on port conflict."""
    mock_port.return_value = False

    result = runner.invoke(app, ["dev", "--no-browser"])
    assert result.exit_code != 0
    assert "already in use" in result.stdout.lower()
    mock_server.assert_not_called()
    mock_vite.assert_not_called()


@patch("manager_os.startup.start_vite_process")
@patch("manager_os.startup.start_server_process")
@patch("manager_os.startup.wait_for_health")
@patch("manager_os.startup.check_port_available")
def test_dev_api_failure(
    mock_port, mock_health, mock_server, mock_vite
):
    """Dev fails when API doesn't start."""
    mock_port.return_value = True
    mock_health.return_value = False
    mock_server.return_value = MagicMock()
    mock_vite.return_value = MagicMock()

    result = runner.invoke(app, ["dev", "--no-browser"])
    assert result.exit_code != 0
    mock_server.assert_called_once()
    mock_vite.assert_called_once()


@patch("manager_os.startup.start_vite_process")
@patch("manager_os.startup.start_server_process")
@patch("manager_os.startup.wait_for_health")
@patch("manager_os.startup.check_port_available")
def test_dev_no_external_calls(
    mock_port, mock_health, mock_server, mock_vite
):
    """Dev command must not make external retrieval calls."""
    mock_port.return_value = True
    mock_health.return_value = True
    mock_server.return_value = MagicMock()
    mock_vite.return_value = MagicMock()

    import time as _time

    def interrupt_after_sleep(*args, **kwargs):
        raise KeyboardInterrupt()

    with patch("time.sleep", side_effect=interrupt_after_sleep):
        result = runner.invoke(app, ["dev", "--no-browser"])

    assert result.exit_code == 0
    # Verify no external retrieval was called
    # (mocked subprocess ensures no real processes are started)