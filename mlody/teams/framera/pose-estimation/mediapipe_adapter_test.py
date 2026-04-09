"""Tests for MediaPipe adapter compatibility helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from mediapipe_adapter import MediaPipeTracker, _try_load_mediapipe_solutions


def test_try_load_mediapipe_solutions_uses_top_level_module_when_available() -> None:
    fake_solutions = SimpleNamespace(name="top-level")
    fake_mp = SimpleNamespace(solutions=fake_solutions)

    with patch("mediapipe_adapter._import_mediapipe", return_value=fake_mp):
        solutions = _try_load_mediapipe_solutions()

    assert solutions is fake_solutions


def test_try_load_mediapipe_solutions_returns_none_when_missing() -> None:
    fake_mp = SimpleNamespace()

    with patch("mediapipe_adapter._import_mediapipe", return_value=fake_mp):
        solutions = _try_load_mediapipe_solutions()

    assert solutions is None


def test_tracker_raises_clear_error_when_tasks_only_and_model_missing() -> None:
    tracker = MediaPipeTracker()

    with patch(
        "mediapipe_adapter._try_load_mediapipe_solutions",
        return_value=None,
    ):
        with pytest.raises(RuntimeError, match="--holistic-model"):
            tracker.__enter__()


def test_tracker_requires_hand_model_for_split_tasks_hands() -> None:
    tracker = MediaPipeTracker(
        face_model_path="face.task",
        pose_model_path="pose.task",
        hands_enabled=True,
    )

    with patch(
        "mediapipe_adapter._try_load_mediapipe_solutions",
        return_value=None,
    ):
        with pytest.raises(RuntimeError, match="--hand-model"):
            tracker.__enter__()
