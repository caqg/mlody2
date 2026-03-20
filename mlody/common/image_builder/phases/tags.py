"""Phase 4: OCI image tag derivation from Bazel labels and commit SHA."""

from __future__ import annotations

import re

_UNSAFE = re.compile(r"[^A-Za-z0-9_.\-]")
_MAX_TAG_LEN = 128
_SHA16_LEN = 16


def _sanitize_label(label: str) -> str:
    """Sanitize a Bazel label into a valid OCI tag component.

    Steps:
    1. Strip leading '//'
    2. Replace ':' with '-'
    3. Replace any character outside [A-Za-z0-9_.-] with '-'
    4. Strip leading '-' or '.' characters to satisfy [A-Za-z0-9_] first-char
       constraint (well-formed Bazel labels starting with '//' will not trigger
       this after step 1, but it is applied defensively)
    """
    s = label.lstrip("/")  # remove leading //
    s = s.replace(":", "-")
    s = _UNSAFE.sub("-", s)
    s = s.lstrip("-.")
    return s


def derive_tag(label: str, sha: str) -> str:
    """Derive a single OCI image tag from a Bazel label and full commit SHA.

    Format: <sanitized-label>-<sha[:16]>
    Maximum length: 128 characters (truncated from left of sanitized label
    prefix if needed to preserve the SHA16 suffix).
    """
    sha16 = sha[:_SHA16_LEN]
    suffix = f"-{sha16}"
    sanitized = _sanitize_label(label)
    max_prefix = _MAX_TAG_LEN - len(suffix)
    if len(sanitized) > max_prefix:
        sanitized = sanitized[:max_prefix]
    return sanitized + suffix


def derive_tags(targets: list[str], sha: str) -> list[str]:
    """Derive one OCI tag per Bazel target label."""
    return [derive_tag(label, sha) for label in targets]
