"""Tests for Sonora speak CLI."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from mlody.teams.sonora.speak.speak import cli


def test_cli_uses_one_shot_mode_for_positional_text(tmp_path: Path) -> None:
    runner = CliRunner()

    with (
        patch("mlody.teams.sonora.speak.speak.run_once") as once_mock,
        patch("mlody.teams.sonora.speak.speak.run_stdin") as stdin_mock,
    ):
        result = runner.invoke(
            cli,
            [
                "hello world",
                "--file",
                str(tmp_path / "out.wav"),
            ],
        )

    assert result.exit_code == 0
    once_mock.assert_called_once()
    stdin_mock.assert_not_called()


def test_cli_uses_stdin_mode_when_text_absent() -> None:
    runner = CliRunner()

    with (
        patch("mlody.teams.sonora.speak.speak.run_once") as once_mock,
        patch("mlody.teams.sonora.speak.speak.run_stdin") as stdin_mock,
    ):
        result = runner.invoke(cli, [], input="first\nsecond\n")

    assert result.exit_code == 0
    once_mock.assert_not_called()
    stdin_mock.assert_called_once()
