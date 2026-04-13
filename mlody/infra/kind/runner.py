"""Subprocess wrapper for kind/docker/kubectl invocations.

All external process calls in kind_cluster.py go through RunnerProtocol so
tests can inject a mock without touching subprocess at all.
"""

from __future__ import annotations

import subprocess
from typing import Protocol, runtime_checkable


@runtime_checkable
class RunnerProtocol(Protocol):
    """Structural protocol satisfied by SubprocessRunner, DryRunRunner, and test doubles."""

    def run(self, cmd: list[str]) -> int:
        """Execute *cmd*, return its exit code."""
        ...

    def run_output(self, cmd: list[str]) -> str:
        """Execute *cmd*, capture and return stdout.

        Raises RuntimeError if the command exits with a non-zero code.
        """
        ...

    def run_with_stdin(self, cmd: list[str], stdin: str) -> int:
        """Execute *cmd* with *stdin* piped as its standard input; return exit code."""
        ...

    def check_connected(self, container: str, network: str) -> bool:
        """Return True if *container* is connected to *network* in Docker."""
        ...


class SubprocessRunner:
    """Routes all calls to real subprocesses.

    verbose=True causes each command to be printed to stdout before execution.
    """

    def __init__(self, *, verbose: bool = False) -> None:
        self._verbose = verbose

    def _maybe_echo(self, cmd: list[str]) -> None:
        if self._verbose:
            print(" ".join(cmd))

    def run(self, cmd: list[str]) -> int:
        self._maybe_echo(cmd)
        result = subprocess.run(cmd)
        return result.returncode

    def run_output(self, cmd: list[str]) -> str:
        self._maybe_echo(cmd)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"Command failed (exit {result.returncode}): {' '.join(cmd)}\n{result.stderr.strip()}"
            )
        return result.stdout

    def run_with_stdin(self, cmd: list[str], stdin: str) -> int:
        self._maybe_echo(cmd)
        result = subprocess.run(cmd, input=stdin, text=True)
        return result.returncode

    def check_connected(self, container: str, network: str) -> bool:
        # `docker inspect` returns a JSON array; parsing for the network name
        # is more robust than grepping raw output, but here we rely on the
        # exit code of a targeted format query to stay dependency-free.
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                f"{{{{.NetworkSettings.Networks.{network}}}}}",
                container,
            ],
            capture_output=True,
            text=True,
        )
        # An empty/null result means the container is not on that network.
        return result.returncode == 0 and result.stdout.strip() not in ("", "<nil>")


class DryRunRunner:
    """Prints commands prefixed with [DRY RUN] and returns no-op success values.

    Satisfies RunnerProtocol — used when --dry-run is passed.
    """

    def run(self, cmd: list[str]) -> int:
        print(f"[DRY RUN] {' '.join(cmd)}")
        return 0

    def run_output(self, cmd: list[str]) -> str:
        print(f"[DRY RUN] {' '.join(cmd)}")
        return ""

    def run_with_stdin(self, cmd: list[str], stdin: str) -> int:
        print(f"[DRY RUN] {' '.join(cmd)}")
        return 0

    def check_connected(self, container: str, network: str) -> bool:
        print(f"[DRY RUN] docker inspect (check_connected {container!r} -> {network!r})")
        return False
