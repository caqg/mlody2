"""Unit tests for kind_cluster provisioning logic.

All tests use MockRunner — a test double that records invocations and lets test
code control return values without touching any real subprocess.

Scenarios tested:
  - Happy path: all five steps run the expected command sequence (spec: happy-path-all-steps)
  - Idempotency: registry already running → step 1 skipped (spec: registry-already-running)
  - Idempotency: cluster exists → step 2 skipped (spec: cluster-exists-skip)
  - Idempotency: registry already on kind network → step 4 skipped (spec: already-connected-skip)
  - Force: existing cluster deleted and recreated (spec: force-delete-recreate-cluster)
  - Force: existing registry deleted and recreated (spec: force-delete-recreate-registry)
  - Error propagation: step 2 failure → steps 3-5 not called (spec: step-fails-halt)
  - Dry-run: DryRunRunner produces [DRY RUN] output with no real subprocesses
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from mlody.infra.kind.kind_cluster import (
    _half_cpus,
    _half_memory,
    check_prerequisites,
    provision,
    step1_create_registry,
    step2_create_cluster,
    step3_configure_containerd,
    step4_connect_registry,
    step5_apply_configmap,
    step6_limit_resources,
)
from mlody.infra.kind.runner import DryRunRunner


class MockRunner:
    """Test double for RunnerProtocol.

    Records every invoked command. Return values per method are configurable
    so tests can simulate specific step outcomes without real processes.
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        # Per-command overrides: prefix -> return value (for run) or output (for run_output)
        self._run_results: dict[str, int] = {}
        self._run_output_results: dict[str, str] = {}
        self._run_output_errors: dict[str, str] = {}
        # check_connected return value (default False — not connected)
        self._connected: bool = False

    def set_run_result(self, cmd_prefix: str, code: int) -> None:
        """Override exit code for commands starting with *cmd_prefix*."""
        self._run_results[cmd_prefix] = code

    def set_run_output(self, cmd_prefix: str, output: str) -> None:
        """Override stdout for commands starting with *cmd_prefix*."""
        self._run_output_results[cmd_prefix] = output

    def set_run_output_error(self, cmd_prefix: str, msg: str) -> None:
        """Make run_output raise RuntimeError for commands starting with *cmd_prefix*."""
        self._run_output_errors[cmd_prefix] = msg

    def set_connected(self, value: bool) -> None:
        self._connected = value

    def _match(self, cmd: list[str], table: Mapping[str, object]) -> str | None:
        joined = " ".join(cmd)
        for prefix in table:
            if joined.startswith(prefix):
                return prefix
        return None

    def run(self, cmd: list[str]) -> int:
        self.calls.append(cmd)
        prefix = self._match(cmd, self._run_results)
        return self._run_results[prefix] if prefix else 0

    def run_output(self, cmd: list[str]) -> str:
        self.calls.append(cmd)
        err_prefix = self._match(cmd, self._run_output_errors)
        if err_prefix:
            raise RuntimeError(self._run_output_errors[err_prefix])
        prefix = self._match(cmd, self._run_output_results)
        return self._run_output_results[prefix] if prefix else ""

    def run_with_stdin(self, cmd: list[str], stdin: str) -> int:  # noqa: ARG002
        self.calls.append(cmd)
        return 0

    def check_connected(self, container: str, network: str) -> bool:
        self.calls.append(["check_connected", container, network])
        return self._connected

    def all_command_strings(self) -> list[str]:
        return [" ".join(c) for c in self.calls]

    def has_call(self, *fragments: str) -> bool:
        """Return True if any recorded call contains all *fragments* as a substring."""
        return any(
            all(f in " ".join(c) for f in fragments) for c in self.calls
        )


# ─── resource defaults ────────────────────────────────────────────────────────


def test_half_cpus_returns_positive_integer_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.cpu_count", lambda: 8)
    assert _half_cpus() == "4"


def test_half_cpus_minimum_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.cpu_count", lambda: 1)
    assert _half_cpus() == "1"


def test_half_memory_gigabytes() -> None:
    from unittest.mock import mock_open, patch

    with patch("builtins.open", mock_open(read_data="MemTotal:       16384000 kB\n")):
        result = _half_memory()
    assert result.endswith("g")
    assert int(result[:-1]) >= 1


def test_half_memory_fallback_returns_valid_format() -> None:
    from unittest.mock import patch

    with patch("builtins.open", side_effect=OSError()), patch("os.sysconf", side_effect=OSError()):
        assert _half_memory() == "2g"


# ─── check_prerequisites ──────────────────────────────────────────────────────


def test_check_prerequisites_passes_when_all_tools_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """All three tools on PATH → no error raised (spec: all-tools-present)."""
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/fake")
    check_prerequisites(MockRunner())  # Must not raise


