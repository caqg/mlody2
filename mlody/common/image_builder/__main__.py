"""Entry point for mlody-image-builder."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import click

from mlody.common.image_builder.errors import BuilderError, ExitCode
from mlody.common.image_builder.output import emit_error, emit_success
from mlody.common.image_builder.pipeline import PipelineInputs, run

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

EXIT_CODE_HELP = """\
Exit codes:
  0  Success -- image built and pushed.
  1  Unexpected error (unhandled exception).
  2  Clone failure -- git remote resolution or shallow clone failed.
  3  Build failure -- `bazel build @dynamic_image//:image` failed.
  4  Push failure -- image push to registry failed.
"""


@click.command(
    name="mlody-image-builder",
    epilog=EXIT_CODE_HELP,
)
@click.argument("targets", nargs=-1, required=True)
@click.option(
    "--sha",
    required=True,
    help="Full 40-digit hexadecimal commit SHA.",
)
@click.option(
    "--registry",
    required=True,
    help=(
        "Container registry destination "
        "(e.g. registry.example.com/mlody)."
    ),
)
@click.option(
    "--remote",
    default=None,
    help=(
        "Git remote URL override. Defaults to "
        "`git remote get-url origin` in the current working directory."
    ),
)
def main(
    targets: tuple[str, ...],
    sha: str,
    registry: str,
    remote: str | None,
) -> None:
    """Build and push an OCI image from Bazel targets at a pinned commit SHA.

    TARGETS: One or more Bazel target labels (e.g. //mlody/lsp:lsp_server).
    """
    if not _SHA_RE.match(sha):
        click.echo(
            _json_error("validation", f"--sha must be a 40-digit hex string, got: {sha!r}"),
            file=sys.stdout,
        )
        sys.exit(ExitCode.CLONE_FAILURE)

    inputs = PipelineInputs(
        targets=list(targets),
        sha=sha,
        registry=registry,
        remote=remote,
        cwd=Path.cwd(),
        cache_root=None,
        auth=None,
    )

    try:
        result = run(inputs)
        emit_success(result)
        sys.exit(ExitCode.SUCCESS)
    except BuilderError as exc:
        emit_error(
            error_type=type(exc).__name__,
            message=exc.message,
            context=exc.context,
        )
        sys.exit(exc.exit_code)
    except Exception as exc:  # noqa: BLE001
        emit_error(
            error_type="UnexpectedError",
            message=str(exc),
            context={},
        )
        sys.exit(1)


def _json_error(error_type: str, message: str) -> str:
    """Serialize a validation error to a JSON string for stdout."""
    return json.dumps({"error": error_type, "message": message}, indent=2)


if __name__ == "__main__":
    main()
