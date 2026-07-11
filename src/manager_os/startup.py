"""Startup, preflight, doctor, and process lifecycle for Manager OS.

All functions are designed to be testable — side-effectful operations
(subprocess, socket, httpx) are isolated and can be mocked in tests.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import signal
import socket
import subprocess
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


# ---------------------------------------------------------------------------
# Repository root
# ---------------------------------------------------------------------------

def find_repo_root(marker: str = "pyproject.toml") -> Path:
    """Find the repository root by walking up from this file's location."""
    path = Path(__file__).resolve().parent.parent.parent
    if (path / marker).exists():
        return path
    # Fallback: walk up from cwd
    path = Path.cwd().resolve()
    for parent in [path] + list(path.parents):
        if (parent / marker).exists():
            return parent
    raise FileNotFoundError(f"Could not find repository root (marker: {marker})")


# ---------------------------------------------------------------------------
# Python environment
# ---------------------------------------------------------------------------

def resolve_python_environment(repo_root: Path) -> Path:
    """Find the Python interpreter to use.

    Preference order:
    1. .venv/bin/python
    2. python3.13, python3.12, python3.11
    3. python3 (only if >= 3.11)
    """
    venv_python = repo_root / ".venv" / "bin" / "python"
    if venv_python.exists():
        return venv_python

    for ver in ("python3.13", "python3.12", "python3.11"):
        try:
            result = subprocess.run(
                [ver, "--version"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                which = subprocess.run(
                    ["which", ver], capture_output=True, text=True, timeout=5
                )
                if which.returncode == 0:
                    return Path(which.stdout.strip())
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue

    try:
        result = subprocess.run(
            ["python3", "--version"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            version_str = result.stdout.strip() or result.stderr.strip()
            parts = version_str.replace("Python ", "").split(".")
            if len(parts) >= 2 and int(parts[0]) >= 3 and int(parts[1]) >= 11:
                which = subprocess.run(
                    ["which", "python3"], capture_output=True, text=True, timeout=5
                )
                if which.returncode == 0:
                    return Path(which.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    raise RuntimeError(
        "No supported Python interpreter found. "
        "Manager OS requires Python >= 3.11. "
        "Install it via: brew install python@3.12"
    )


def get_python_version(python_path: Path) -> str:
    """Get the version string from a Python interpreter."""
    try:
        result = subprocess.run(
            [str(python_path), "--version"],
            capture_output=True, text=True, timeout=5,
        )
        return (result.stdout or result.stderr).strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return "unknown"


# ---------------------------------------------------------------------------
# Port checking
# ---------------------------------------------------------------------------

def check_port_available(host: str, port: int) -> bool:
    """Check if a port is available for listening."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def check_port_has_manager_os(host: str, port: int) -> bool:
    """Check if a running Manager OS instance is healthy on this port."""
    try:
        resp = httpx.get(f"http://{host}:{port}/api/health", timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("ok") is True and data.get("service") == "manager-os-api"
    except (httpx.RequestError, ValueError, KeyError):
        pass
    return False


# ---------------------------------------------------------------------------
# Health polling
# ---------------------------------------------------------------------------

def wait_for_health(url: str, timeout: float = 30.0, interval: float = 0.5) -> bool:
    """Poll the health endpoint until it responds or timeout expires."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            resp = httpx.get(url, timeout=2)
            if resp.status_code == 200:
                return True
        except (httpx.RequestError, httpx.TimeoutException):
            pass
        time.sleep(interval)
    return False


# ---------------------------------------------------------------------------
# Frontend build detection
# ---------------------------------------------------------------------------

_BUILD_MANIFEST_FILENAME = ".build-manifest.json"

_FRONTEND_SOURCE_GLOBS = [
    "frontend/src/**/*.ts",
    "frontend/src/**/*.tsx",
    "frontend/src/**/*.css",
    "frontend/index.html",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/vite.config.ts",
    "frontend/tsconfig.json",
    "frontend/tsconfig.app.json",
    "frontend/tsconfig.node.json",
    "frontend/tailwind.config.js",
    "frontend/postcss.config.js",
]


def compute_source_fingerprint(repo_root: Path) -> str:
    """Compute a SHA-256 fingerprint of all relevant frontend source files."""
    hasher = hashlib.sha256()
    for pattern in _FRONTEND_SOURCE_GLOBS:
        for path in sorted(repo_root.glob(pattern)):
            if path.is_file():
                try:
                    hasher.update(path.read_bytes())
                except (OSError, PermissionError):
                    pass
    return hasher.hexdigest()


def write_build_manifest(dist_dir: Path, fingerprint: str) -> dict[str, Any]:
    """Write a build manifest to the dist directory."""
    manifest = {
        "schema_version": 1,
        "fingerprint": fingerprint,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "python_version": platform.python_version(),
        "node_version": _get_node_version(),
    }
    dist_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = dist_dir / _BUILD_MANIFEST_FILENAME
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def read_build_manifest(dist_dir: Path) -> dict[str, Any] | None:
    """Read the build manifest from the dist directory."""
    manifest_path = dist_dir / _BUILD_MANIFEST_FILENAME
    if not manifest_path.exists():
        return None
    try:
        return json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def is_build_current(repo_root: Path) -> bool:
    """Check if the frontend build is current based on source fingerprint."""
    dist_dir = repo_root / "frontend" / "dist"
    if not dist_dir.exists() or not (dist_dir / "index.html").exists():
        return False
    manifest = read_build_manifest(dist_dir)
    if manifest is None:
        return False
    current_fingerprint = compute_source_fingerprint(repo_root)
    return manifest.get("fingerprint") == current_fingerprint


def _get_node_version() -> str:
    """Get the installed Node.js version string."""
    try:
        result = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, timeout=5,
        )
        return (result.stdout or result.stderr).strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return "unknown"


# ---------------------------------------------------------------------------
# Frontend build
# ---------------------------------------------------------------------------

def run_frontend_build(repo_root: Path) -> subprocess.CompletedProcess:
    """Run the production frontend build.

    Returns the subprocess result. Raises on timeout.
    """
    frontend_dir = repo_root / "frontend"
    env = os.environ.copy()
    env["CI"] = "true"
    result = subprocess.run(
        ["npm", "run", "build"],
        cwd=str(frontend_dir),
        capture_output=True, text=True,
        timeout=120,
        env=env,
    )
    if result.returncode == 0:
        fingerprint = compute_source_fingerprint(repo_root)
        write_build_manifest(frontend_dir / "dist", fingerprint)
    return result


def run_npm_install(repo_root: Path) -> subprocess.CompletedProcess:
    """Install frontend dependencies.

    Prefers npm ci when package-lock.json exists.
    """
    frontend_dir = repo_root / "frontend"
    lockfile = frontend_dir / "package-lock.json"
    cmd = ["npm", "ci"] if lockfile.exists() else ["npm", "install"]
    return subprocess.run(
        cmd,
        cwd=str(frontend_dir),
        capture_output=True, text=True,
        timeout=120,
    )


def check_frontend_dependencies(repo_root: Path) -> bool:
    """Check if frontend node_modules exists."""
    return (repo_root / "frontend" / "node_modules").exists()


# ---------------------------------------------------------------------------
# Process lifecycle
# ---------------------------------------------------------------------------

def start_server_process(
    repo_root: Path,
    host: str = "127.0.0.1",
    port: int = 8000,
    reload: bool = False,
) -> subprocess.Popen:
    """Start a Uvicorn server process."""
    python_path = resolve_python_environment(repo_root)
    cmd = [
        str(python_path), "-m", "uvicorn",
        "manager_os.api.app:app",
        "--host", host,
        "--port", str(port),
    ]
    if reload:
        cmd.append("--reload")
    return subprocess.Popen(
        cmd,
        cwd=str(repo_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def start_vite_process(repo_root: Path, port: int = 5173) -> subprocess.Popen:
    """Start a Vite dev server process."""
    frontend_dir = repo_root / "frontend"
    env = os.environ.copy()
    env["VITE_MANAGER_OS_API_BASE_URL"] = "http://127.0.0.1:8000"
    return subprocess.Popen(
        ["npx", "vite", "--port", str(port), "--host", "127.0.0.1"],
        cwd=str(frontend_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )


def terminate_process(proc: subprocess.Popen, timeout: float = 5.0) -> None:
    """Terminate a process gracefully, then force-kill if needed."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def terminate_process_tree(proc: subprocess.Popen, timeout: float = 5.0) -> None:
    """Terminate a process and its children."""
    if proc.poll() is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(pgid, signal.SIGKILL)
            proc.wait()
    except (ProcessLookupError, PermissionError, OSError):
        terminate_process(proc, timeout)


# ---------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------

def open_browser(url: str) -> None:
    """Open the dashboard URL in the default browser."""
    webbrowser.open(url)


# ---------------------------------------------------------------------------
# Doctor checks
# ---------------------------------------------------------------------------

class DoctorCheck:
    """A single doctor check result."""

    def __init__(self, name: str, status: str, message: str = "", detail: str = ""):
        self.name = name
        self.status = status  # "PASS", "WARN", "FAIL"
        self.message = message
        self.detail = detail

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "detail": self.detail,
        }


class DoctorReport:
    """Aggregated doctor report."""

    def __init__(self, checks: list[DoctorCheck]):
        self.checks = checks

    @property
    def all_pass(self) -> bool:
        return all(c.status != "FAIL" for c in self.checks)

    @property
    def exit_code(self) -> int:
        return 0 if self.all_pass else 1

    @property
    def warnings(self) -> int:
        return sum(1 for c in self.checks if c.status == "WARN")

    @property
    def failures(self) -> int:
        return sum(1 for c in self.checks if c.status == "FAIL")

    def to_dict(self) -> dict[str, Any]:
        return {
            "checks": [c.to_dict() for c in self.checks],
            "all_pass": self.all_pass,
            "exit_code": self.exit_code,
            "warnings": self.warnings,
            "failures": self.failures,
        }


def run_doctor(repo_root: Path | None = None) -> DoctorReport:
    """Run all doctor checks and return a report."""
    if repo_root is None:
        try:
            repo_root = find_repo_root()
        except FileNotFoundError:
            repo_root = Path.cwd()

    checks: list[DoctorCheck] = []

    # Repository root
    if (repo_root / "pyproject.toml").exists():
        checks.append(DoctorCheck("Repository root", "PASS", str(repo_root)))
    else:
        checks.append(
            DoctorCheck("Repository root", "FAIL", "Not found", str(repo_root))
        )

    # Python version
    try:
        python_path = resolve_python_environment(repo_root)
        version = get_python_version(python_path)
        checks.append(DoctorCheck("Python", "PASS", version))
    except RuntimeError as exc:
        checks.append(DoctorCheck("Python", "FAIL", str(exc)))

    # Virtual environment
    venv_path = repo_root / ".venv"
    if venv_path.exists() and (venv_path / "bin" / "python").exists():
        checks.append(DoctorCheck("Virtual environment", "PASS"))
    else:
        checks.append(
            DoctorCheck(
                "Virtual environment",
                "FAIL",
                "Not found. Run: python -m venv .venv",
            )
        )

    # Backend dependencies
    egg_link = repo_root / "src" / "manager_os.egg-info"
    if egg_link.exists() or (repo_root / ".venv").exists():
        checks.append(DoctorCheck("Backend dependencies", "PASS"))
    else:
        checks.append(
            DoctorCheck(
                "Backend dependencies",
                "WARN",
                "Not installed. Run: pip install -e .",
            )
        )

    # Uvicorn availability
    try:
        import uvicorn  # noqa: F401
        checks.append(DoctorCheck("Uvicorn", "PASS"))
    except ImportError:
        checks.append(DoctorCheck("Uvicorn", "FAIL", "Not installed"))

    # Node.js
    try:
        result = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            node_ver = (result.stdout or result.stderr).strip()
            checks.append(DoctorCheck("Node.js", "PASS", node_ver))
        else:
            checks.append(DoctorCheck("Node.js", "FAIL", "Not found"))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        checks.append(DoctorCheck("Node.js", "FAIL", "Not found"))

    # npm
    try:
        result = subprocess.run(
            ["npm", "--version"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            npm_ver = (result.stdout or result.stderr).strip()
            checks.append(DoctorCheck("npm", "PASS", npm_ver))
        else:
            checks.append(DoctorCheck("npm", "FAIL", "Not found"))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        checks.append(DoctorCheck("npm", "FAIL", "Not found"))

    # Frontend dependencies
    if check_frontend_dependencies(repo_root):
        checks.append(DoctorCheck("Frontend dependencies", "PASS"))
    else:
        checks.append(
            DoctorCheck(
                "Frontend dependencies",
                "WARN",
                "Not installed. Run: cd frontend && npm install",
            )
        )

    # React build
    dist_dir = repo_root / "frontend" / "dist"
    if (dist_dir / "index.html").exists():
        if is_build_current(repo_root):
            checks.append(DoctorCheck("React build", "PASS", "Build is current"))
        else:
            checks.append(
                DoctorCheck(
                    "React build", "WARN", "Build is stale. Run: ./manager-os build"
                )
            )
    else:
        checks.append(
            DoctorCheck(
                "React build", "WARN", "Not built. Run: ./manager-os build"
            )
        )

    # .env file
    env_file = repo_root / ".env"
    if env_file.exists():
        checks.append(DoctorCheck(".env file", "PASS"))
    else:
        checks.append(
            DoctorCheck(
                ".env file",
                "WARN",
                "Not found. Copy from .env.example and edit",
            )
        )

    # Settings loadability
    try:
        from manager_os.config import get_settings
        get_settings()
        checks.append(DoctorCheck("Settings", "PASS"))
    except Exception as exc:
        checks.append(DoctorCheck("Settings", "FAIL", str(exc)))

    # Database directory writability
    try:
        from manager_os.config import get_settings
        settings = get_settings()
        db_path = Path(settings.db_path)
        db_dir = db_path.parent if db_path.suffix else db_path
        db_dir.mkdir(parents=True, exist_ok=True)
        test_file = db_dir / ".write_test"
        test_file.write_text("test")
        test_file.unlink()
        checks.append(DoctorCheck("Database directory", "PASS"))
    except Exception as exc:
        checks.append(DoctorCheck("Database directory", "FAIL", str(exc)))

    # Obsidian vault
    try:
        from manager_os.config import get_settings
        settings = get_settings()
        if settings.vault_path:
            vault = Path(settings.vault_path)
            if vault.exists():
                checks.append(DoctorCheck("Obsidian vault", "PASS"))
            else:
                checks.append(
                    DoctorCheck(
                        "Obsidian vault",
                        "WARN",
                        f"Path not found: {settings.vault_path}",
                    )
                )
        else:
            checks.append(DoctorCheck("Obsidian vault", "WARN", "Not configured"))
    except Exception:
        checks.append(DoctorCheck("Obsidian vault", "WARN", "Could not check"))

    # Deals CSV
    try:
        from manager_os.config import get_settings
        settings = get_settings()
        if settings.deals_csv:
            deals_path = Path(settings.deals_csv)
            if not deals_path.is_absolute():
                deals_path = repo_root / deals_path
            if deals_path.exists():
                checks.append(DoctorCheck("Deals source", "PASS"))
            else:
                checks.append(
                    DoctorCheck(
                        "Deals source", "WARN", f"Not found: {settings.deals_csv}"
                    )
                )
        else:
            checks.append(DoctorCheck("Deals source", "WARN", "Not configured"))
    except Exception:
        checks.append(DoctorCheck("Deals source", "WARN", "Could not check"))

    # Forecast CSV
    try:
        from manager_os.config import get_settings
        settings = get_settings()
        if settings.forecast_csv:
            forecast_path = Path(settings.forecast_csv)
            if not forecast_path.is_absolute():
                forecast_path = repo_root / forecast_path
            if forecast_path.exists():
                checks.append(DoctorCheck("Forecast source", "PASS"))
            else:
                checks.append(
                    DoctorCheck(
                        "Forecast source",
                        "WARN",
                        f"Not found: {settings.forecast_csv}",
                    )
                )
        else:
            checks.append(DoctorCheck("Forecast source", "WARN", "Not configured"))
    except Exception:
        checks.append(DoctorCheck("Forecast source", "WARN", "Could not check"))

    # Workspace summary directory
    try:
        from manager_os.config import get_settings
        settings = get_settings()
        if settings.workspace_summary_dir:
            summary_path = Path(settings.workspace_summary_dir)
            if not summary_path.is_absolute():
                summary_path = repo_root / summary_path
            if summary_path.exists():
                checks.append(DoctorCheck("Workspace summary dir", "PASS"))
            else:
                checks.append(
                    DoctorCheck(
                        "Workspace summary dir",
                        "WARN",
                        f"Not found: {settings.workspace_summary_dir}",
                    )
                )
        else:
            checks.append(
                DoctorCheck("Workspace summary dir", "WARN", "Not configured")
            )
    except Exception:
        checks.append(
            DoctorCheck("Workspace summary dir", "WARN", "Could not check")
        )

    # Port availability
    if check_port_available("127.0.0.1", 8000):
        checks.append(DoctorCheck("Port 8000", "PASS", "Available"))
    else:
        if check_port_has_manager_os("127.0.0.1", 8000):
            checks.append(
                DoctorCheck(
                    "Port 8000", "PASS", "Manager OS already running"
                )
            )
        else:
            checks.append(
                DoctorCheck(
                    "Port 8000",
                    "FAIL",
                    "Already in use by another process",
                )
            )

    return DoctorReport(checks)