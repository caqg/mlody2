"""Phase 5: OCI image push to the target registry using crane.

Uses Option B from the spec: invoke `crane push` as a subprocess against the
OCI image layout directory produced by `bazel build //_dynamic_image:image`.
The image layout is written to bazel-bin/_dynamic_image/image/ by rules_oci.

crane honours DOCKER_CONFIG for authentication, which is set by the RegistryAuth
abstraction. Credentials never appear in logs or error output.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
from pathlib import Path

from mlody.common.image_builder.auth import RegistryAuth
from mlody.common.image_builder.errors import PushError
from mlody.common.image_builder.log import info

# rules_oci writes the OCI image layout to this path relative to the clone dir.
_IMAGE_LAYOUT_RELPATH = Path("bazel-bin") / "_dynamic_image" / "image"


@dataclasses.dataclass(frozen=True)
class PushResult:
    image_digest: str
    image_references: list[str]


def push_image(
    clone_dir: Path,
    registry: str,
    tags: list[str],
    auth: RegistryAuth,
) -> PushResult:
    """Push the built OCI image to the registry with all derived tags.

    Invokes `crane push <image-layout-dir> <registry>:<tag>` for each tag.
    The image digest is extracted from crane's stdout (sha256:... line).
    Credentials are sourced exclusively from auth.env_vars().

    Raises PushError if crane is not on PATH or if any tag push fails.
    """
    image_layout = clone_dir / _IMAGE_LAYOUT_RELPATH
    env = {**os.environ, **auth.env_vars()}
    image_references: list[str] = []
    digest: str | None = None

    for tag in tags:
        reference = f"{registry}:{tag}"
        info("push", tag=tag, registry=registry)

        cmd = ["crane", "push", str(image_layout), reference]
        result = subprocess.run(
            cmd,
            cwd=clone_dir,
            capture_output=True,
            text=True,
            env=env,
        )
        if result.returncode != 0:
            raise PushError(
                f"Push failed for tag {tag}",
                tag=tag,
                registry=registry,
                returncode=result.returncode,
                # Deliberately omit env from error context to protect credentials
            )

        image_references.append(reference)
        # crane push outputs "<ref>@sha256:<digest>" on the last stdout line.
        for line in result.stdout.splitlines():
            line = line.strip()
            if "@sha256:" in line:
                digest = line.split("@sha256:", 1)[1]
                if digest:
                    digest = "sha256:" + digest

    if digest is None:
        raise PushError(
            "Push completed but no image digest was returned",
            image_references=image_references,
        )

    info("push", status="success", digest=digest, references=image_references)
    return PushResult(image_digest=digest, image_references=image_references)