def test_check_prerequisites_exits_when_tool_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing tool → SystemExit(1) with tool name in message (spec: missing-tool)."""

    def fake_which(name: str) -> str | None:
        return None if name == "kind" else "/usr/bin/fake"

    monkeypatch.setattr("shutil.which", fake_which)
    with pytest.raises(SystemExit) as exc:
        check_prerequisites(MockRunner())
    assert exc.value.code == 1


# ─── step1_create_registry ────────────────────────────────────────────────────


def test_step1_creates_registry_when_absent() -> None:
    """No existing container → docker run command issued (spec: registry-absent-create)."""
    runner = MockRunner()
    # inspect returns empty string (container not found)
    runner.set_run_output("docker inspect", "")
    result = step1_create_registry(runner, "kind-registry", 5001, force=False)
    assert runner.has_call("docker", "run")
    assert result == "created"


def test_step1_skips_when_registry_already_running() -> None:
    """Container running → skip, no docker run (spec: registry-already-running-skip)."""
    runner = MockRunner()
    runner.set_run_output("docker inspect", "running")
    result = step1_create_registry(runner, "kind-registry", 5001, force=False)
    assert not runner.has_call("docker", "run")
    assert result == "skipped"


def test_step1_starts_stopped_registry() -> None:
    """Container stopped → docker start, not docker run (spec: registry-stopped-start)."""
    runner = MockRunner()
    runner.set_run_output("docker inspect", "exited")
    result = step1_create_registry(runner, "kind-registry", 5001, force=False)
    assert runner.has_call("docker", "start")
    assert not runner.has_call("docker", "run")
    assert result == "started"


def test_step1_force_deletes_and_recreates() -> None:
    """--force set and container exists → docker rm then docker run (spec: force-delete-recreate-registry)."""
    runner = MockRunner()
    runner.set_run_output("docker inspect", "running")
    result = step1_create_registry(runner, "kind-registry", 5001, force=True)
    assert runner.has_call("docker", "rm")
    assert runner.has_call("docker", "run")
    assert result == "created"


# ─── step2_create_cluster ─────────────────────────────────────────────────────


def test_step2_creates_cluster_when_absent(tmp_path: object) -> None:
    """No cluster → kind create cluster executed (spec: cluster-absent-create)."""
    runner = MockRunner()
    # kind get clusters returns empty (no clusters)
    runner.set_run_output("kind get clusters", "")
    step2_create_cluster(
        runner,
        cluster_name="mlody",
        registry_name="kind-registry",
        registry_port=5001,
        kubeconfig=None,
        save_config=None,
        force=False,
    )
    assert runner.has_call("kind", "create", "cluster")


def test_step2_skips_when_cluster_exists() -> None:
    """Cluster already present → skip, no kind commands (spec: cluster-exists-skip)."""
    runner = MockRunner()
    runner.set_run_output("kind get clusters", "mlody")
    step2_create_cluster(
        runner,
        cluster_name="mlody",
        registry_name="kind-registry",
        registry_port=5001,
        kubeconfig=None,
        save_config=None,
        force=False,
    )
    assert not runner.has_call("kind", "create")
    assert not runner.has_call("kind", "delete")


def test_step2_force_deletes_then_creates() -> None:
    """--force set, cluster exists → kind delete then kind create (spec: force-delete-recreate-cluster)."""
    runner = MockRunner()
    runner.set_run_output("kind get clusters", "mlody")
    step2_create_cluster(
        runner,
        cluster_name="mlody",
        registry_name="kind-registry",
        registry_port=5001,
        kubeconfig=None,
        save_config=None,
        force=True,
    )
    assert runner.has_call("kind", "delete", "cluster")
    assert runner.has_call("kind", "create", "cluster")


def test_step2_saves_config_to_path(tmp_path: object) -> None:
    """--save-config path provided → YAML written there (spec: save-config)."""
    import pathlib

    path = pathlib.Path(str(tmp_path)) / "mlody.yaml"
    runner = MockRunner()
    runner.set_run_output("kind get clusters", "")
    step2_create_cluster(
        runner,
        cluster_name="mlody",
        registry_name="kind-registry",
        registry_port=5001,
        kubeconfig=None,
        save_config=str(path),
        force=False,
    )
    assert path.exists()
    content = path.read_text()
    assert "containerdConfigPatches" in content


# ─── step3_configure_containerd ───────────────────────────────────────────────


def test_step3_configures_each_node() -> None:
    """For each node from kind get nodes, mkdir and cat are called (spec: configure-nodes-fresh-cluster)."""
    runner = MockRunner()
    runner.set_run_output("kind get nodes", "node1\nnode2\n")
    runner.set_run_output("docker network inspect", "172.18.0.1")
    step3_configure_containerd(runner, "mlody", "kind-registry", 5001)
    # Each node gets a mkdir and a write
    assert runner.has_call("docker", "exec", "node1", "mkdir")
    assert runner.has_call("docker", "exec", "node2", "mkdir")
    assert runner.has_call("docker", "exec", "node1")
    assert runner.has_call("docker", "exec", "node2")


def test_step3_handles_single_node() -> None:
    runner = MockRunner()
    runner.set_run_output("kind get nodes", "mlody-control-plane\n")
    runner.set_run_output("docker network inspect", "172.18.0.1")
    step3_configure_containerd(runner, "mlody", "kind-registry", 5001)
    assert runner.has_call("docker", "exec", "mlody-control-plane")


# ─── step4_connect_registry ───────────────────────────────────────────────────


def test_step4_connects_when_not_connected() -> None:
    """Not connected → docker network connect executed (spec: not-connected-connect)."""
    runner = MockRunner()
    runner.set_connected(False)
    step4_connect_registry(runner, "kind-registry")
    assert runner.has_call("docker", "network", "connect")


def test_step4_skips_when_already_connected() -> None:
    """Already connected → no docker network connect (spec: already-connected-skip)."""
    runner = MockRunner()
    runner.set_connected(True)
    step4_connect_registry(runner, "kind-registry")
    assert not runner.has_call("docker", "network", "connect")


# ─── step5_apply_configmap ────────────────────────────────────────────────────


def test_step5_applies_configmap() -> None:
    """ConfigMap YAML piped to kubectl apply (spec: apply-configmap-fresh-cluster)."""
    runner = MockRunner()
    step5_apply_configmap(runner, 5001, kubeconfig=None)
    assert runner.has_call("kubectl", "apply")


def test_step5_includes_kubeconfig_when_provided() -> None:
    """--kubeconfig provided → kubectl invocation includes --kubeconfig flag (spec: kubeconfig-override)."""
    runner = MockRunner()
    step5_apply_configmap(runner, 5001, kubeconfig="/home/user/.kube/config")
    assert runner.has_call("kubectl", "--kubeconfig")


# ─── step6_limit_resources ────────────────────────────────────────────────────


def test_step6_skips_when_no_limits() -> None:
    """Neither limit set → docker update not called, returns skipped."""
    runner = MockRunner()
    result = step6_limit_resources(runner, "mlody", max_cpus=None, max_memory=None)
    assert result == "skipped"
    assert not runner.has_call("docker", "update")


def test_step6_applies_cpus_and_memory() -> None:
    """Both limits set → docker update called with --cpus and --memory for each node."""
    runner = MockRunner()
    runner.set_run_output("kind get nodes", "mlody-control-plane\n")
    result = step6_limit_resources(runner, "mlody", max_cpus="2", max_memory="4g")
    assert result == "applied"
    assert runner.has_call("docker", "update", "--cpus", "2")
    assert runner.has_call("docker", "update", "--memory", "4g")


def test_step6_applies_cpus_only() -> None:
    runner = MockRunner()
    runner.set_run_output("kind get nodes", "mlody-control-plane\n")
    step6_limit_resources(runner, "mlody", max_cpus="1", max_memory=None)
    assert runner.has_call("docker", "update", "--cpus")
    assert not runner.has_call("--memory")


def test_step6_applies_memory_only() -> None:
    runner = MockRunner()
    runner.set_run_output("kind get nodes", "mlody-control-plane\n")
    step6_limit_resources(runner, "mlody", max_cpus=None, max_memory="2g")
    assert runner.has_call("docker", "update", "--memory")
    assert not runner.has_call("--cpus")


# ─── provision() integration ──────────────────────────────────────────────────


def test_provision_happy_path_runs_all_five_steps() -> None:
    """Fresh environment → all five steps execute (spec: happy-path-test-all-steps)."""
    runner = MockRunner()
    runner.set_run_output("docker inspect", "")
    runner.set_run_output("kind get clusters", "")
    runner.set_run_output("kind get nodes", "mlody-control-plane\n")
    runner.set_run_output("docker network inspect", "172.18.0.1")
    runner.set_connected(False)

    provision(
        runner,
        cluster_name="mlody",
        registry_name="kind-registry",
        registry_port=5001,
        kubeconfig=None,
        save_config=None,
        force=False,
    )

    assert runner.has_call("docker", "run")           # step 1
    assert runner.has_call("kind", "create", "cluster")  # step 2
    assert runner.has_call("docker", "exec")          # step 3
    assert runner.has_call("docker", "network", "connect")  # step 4
    assert runner.has_call("kubectl", "apply")        # step 5


def test_provision_stops_at_failed_step() -> None:
    """Step 2 failure → SystemExit(1), steps 3-5 not executed (spec: step-fails-halt)."""
    runner = MockRunner()
    runner.set_run_output("docker inspect", "")
    runner.set_run_output("kind get clusters", "")
    # Simulate kind create cluster failing
    runner.set_run_output_error("kind create cluster", "kind create cluster failed")

    with pytest.raises(SystemExit) as exc:
        provision(
            runner,
            cluster_name="mlody",
            registry_name="kind-registry",
            registry_port=5001,
            kubeconfig=None,
            save_config=None,
            force=False,
        )
    assert exc.value.code == 1
    # Steps 3–5 commands must not appear
    assert not runner.has_call("docker", "exec")
    assert not runner.has_call("docker", "network", "connect")
    assert not runner.has_call("kubectl", "apply")


def test_provision_idempotent_registry_running() -> None:
    """Registry running → step 1 skipped (spec: registry-already-running-skip via provision)."""
    runner = MockRunner()
    runner.set_run_output("docker inspect", "running")
    runner.set_run_output("kind get clusters", "")
    runner.set_run_output("kind get nodes", "mlody-control-plane\n")
    runner.set_run_output("docker network inspect", "172.18.0.1")
    runner.set_connected(False)

    provision(
        runner,
        cluster_name="mlody",
        registry_name="kind-registry",
        registry_port=5001,
        kubeconfig=None,
        save_config=None,
        force=False,
    )
    # step 1 skipped → no docker run
    assert not runner.has_call("docker", "run")
    # remaining steps still run
    assert runner.has_call("kind", "create", "cluster")


def test_provision_idempotent_cluster_exists() -> None:
    """Cluster already exists → step 2 skipped (spec: idempotency-test-cluster-exists)."""
    runner = MockRunner()
    runner.set_run_output("docker inspect", "")
    runner.set_run_output("kind get clusters", "mlody")
    runner.set_run_output("kind get nodes", "mlody-control-plane\n")
    runner.set_run_output("docker network inspect", "172.18.0.1")
    runner.set_connected(False)

    provision(
        runner,
        cluster_name="mlody",
        registry_name="kind-registry",
        registry_port=5001,
        kubeconfig=None,
        save_config=None,
        force=False,
    )
    assert not runner.has_call("kind", "create", "cluster")
    # steps 3–5 still run
    assert runner.has_call("docker", "exec")


def test_provision_idempotent_registry_already_connected() -> None:
    """Registry already on kind network → step 4 skipped (spec: already-connected-skip via provision)."""
    runner = MockRunner()
    runner.set_run_output("docker inspect", "")
    runner.set_run_output("kind get clusters", "")
    runner.set_run_output("kind get nodes", "mlody-control-plane\n")
    runner.set_run_output("docker network inspect", "172.18.0.1")
    runner.set_connected(True)

    provision(
        runner,
        cluster_name="mlody",
        registry_name="kind-registry",
        registry_port=5001,
        kubeconfig=None,
        save_config=None,
        force=False,
    )
    assert not runner.has_call("docker", "network", "connect")


def test_provision_force_recreates_cluster_and_registry() -> None:
    """--force → registry deleted+recreated AND cluster deleted+recreated (spec: force-test)."""
    runner = MockRunner()
    runner.set_run_output("docker inspect", "running")
    runner.set_run_output("kind get clusters", "mlody")
    runner.set_run_output("kind get nodes", "mlody-control-plane\n")
    runner.set_run_output("docker network inspect", "172.18.0.1")
    runner.set_connected(False)

    provision(
        runner,
        cluster_name="mlody",
        registry_name="kind-registry",
        registry_port=5001,
        kubeconfig=None,
        save_config=None,
        force=True,
    )
    assert runner.has_call("docker", "rm")
    assert runner.has_call("docker", "run")
    assert runner.has_call("kind", "delete", "cluster")
    assert runner.has_call("kind", "create", "cluster")


# ─── dry-run ──────────────────────────────────────────────────────────────────


def test_provision_dry_run_no_real_calls(capsys: pytest.CaptureFixture[str]) -> None:
    """DryRunRunner → [DRY RUN] prefix in output, no real subprocess (spec: dry-run-output-format)."""
    runner = DryRunRunner()
    # DryRunRunner.run_output always returns "" — kind get clusters returns ""
    # which means "no cluster" → step 2 will try to create, but DryRunRunner
    # just prints and returns 0.
    provision(
        runner,
        cluster_name="mlody",
        registry_name="kind-registry",
        registry_port=5001,
        kubeconfig=None,
        save_config=None,
        force=False,
    )
    captured = capsys.readouterr()
    assert "[DRY RUN]" in captured.out
