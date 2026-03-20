"""Exit codes and typed error hierarchy for mlody-image-builder."""

from __future__ import annotations

import enum


class ExitCode(enum.IntEnum):
    SUCCESS = 0
    CLONE_FAILURE = 2
    BUILD_FAILURE = 3
    PUSH_FAILURE = 4


class BuilderError(Exception):
    """Base class for all pipeline errors.

    Carries the exit code and a human-readable message. Subclasses add
    structured context (e.g. affected targets, stderr from subprocess).
    """

    exit_code: ExitCode
    message: str
    context: dict[str, object]

    def __init__(
        self,
        message: str,
        exit_code: ExitCode,
        **context: object,
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.message = message
        self.context = context


class CloneError(BuilderError):
    def __init__(self, message: str, **context: object) -> None:
        super().__init__(message, ExitCode.CLONE_FAILURE, **context)


class BazelBuildError(BuilderError):
    def __init__(self, message: str, **context: object) -> None:
        super().__init__(message, ExitCode.BUILD_FAILURE, **context)


class PushError(BuilderError):
    def __init__(self, message: str, **context: object) -> None:
        super().__init__(message, ExitCode.PUSH_FAILURE, **context)
