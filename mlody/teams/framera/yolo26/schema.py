"""JSONL frame contracts for Framera YOLOv26 detection."""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Detection:
    """One object detection in image-space coordinates."""

    index: int
    class_id: int
    class_name: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe detection payload."""
        return {
            "index": self.index,
            "class_id": self.class_id,
            "class_name": self.class_name,
            "confidence": self.confidence,
            "x1": self.x1,
            "y1": self.y1,
            "x2": self.x2,
            "y2": self.y2,
        }


@dataclass(frozen=True)
class FramePacket:
    """One emitted stdout frame."""

    timestamp_ms: int
    frame_width: int
    frame_height: int
    task: str
    status: str
    model: str
    detections: tuple[Detection, ...]

    def to_dict(self) -> dict[str, object]:
        """Return the packet as a JSON-safe mapping."""
        return {
            "timestamp_ms": self.timestamp_ms,
            "frame_width": self.frame_width,
            "frame_height": self.frame_height,
            "task": self.task,
            "status": self.status,
            "model": self.model,
            "detections": [detection.to_dict() for detection in self.detections],
        }

    def to_json_line(self) -> str:
        """Serialize the packet as one JSONL line."""
        return json.dumps(self.to_dict(), separators=(",", ":"))
