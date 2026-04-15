"""CLI entrypoint for Sonora Chatterbox Turbo speech generation."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click

from mlody.teams.sonora.chatterbox.runtime import (
    DEFAULT_DEVICE,
    ChatterboxConfig,
    run_once,
    run_stdin,
)


@click.command()
@click.argument("text", required=False)
@click.option(
    "--file",
    "output_file",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Write WAV output to this path instead of live playback.",
)
@click.option(
    "--audio-prompt",
    "audio_prompt_path",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    default=None,
    help="Optional reference audio clip for voice cloning (~10s WAV).",
)
@click.option(
    "--device",
    type=str,
    default=DEFAULT_DEVICE,
    show_default=True,
    help="Torch device (cuda or cpu).",
)
@click.option(
    "--sink",
    type=str,
    default=None,
    help="Optional Pulse sink/device name (paplay only).",
)
def cli(
    *,
    text: str | None,
    output_file: Path | None,
    audio_prompt_path: Path | None,
    device: str,
    sink: str | None,
) -> None:
    """Synthesize text to speech via Chatterbox Turbo."""
    config = ChatterboxConfig(
        device=device,
        audio_prompt_path=audio_prompt_path,
        output_file=output_file,
        sink=sink,
    )
    try:
        if text is not None:
            run_once(config=config, text=text)
            return
        run_stdin(config=config, stream=sys.stdin)
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        raise click.ClickException(str(exc)) from exc


def main() -> None:
    """Execute the CLI."""
    cli(standalone_mode=True)


if __name__ == "__main__":
    main()
