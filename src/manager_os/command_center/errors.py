"""Exception types for the command center registry/runner.

All errors carry clear, actionable messages — callers should never hit a bare
KeyError or an unhelpful traceback when misusing the registry or runner.
"""

from __future__ import annotations


class CommandCenterError(Exception):
    """Base class for all command center errors."""


class CommandNotFoundError(CommandCenterError):
    """Raised when a command_id is not registered."""

    def __init__(self, command_id: str) -> None:
        self.command_id = command_id
        super().__init__(
            f"Unknown command_id {command_id!r}. "
            "See manager_os.command_center.registry.list_command_ids() for the "
            "list of available commands."
        )


class CommandBlockedError(CommandCenterError):
    """Raised when attempting to validate or build argv for a blocked command."""

    def __init__(self, command_id: str) -> None:
        self.command_id = command_id
        super().__init__(
            f"Command {command_id!r} is risk_level=blocked and cannot be run "
            "from the command center."
        )


class ConfirmationRequiredError(CommandCenterError):
    """Raised when a live run of a confirmation-required command lacks confirmation."""

    def __init__(self, command_id: str) -> None:
        self.command_id = command_id
        super().__init__(
            f"Command {command_id!r} requires explicit confirmation before "
            "running live (pass confirmed=True)."
        )


class DryRunRequiredError(CommandCenterError):
    """Raised when a live run requires a prior dry-run attestation."""

    def __init__(self, command_id: str) -> None:
        self.command_id = command_id
        super().__init__(
            f"Command {command_id!r} requires a dry run before it can be run live."
        )


class InvalidArgumentError(CommandCenterError):
    """Raised for unknown parameters, missing required parameters, or wrong types."""


class ScopeExceededError(CommandCenterError):
    """Raised when a bounded parameter (e.g. --limit, --limit-projects) exceeds its max."""
