"""Runtime helpers for the Sonora Chatterbox Turbo speech tool."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

DEFAULT_DEVICE = "cuda"


@dataclass(frozen=True)
class ChatterboxConfig:
    """Runtime configuration for one invocation."""

    device: str
    audio_prompt_path: Path | None
    output_file: Path | None
    sink: str | None


class ChatterboxSpeaker:
    """Synthesizes speech and handles playback/file output."""

    def __init__(self, *, config: ChatterboxConfig) -> None:
        self._config = config
        self._model = _load_model(device=config.device)
        self._sample_rate: int = self._model.sr
        self._playback_program = _resolve_playback_program() if config.output_file is None else None

    def synthesize_text(self, text: str) -> Any:
        """Return synthesized audio tensor for one text segment."""
        stripped = text.strip()
        if not stripped:
            return None
        audio_prompt = (
            str(self._config.audio_prompt_path)
            if self._config.audio_prompt_path is not None
            else None
        )
        return self._model.generate(stripped, audio_prompt_path=audio_prompt)

    def write_wav(self, *, path: Path, audio: Any) -> None:
        """Write a WAV file from a torchaudio-compatible tensor."""
        import torchaudio

        path.parent.mkdir(parents=True, exist_ok=True)
        torchaudio.save(str(path), audio, self._sample_rate)

    def play_audio(self, *, audio: Any) -> None:
        """Play audio through paplay/aplay."""
        if self._playback_program is None:
            raise RuntimeError("playback is unavailable when output_file mode is enabled")

        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix="sonora_cb_", suffix=".wav", delete=False) as tmp:
                temp_path = Path(tmp.name)
            self.write_wav(path=temp_path, audio=audio)
            command = _build_playback_command(
                program=self._playback_program,
                wav_path=temp_path,
                sink=self._config.sink,
            )
            subprocess.run(command, check=True)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()


def run_once(*, config: ChatterboxConfig, text: str) -> None:
    """Run one-shot synthesis mode."""
    speaker = ChatterboxSpeaker(config=config)
    audio = speaker.synthesize_text(text)
    if audio is None:
        return
    if config.output_file is not None:
        speaker.write_wav(path=config.output_file, audio=audio)
        return
    speaker.play_audio(audio=audio)


def run_stdin(*, config: ChatterboxConfig, stream: TextIO) -> None:
    """Run line-by-line stdin mode until EOF."""
    import torch

    speaker = ChatterboxSpeaker(config=config)
    if config.output_file is not None:
        chunks: list[Any] = []
        for line in stream:
            audio = speaker.synthesize_text(line.rstrip("\n"))
            if audio is not None:
                chunks.append(audio)
        if chunks:
            full_audio = torch.cat(chunks, dim=-1)
            speaker.write_wav(path=config.output_file, audio=full_audio)
        return

    for line in stream:
        audio = speaker.synthesize_text(line.rstrip("\n"))
        if audio is not None:
            speaker.play_audio(audio=audio)


def _patch_ml_dtypes() -> None:
    """onnx>=1.16 accesses ml_dtypes.float4_e2m1fn at import time; installs
    older than ml_dtypes 0.5.0 lack it. Inject a stand-in so onnx loads.
    float4 tensors are never used in English TTS."""
    try:
        import ml_dtypes  # noqa: PLC0415

        if not hasattr(ml_dtypes, "float4_e2m1fn"):
            import numpy as np  # noqa: PLC0415

            ml_dtypes.float4_e2m1fn = np.dtype("float16")  # type: ignore[attr-defined]
    except ImportError:
        pass


def _load_model(device: str) -> Any:
    _patch_ml_dtypes()
    import perth

    if getattr(perth, "PerthImplicitWatermarker", None) is None:
        class _DummyWatermarker:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

            def apply_watermark(self, wav: Any, sample_rate: int) -> Any:
                return wav

            def get_watermark(self, wav: Any, sample_rate: int) -> None:
                return None

        perth.PerthImplicitWatermarker = _DummyWatermarker  # type: ignore[attr-defined]

    from chatterbox.tts_turbo import ChatterboxTurboTTS

    return ChatterboxTurboTTS.from_pretrained(device=device)


def _resolve_playback_program() -> str:
    if shutil.which("paplay") is not None:
        return "paplay"
    if shutil.which("aplay") is not None:
        return "aplay"
    raise RuntimeError("no playback program found (expected paplay or aplay)")


def _build_playback_command(*, program: str, wav_path: Path, sink: str | None) -> list[str]:
    if program == "paplay":
        command = [program]
        if sink:
            command.extend(["--device", sink])
        command.append(str(wav_path))
        return command
    if program == "aplay":
        return [program, str(wav_path)]
    raise RuntimeError(f"unsupported playback program: {program}")
