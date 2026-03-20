"""Registry authentication abstraction for mlody-image-builder.

The RegistryAuth Protocol is the stable interface. DockerConfigAuth is the
default implementation. Future implementations (vault, explicit credentials)
implement the same protocol without changing callers.

Credentials must never appear in log output or JSON error payloads.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class RegistryAuth(Protocol):
    """Abstraction over registry credential sources."""

    def env_vars(self) -> dict[str, str]:
        """Return environment variables to pass to the push subprocess.

        The returned dict must not be logged or included in any output.
        """
        ...


class DockerConfigAuth:
    """Reads credentials from ~/.docker/config.json.

    Passes DOCKER_CONFIG to the push subprocess so that credential helpers
    and base64-encoded auths are resolved by the OCI tooling itself.
    Credentials are never read or echoed by this class.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        self._config_dir = (
            config_path.parent
            if config_path is not None
            else Path.home() / ".docker"
        )

    def env_vars(self) -> dict[str, str]:
        """Return DOCKER_CONFIG pointing to the docker config directory."""
        return {"DOCKER_CONFIG": str(self._config_dir)}
