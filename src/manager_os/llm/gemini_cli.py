"""Gemini CLI provider for LLM extraction.

All LLM calls route through the local Gemini CLI binary using Vertex AI
authentication.  No API keys, no OpenAI SDK, no Google genai SDK.

Usage::

    from manager_os.llm.gemini_cli import GeminiCliProvider, is_gemini_available

    if is_gemini_available():
        provider = GeminiCliProvider()
        result = provider.generate(system_prompt="...", user_prompt="...")
        print(result)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Settings
# ─────────────────────────────────────────────────────────────


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


LLM_PROVIDER = _env("MANAGER_OS_LLM_PROVIDER", "gemini_cli")
GEMINI_CLI_BIN = _env("MANAGER_OS_GEMINI_CLI_BIN", "gemini")
GEMINI_CLI_MODEL = _env("MANAGER_OS_GEMINI_CLI_MODEL", "")
GEMINI_CLI_TIMEOUT = int(_env("MANAGER_OS_GEMINI_CLI_TIMEOUT_SECONDS", "120"))
GEMINI_CLI_WORKDIR = _env("MANAGER_OS_GEMINI_CLI_WORKDIR", "")
GEMINI_CLI_ARGS = _env("MANAGER_OS_GEMINI_CLI_ARGS", "")
GEMINI_CLI_YOLO = _env("MANAGER_OS_GEMINI_CLI_YOLO", "false").lower() in ("true", "yes", "1")
GEMINI_CLI_YOLO_ARGS = _env("MANAGER_OS_GEMINI_CLI_YOLO_ARGS", "-y")
LLM_MAX_CANDIDATES = int(_env("MANAGER_OS_LLM_MAX_CANDIDATES", "25"))
LLM_MAX_CHARS_PER_NOTE = int(_env("MANAGER_OS_LLM_MAX_CHARS_PER_NOTE", "6000"))
LLM_ENABLED = _env("MANAGER_OS_LLM_ENABLED", "true").lower() not in ("0", "false", "no", "off")


@dataclass
class DoctorResult:
    provider: str
    gemini_bin: str
    gemini_bin_exists: bool
    gemini_bin_executable: bool
    configured_model: str
    base_args: str
    yolo_enabled: bool
    yolo_args: str
    timeout: int
    workdir: str
    llm_enabled: bool
    workspace_retrieval_enabled: bool = False
    smoke_test_passed: bool | None = None
    smoke_test_error: str = ""
    smoke_test_output: str = ""


# ─────────────────────────────────────────────────────────────
# Availability
# ─────────────────────────────────────────────────────────────


def is_gemini_available() -> bool:
    """Return True if the Gemini CLI binary is findable and LLM is enabled."""
    if not LLM_ENABLED:
        return False
    return shutil.which(GEMINI_CLI_BIN) is not None


class GeminiUnavailable(RuntimeError):
    """Raised when Gemini CLI is not available."""


# ─────────────────────────────────────────────────────────────
# Doctor
# ─────────────────────────────────────────────────────────────


def run_doctor(smoke_test: bool = True, timeout: int = 60) -> DoctorResult:
    """Diagnose the Gemini CLI configuration.

    Args:
        smoke_test: If True, attempt a minimal prompt to verify the binary works.
        timeout: Max seconds for the smoke test.
    """
    gemini_bin = shutil.which(GEMINI_CLI_BIN) or GEMINI_CLI_BIN
    exists = Path(gemini_bin).exists() if gemini_bin else False
    executable = os.access(gemini_bin, os.X_OK) if exists else False

    workdir = GEMINI_CLI_WORKDIR or os.getcwd()
    ws_retrieval = _env("MANAGER_OS_WORKSPACE_RETRIEVAL_ENABLED", "false").lower() in ("true", "yes", "1")

    result = DoctorResult(
        provider=LLM_PROVIDER,
        gemini_bin=gemini_bin,
        gemini_bin_exists=exists,
        gemini_bin_executable=executable,
        configured_model=GEMINI_CLI_MODEL or "(default)",
        base_args=GEMINI_CLI_ARGS,
        yolo_enabled=GEMINI_CLI_YOLO,
        yolo_args=GEMINI_CLI_YOLO_ARGS,
        timeout=GEMINI_CLI_TIMEOUT,
        workdir=workdir,
        llm_enabled=LLM_ENABLED,
        workspace_retrieval_enabled=ws_retrieval,
    )

    if not executable:
        result.smoke_test_passed = False
        result.smoke_test_error = f"Gemini CLI binary not found/executable at {gemini_bin}"
        return result

    if smoke_test:
        try:
            output = _run_gemini(
                system_prompt="You are a helpful assistant. Answer concisely.",
                user_prompt="Say 'ok' and nothing else.",
                timeout=timeout,
            )
            result.smoke_test_output = output[:200]
            result.smoke_test_passed = "ok" in output.lower()
            if not result.smoke_test_passed:
                result.smoke_test_error = f"Unexpected smoke-test response: {output[:200]}"
        except Exception as exc:
            result.smoke_test_passed = False
            result.smoke_test_error = str(exc)

    return result


# ─────────────────────────────────────────────────────────────
# Core generation
# ─────────────────────────────────────────────────────────────


def _run_gemini(
    system_prompt: str,
    user_prompt: str,
    timeout: int = 120,
    extra_args: list[str] | None = None,
) -> str:
    """Invoke Gemini CLI via subprocess (no shell=True)."""
    cmd = [GEMINI_CLI_BIN]
    if GEMINI_CLI_MODEL:
        cmd.extend(["--model", GEMINI_CLI_MODEL])

    # Extra base CLI args
    if GEMINI_CLI_ARGS:
        cmd.extend(GEMINI_CLI_ARGS.split())

    if extra_args:
        cmd.extend(extra_args)

    # Build the full prompt.
    full_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"

    proc = subprocess.run(
        cmd + ["--prompt", full_prompt],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=GEMINI_CLI_WORKDIR or None,
    )

    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        raise RuntimeError(
            f"Gemini CLI exited with code {proc.returncode}: {stderr}"
        )

    return proc.stdout.strip()


def generate(
    system_prompt: str,
    user_prompt: str,
    timeout: int | None = None,
) -> str:
    """Send prompts to Gemini CLI and return the response text.

    Args:
        system_prompt: System-level instruction.
        user_prompt: The user message / note content.
        timeout: Override default timeout (seconds).

    Returns:
        Raw text output from Gemini.

    Raises:
        GeminiUnavailable: If the binary is not found or LLM is disabled.
        RuntimeError: If the subprocess exits with a non-zero code.
        subprocess.TimeoutExpired: If the process exceeds the timeout.
    """
    if not LLM_ENABLED:
        raise GeminiUnavailable("LLM extraction is disabled (MANAGER_OS_LLM_ENABLED=false)")

    if not is_gemini_available():
        raise GeminiUnavailable(
            f"Gemini CLI binary '{GEMINI_CLI_BIN}' not found. "
            f"Set MANAGER_OS_GEMINI_CLI_BIN or install the Gemini CLI."
        )

    effective_timeout = timeout if timeout is not None else GEMINI_CLI_TIMEOUT
    return _run_gemini(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        timeout=effective_timeout,
    )


# ─────────────────────────────────────────────────────────────
# JSON helpers
# ─────────────────────────────────────────────────────────────


def _extract_json(raw: str) -> str:
    """Extract a JSON array or object from a raw LLM response.

    Handles:
        - Plain JSON
        - Markdown-fenced JSON (```json ... ```)
        - JSON inside arbitrary text (best-effort)
    """
    text = raw.strip()

    # Strip markdown fences
    if text.startswith("```"):
        lines = text.splitlines()
        # Remove opening fence
        if len(lines) > 1:
            lines = lines[1:]
        # Remove closing fence
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Try direct parse first
    try:
        json.loads(text)
        return text
    except (json.JSONDecodeError, ValueError):
        pass

    # Try to find [...]  or  {...}  boundaries
    for seek_char, pair_char in [("[", "]"), ("{", "}")]:
        start = text.find(seek_char)
        end = text.rfind(pair_char)
        if start != -1 and end > start:
            candidate = text[start: end + 1]
            try:
                json.loads(candidate)
                return candidate
            except (json.JSONDecodeError, ValueError):
                continue

    raise ValueError(f"Could not extract valid JSON from response: {raw[:300]}")


def parse_json_response(raw: str) -> list[dict] | dict:
    """Parse a Gemini response as JSON.

    Returns a list (JSON array) or dict (JSON object).

    Raises ValueError if parsing fails.
    """
    clean = _extract_json(raw)
    parsed = json.loads(clean)
    if isinstance(parsed, (list, dict)):
        return parsed
    raise ValueError(f"Expected JSON array or object, got {type(parsed).__name__}")
