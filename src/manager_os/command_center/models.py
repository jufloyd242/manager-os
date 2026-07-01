"""Data models for the command center: command specs, parameter specs, run
request/result shapes, and risk enums.

Pydantic v2 models, consistent with the rest of the codebase (see schemas.py).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class RiskLevel(str, Enum):
    """How risky it is to run a command at all."""

    local_safe = "local_safe"
    local_write = "local_write"
    external_bounded = "external_bounded"
    external_high_risk = "external_high_risk"
    blocked = "blocked"


class ExternalCallRisk(str, Enum):
    """How likely a command is to make an external (non-local) call."""

    none = "none"
    possible = "possible"
    likely = "likely"
    high = "high"


ParameterType = Literal["str", "int", "float", "bool", "list"]


class ParameterSpec(BaseModel):
    """A single typed parameter accepted by a registered command."""

    model_config = ConfigDict(frozen=True)

    name: str
    type: ParameterType
    required: bool = False
    default: Any = None
    allowed_values: Optional[tuple[Any, ...]] = None
    help: str = ""


class CommandSpec(BaseModel):
    """Full specification of a single allowlisted, runnable Manager OS command."""

    model_config = ConfigDict(frozen=True)

    command_id: str
    label: str
    description: str
    category: str
    cli_command: str
    risk_level: RiskLevel
    external_call_risk: ExternalCallRisk

    parameters: tuple[ParameterSpec, ...] = ()
    supports_dry_run: bool = False
    supports_print_prompt: bool = False
    requires_confirmation: bool = False
    dry_run_required_before_live: bool = False
    default_timeout_seconds: int = 120

    estimated_prompt_chars: Optional[int] = None
    estimated_input_tokens: Optional[int] = None

    # max_scope bounds the value of `bounded_param` (e.g. --limit or
    # --limit-projects). Enforced by the runner, not just documented.
    max_scope: Optional[int] = None
    bounded_param: Optional[str] = None

    writes_tables: tuple[str, ...] = ()
    reads_tables: tuple[str, ...] = ()

    def parameter_names(self) -> set[str]:
        return {p.name for p in self.parameters}

    def get_parameter(self, name: str) -> Optional[ParameterSpec]:
        for p in self.parameters:
            if p.name == name:
                return p
        return None


class CommandRunRequest(BaseModel):
    """A request to run a registered command, as received from a caller (e.g. API/UI)."""

    command_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False
    confirm: bool = False


class CommandRunResult(BaseModel):
    """Immediate result of validating/building (and, where implemented, executing)
    a CommandRunRequest."""

    command_id: str
    status: Literal["validated", "ok", "error", "rejected"]
    argv: Optional[list[str]] = None
    dry_run: bool = False
    error: Optional[str] = None
    estimated_input_tokens: Optional[int] = None
