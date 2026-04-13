# Requirements Document: mlody Kind Cluster Provisioner

**Version:** 1.0 **Date:** 2026-04-13 **Prepared by:** Requirements Analyst AI
(@socrates) **Status:** Draft

---

## 1. Executive Summary

The mlody project requires a local Kubernetes development environment that
mirrors the components and networking assumptions of its target production stack
(Grafana, Argo Workflows, Kubeflow, etc.). Today, developers must manually
assemble a kind cluster, a local Docker registry, and the plumbing that connects
them — a multi-step, error-prone process with no canonical reference
implementation.

This project delivers a single Python script
(`mlody/infra/kind/kind-cluster.py`) with a Bazel target
(`//mlody/infra/kind:o-mlody-cluster`) that provisions a fully configured local
cluster in one command. The script creates a kind cluster backed by a persistent
local Docker registry, configures each cluster node's containerd runtime to
resolve pushes and pulls through that registry, and publishes the registry's
address to the cluster via the standard `local-registry-hosting` ConfigMap.
Every operation is idempotent: re-running the script on an already-provisioned
environment safely skips or re-applies each step as appropriate.

Success is measured by a developer being able to run the Bazel target on a clean
machine and, within one command, have a working kind cluster and local registry
through which container images can be pushed and pulled without further
configuration.

---

## 2. Project Scope

### 2.1 In Scope

- Python script that provisions a kind cluster and local Docker registry.
- All CLI arguments documented in section 6.
- Idempotent execution for all five provisioning steps.
- Rich terminal output with spinners and coloured per-step status.
- Dry-run mode that prints commands without executing them.
- A thin subprocess wrapper module to centralise all external calls for
  testability.
- Bazel build target using the `o_py_binary` rule wrapper.
- Unit tests with mocked subprocess wrapper.
- Integration tests as needed.

### 2.2 Out of Scope

- Provisioning of Kubernetes workloads (Grafana, Argo Workflows, Kubeflow, etc.)
  — planned for a future evolution of this script.
- Hermetic management of `kind`, `docker`, and `kubectl` via multitool or any
  other mechanism — these are assumed to be on `PATH`.
- Multi-cluster or multi-registry topologies.
- CI/CD pipeline integration.
- Windows support (Linux and macOS only).

### 2.3 Assumptions

- `kind`, `docker`, and `kubectl` are installed and available on `PATH` at
  runtime.
- The developer has Docker running locally with sufficient permissions to create
  containers and networks.
- The Bazel Python toolchain version is whatever the workspace provides; the
  script must not hardcode a Python version.
- The default port 5001 is available on localhost when the registry is first
  created.
- The cluster name (`mlody`) uniquely identifies the kind cluster within the
  developer's local Docker environment.

### 2.4 Constraints

- Permitted third-party Python dependencies: `click`, `rich`, `pyyaml`.
- The script must be buildable and runnable via Bazel using the `o_py_binary`
  rule; it must not require `pip install` outside of the Bazel dependency graph.
- External tool calls must be routed through the centralised subprocess wrapper
  — never called ad hoc from business logic.

---

## 3. Stakeholders

| Role          | Name/Group          | Responsibilities                                 | Contact                   |
| ------------- | ------------------- | ------------------------------------------------ | ------------------------- |
| Primary User  | mlody developers    | Run the script to provision local environments   | Internal engineering      |
| Product Owner | Maurizio Vitale     | Requirements sign-off, future roadmap            | Git user: Maurizio Vitale |
| Future Users  | Infra / ML platform | Will extend the script for workload provisioning | [TBD]                     |

---

## 4. Business Requirements

### 4.1 Business Objectives

- **BR-001:** Reduce local environment setup time from a multi-step manual
  process to a single command.
- **BR-002:** Eliminate configuration drift between developer environments by
  providing a canonical, version-controlled provisioning script.
- **BR-003:** Establish an extensible foundation for future local infrastructure
  provisioning (Grafana, Argo Workflows, Kubeflow, etc.).

### 4.2 Success Metrics

- **KPI-001:** A developer on a clean machine can provision a working cluster
  and registry in one command with no manual follow-up steps. Target: 100% of
  cases. Measurement: manual verification and integration test passage.
- **KPI-002:** Re-running the script on an already-provisioned environment
  produces no errors and leaves the environment unchanged. Target: 100% of
  cases. Measurement: unit and integration test coverage of idempotency paths.

---

## 5. User Requirements

### 5.1 User Personas

**Persona 1: mlody Developer**

- Goals: Quickly spin up a local Kubernetes cluster to test ML pipeline
  components; push container images from the Bazel build to the local registry
  without extra configuration.
- Pain Points: Multi-step manual setup; inconsistent environments between
  teammates; having to look up the correct `kind` config patch syntax each time.
- Needs: One command, clear feedback on what is happening, safe re-runs.

### 5.2 User Stories

**Epic 1: Local Cluster Provisioning**

- **US-001:** As a mlody developer, I want to run a single Bazel target so that
  a kind cluster and local registry are ready without any manual steps.
  - Acceptance Criteria: Given a clean machine with `kind`, `docker`, and
    `kubectl` on PATH, when I run
    `bazel run //mlody/infra/kind:o-mlody-cluster`, then a kind cluster named
    `mlody` exists, a registry container named `kind-registry` is running on
    port 5001, each cluster node resolves `localhost:5001` to the registry, the
    registry is connected to the kind Docker network, and the
    `local-registry-hosting` ConfigMap exists in `kube-public`.
  - Priority: Must Have

- **US-002:** As a mlody developer, I want re-running the provisioner to be safe
  so that I can run it again after a partial failure or configuration change
  without breaking my environment.
  - Acceptance Criteria: Given an already-provisioned environment, when I run
    the script again, then no step fails and the cluster and registry are
    unchanged.
  - Priority: Must Have

- **US-003:** As a mlody developer, I want to use `--dry-run` so that I can
  inspect what commands would be executed before committing to them.
  - Acceptance Criteria: Given `--dry-run` is passed, when the script runs, then
    all commands are printed to stdout and no external process is executed.
  - Priority: Must Have

- **US-004:** As a mlody developer, I want to use `--force` to delete and
  recreate the kind cluster when I need a clean slate, without affecting the
  registry.
  - Acceptance Criteria: Given an existing cluster and `--force` is passed, when
    the script runs, then the existing kind cluster is deleted, a new one is
    created, and the registry container is left untouched.
  - Priority: Should Have

- **US-005:** As a mlody developer, I want to save the generated kind YAML
  config to a path of my choosing so that I can inspect or modify it for
  debugging.
  - Acceptance Criteria: Given `--save-config PATH` is passed, when the script
    runs, then the kind cluster YAML is written to `PATH` before
    `kind create cluster` is called.
  - Priority: Could Have

---

## 6. Functional Requirements

### 6.1 Provisioning Steps

**FR-001: Create Local Docker Registry**

- Description: Start a Docker container named `kind-registry` (configurable)
  listening on `localhost:{registry_port}` if one is not already running.
- Inputs: `--registry-name`, `--registry-port`.
- Processing: Check if a container with `--registry-name` is already running. If
  running, skip. If not running (absent or stopped), create it.
- Outputs: Running registry container.
- Business Rules:
  - The registry is NEVER deleted or recreated by default.
  - If the container exists but is stopped, start it (do not recreate).
  - `--force` causes the registry container to be deleted and recreated (unlike
    the kind cluster, the registry is treated the same under `--force`).
- Priority: Must Have
- Dependencies: Docker daemon running.

**FR-002: Create kind Cluster**

- Description: Create a kind cluster with a containerd registry config patch
  that tells containerd about the local registry.
- Inputs: `--cluster-name`, `--registry-name`, `--registry-port`,
  `--kubeconfig`, `--save-config`.
- Processing:
  1. Generate kind cluster YAML with a `containerdConfigPatches` section that
     adds a mirror entry for `localhost:{registry_port}`.
  2. If `--save-config PATH` is provided, write the YAML to `PATH`; otherwise
     write to a temp file.
  3. Run `kind create cluster --name {cluster_name} --config {yaml_path}`.
- Outputs: Running kind cluster reachable via `kubectl`.
- Business Rules:
  - If the cluster already exists and `--force` is NOT set, skip this step.
  - If the cluster already exists and `--force` IS set, delete the cluster first
    (`kind delete cluster --name {cluster_name}`), then create it.
- Priority: Must Have
- Dependencies: FR-001 (registry must exist before cluster is created so that
  the config patch references a valid name/port).

**FR-003: Configure Kind Node containerd Registry**

- Description: Write
  `/etc/containerd/certs.d/localhost:{registry_port}/hosts.toml` into each kind
  node container via `docker exec`, pointing containerd at the registry.
- Inputs: `--cluster-name`, `--registry-port`.
- Processing: For each node returned by `kind get nodes --name {cluster_name}`,
  run `docker exec {node} mkdir -p /etc/containerd/certs.d/localhost:{port}`
  then
  `docker exec {node} sh -c 'cat > /etc/containerd/certs.d/localhost:{port}/hosts.toml'`
  with the appropriate TOML content.
- Outputs: Updated containerd config on each node.
- Business Rules:
  - Always re-apply, regardless of whether the file already exists. The
    operation is idempotent (overwriting with the same content is safe).
- Priority: Must Have
- Dependencies: FR-002 (cluster nodes must exist).

**FR-004: Connect Registry to kind Docker Network**

- Description: Connect the registry container to the kind Docker network so that
  cluster nodes can reach it.
- Inputs: `--cluster-name`, `--registry-name`.
- Processing: Use `kind` as the Docker network name (hardcoded for now;
  customisable network name is deferred to a future version). Check if the
  registry container is already connected to that network. If connected, skip.
  If not, run `docker network connect kind {registry_name}`.
- Outputs: Registry container reachable from kind cluster nodes.
- Business Rules:
  - Skip (do not error) if already connected.
- Priority: Must Have
- Dependencies: FR-001, FR-002.

**FR-005: Apply local-registry-hosting ConfigMap**

- Description: Create or update the `local-registry-hosting` ConfigMap in the
  `kube-public` namespace with the registry's address and port.
- Inputs: `--registry-port`, `--kubeconfig`.
- Processing: Generate the ConfigMap YAML and apply it with
  `kubectl apply -f -`.
- Outputs: ConfigMap present in `kube-public` namespace.
- Business Rules:
  - Always re-apply via `kubectl apply` (idempotent, safe to redo).
- Priority: Must Have
- Dependencies: FR-002 (cluster must be running and `kubectl` must be
  configured).

### 6.2 CLI Interface

**FR-006: Command-Line Arguments**

| Argument             | Default                  | Description                                                     |
| -------------------- | ------------------------ | --------------------------------------------------------------- |
| `--cluster-name`     | `mlody`                  | Name of the kind cluster                                        |
| `--registry-name`    | `kind-registry`          | Name of the Docker registry container                           |
| `--registry-port`    | `5001`                   | Host port the registry listens on                               |
| `--kubeconfig`       | (system default)         | Path to kubeconfig file override                                |
| `--save-config PATH` | (not set; use temp file) | Persist generated kind YAML to this path                        |
| `--dry-run`          | off                      | Print commands without executing them                           |
| `--verbose`          | off                      | Increase output verbosity                                       |
| `--quiet`            | off                      | Suppress non-essential output                                   |
| `--force`            | off                      | Delete and recreate the kind cluster (does NOT affect registry) |

- Priority: Must Have

**FR-007: Dry-Run Mode**

- Description: When `--dry-run` is active, every command that would be executed
  by the subprocess wrapper is printed to stdout instead of being run. No
  external state is modified.
- Priority: Must Have

### 6.3 Subprocess Wrapper Module

**FR-008: Centralised Subprocess Abstraction**

- Description: All calls to external tools (`kind`, `docker`, `kubectl`) must go
  through a single thin wrapper module. Business logic must not call
  `subprocess` directly.
- Inputs: Command and arguments.
- Outputs: Stdout/stderr text or a structured result object.
- Business Rules:
  - The wrapper must be injectable/mockable for unit testing.
  - In dry-run mode, the wrapper prints commands and returns a no-op result.
- Priority: Must Have

### 6.4 Error Handling

**FR-009: Fatal Error on Step Failure**

- Description: If any provisioning step fails (non-zero exit from an external
  tool, or an unexpected exception), the script must exit immediately with a
  clear, human-readable error message indicating which step failed and why.
- Business Rules:
  - Do not silently swallow errors.
  - Do not attempt to continue to subsequent steps after a failure.
  - Exit code must be non-zero on failure.
- Priority: Must Have

---

## 7. Non-Functional Requirements

### 7.1 Performance Requirements

- **NFR-001:** The script itself adds negligible overhead beyond the time taken
  by the underlying `kind`, `docker`, and `kubectl` commands. No specific
  wall-clock target, as provisioning time is dominated by image pulls.

### 7.2 Scalability Requirements

- **NFR-002:** The script is designed for single-developer local use; no
  multi-user or concurrent execution requirements.

### 7.3 Availability & Reliability

- **NFR-003:** The script must be idempotent. Re-running on an
  already-provisioned environment must never break the environment.
- **NFR-004:** Any failure must leave the environment in a known, inspectable
  state — partially completed steps must not silently corrupt state.

### 7.4 Security Requirements

- **NFR-005:** The local registry is unauthenticated by design (local
  development only). No TLS or auth configuration is required.
- **NFR-006:** The script must not write secrets or tokens to disk in plain
  text.

### 7.5 Usability Requirements

- **NFR-007:** The script must use Rich for terminal output: spinners during
  long-running steps, green checkmarks for skipped/completed steps, and red
  error indicators on failure.
- **NFR-008:** Each provisioning step must be clearly identified in the output
  so the user knows which step is in progress and which have completed.
- **NFR-009:** `--quiet` mode must suppress spinner and status output, printing
  only errors.
- **NFR-010:** `--verbose` mode must print the full commands being executed
  (even when not in dry-run mode) and any stdout/stderr from external tools.

### 7.6 Maintainability Requirements

- **NFR-011:** The subprocess wrapper must be the sole integration boundary
  between business logic and the operating system. This makes the script
  testable without spawning real processes.
- **NFR-012:** The script must use `click` for argument parsing; argument
  definitions must be co-located with their help text.

### 7.7 Compatibility Requirements

- **NFR-013:** The script must run on Linux and macOS.
- **NFR-014:** The Python version must be whatever the Bazel workspace provides.
  No `sys.version_info` guards or hardcoded version strings.

---

## 8. Data Requirements

### 8.1 Data Entities

- **Kind cluster YAML config:** Generated in memory; optionally persisted to
  disk via `--save-config`.
- **local-registry-hosting ConfigMap YAML:** Generated in memory; applied via
  `kubectl apply`.
- **containerd hosts.toml:** Generated in memory; written to each node via
  `docker exec`.

### 8.2 Data Quality Requirements

- All generated YAML must be valid and parseable by the respective tools.

### 8.3 Data Retention & Archival

- The script does not persist data beyond the optional `--save-config` file.
  Cluster and registry state is managed by Docker and kind.

### 8.4 Data Privacy & Compliance

- No personal data is handled. No compliance requirements beyond local developer
  tooling norms.

---

## 9. Integration Requirements

### 9.1 External Tools

| Tool      | Purpose                                | Direction        | Notes                                    |
| --------- | -------------------------------------- | ---------------- | ---------------------------------------- |
| `kind`    | Create/delete/query kind clusters      | Script → kind    | Assumed on PATH                          |
| `docker`  | Manage registry container and networks | Script → docker  | Assumed on PATH                          |
| `kubectl` | Apply ConfigMap to cluster             | Script → kubectl | Assumed on PATH; kubeconfig configurable |

### 9.2 API Requirements

- All integration is via subprocess invocation of CLI tools. No HTTP API calls.

---

## 10. User Interface Requirements

### 10.1 UI/UX Principles

- Terminal-first; no web UI or GUI.
- Progress must be visible for long-running steps (spinners via Rich).
- Each step's outcome (created, skipped, re-applied, failed) must be
  distinguishable at a glance by colour and/or symbol.

### 10.2 Key Output States

| State                         | Visual                                        |
| ----------------------------- | --------------------------------------------- |
| Step in progress              | Spinner with step name                        |
| Step completed (action taken) | Green checkmark + step name + action taken    |
| Step skipped (already done)   | Yellow/grey checkmark + step name + "skipped" |
| Step failed                   | Red X + step name + error detail              |

### 10.3 Dry-Run Output

In dry-run mode, each command is printed as it would be executed, prefixed with
a clear `[DRY RUN]` indicator.

---

## 11. Reporting & Analytics Requirements

Not applicable for this tooling script.

---

## 12. Security & Compliance Requirements

### 12.1 Authentication & Authorization

- No authentication. Local developer tool only.

### 12.2 Data Security

- No sensitive data handled.

### 12.3 Compliance

- No regulatory compliance requirements.

### 12.4 Permission Matrix

Not applicable.

---

## 13. Infrastructure & Deployment Requirements

### 13.1 Hosting & Environment

- Developer workstation (Linux or macOS).
- Docker and kind must be pre-installed.

### 13.2 Deployment

- Distributed as a Bazel target: `//mlody/infra/kind:o-mlody-cluster`.
- Built with `o_py_binary` rule.
- Dependencies (`click`, `rich`, `pyyaml`) managed via Bazel.

### 13.3 Disaster Recovery

- Not applicable. If the local environment is corrupted, the developer deletes
  the cluster (`kind delete cluster --name mlody`) and re-runs the script.

---

## 14. Testing & Quality Assurance Requirements

### 14.1 Testing Scope

- **Unit tests:** All provisioning steps tested with the subprocess wrapper
  mocked. Covers happy path, idempotency paths (already exists / already
  connected / etc.), and error paths.
- **Integration tests:** End-to-end test that provisions a real cluster
  (requires `kind`, `docker`, `kubectl` on PATH). Scope TBD based on CI
  environment constraints.

### 14.2 Acceptance Criteria

- All unit tests pass in `bazel test //mlody/infra/kind/...`.
- Dry-run mode produces expected command output with no side effects.
- Running the script twice on the same environment produces no errors on the
  second run.
- `--force` deletes and recreates the cluster without touching the registry.

---

## 15. Training & Documentation Requirements

### 15.1 User Documentation

- Inline `--help` output (via `click`) must describe every argument with its
  default value.

### 15.2 Technical Documentation

- Inline code comments for the subprocess wrapper contract and each provisioning
  step's idempotency logic.

### 15.3 Training

- No formal training required; `--help` and `--dry-run` are the primary
  onboarding mechanism.

---

## 16. Risks & Mitigation Strategies

| Risk ID | Description                                                                  | Impact | Probability | Mitigation                                                                | Owner       |
| ------- | ---------------------------------------------------------------------------- | ------ | ----------- | ------------------------------------------------------------------------- | ----------- |
| R-001   | Port 5001 already in use on developer machine                                | High   | Medium      | Emit a clear error message directing the user to pass `--registry-port`   | Developer   |
| R-002   | `kind`, `docker`, or `kubectl` not on PATH                                   | High   | Low         | Check for tool presence at startup and fail fast with an actionable error | Script      |
| R-003   | Kind cluster name collision with non-mlody cluster                           | Medium | Low         | Default name `mlody` is distinctive; document in `--help`                 | Developer   |
| R-004   | Script grows in scope (workload provisioning) without architectural refactor | Medium | High        | Design step abstraction to be extensible from day one                     | Engineering |
| R-005   | Integration tests require Docker-in-Docker in CI                             | Medium | Medium      | Make integration tests opt-in; unit tests are sufficient for initial CI   | Engineering |

---

## 17. Dependencies

| Dependency                              | Type                | Status                        | Impact if Delayed         | Owner       |
| --------------------------------------- | ------------------- | ----------------------------- | ------------------------- | ----------- |
| `o_py_binary` Bazel rule                | Internal build rule | Assumed available             | Cannot build Bazel target | Build infra |
| `click`, `rich`, `pyyaml` in Bazel deps | Third-party Python  | Assumed available in pip repo | Cannot build              | Build infra |
| `kind` on developer PATH                | External tool       | Developer responsibility      | Script cannot run         | Developer   |
| `docker` on developer PATH              | External tool       | Developer responsibility      | Script cannot run         | Developer   |
| `kubectl` on developer PATH             | External tool       | Developer responsibility      | Script cannot run         | Developer   |

---

## 18. Open Questions & Action Items

| ID     | Question/Action                                                                                            | Owner           | Target Date | Status                                         |
| ------ | ---------------------------------------------------------------------------------------------------------- | --------------- | ----------- | ---------------------------------------------- |
| OQ-001 | If the registry container exists but is stopped, start it. If `--force` is passed, delete and recreate it. | Maurizio Vitale | 2026-04-13  | Resolved                                       |
| OQ-002 | Kind network name is hardcoded to `kind` for now; customisable network name deferred to a future version.  | Engineering     | 2026-04-13  | Resolved                                       |
| OQ-003 | Integration test strategy for CI (Docker-in-Docker vs. skip on CI)                                         | Engineering     | [TBD]       | Open                                           |
| OQ-004 | Exact `o_py_binary` and pip dependency declaration pattern to follow in BUILD.bazel                        | Engineering     | [TBD]       | Open — check existing examples in `build/bzl/` |

---

## 19. Revision History

| Version | Date       | Author                              | Changes       |
| ------- | ---------- | ----------------------------------- | ------------- |
| 1.0     | 2026-04-13 | Requirements Analyst AI (@socrates) | Initial draft |

---

## Appendices

### Appendix A: Glossary

- **kind:** Kubernetes IN Docker — a tool for running local Kubernetes clusters
  using Docker container nodes.
- **kind-registry:** The Docker container name for the local OCI image registry.
- **local-registry-hosting:** A Kubernetes ConfigMap convention (KEP-1755) that
  advertises the address of a local registry to tooling running inside the
  cluster.
- **containerd registry mirror:** A containerd configuration that redirects
  image pulls for a given hostname to a different endpoint (here, the local
  registry).
- **o_py_binary:** An internal Bazel rule wrapper for Python binaries in this
  monorepo.

### Appendix B: References

- KIND local registry guide: https://kind.sigs.k8s.io/docs/user/local-registry/
- KEP-1755 (local-registry-hosting ConfigMap convention):
  https://github.com/kubernetes/enhancements/tree/master/keps/sig-cluster-lifecycle/generic/1755-communicating-a-local-registry

### Appendix C: Provisioning Step Flow

```
bazel run //mlody/infra/kind:o-mlody-cluster [args]
    │
    ├─ Step 1: Create registry container (skip if running; NEVER delete)
    │
    ├─ Step 2: Create kind cluster (skip if exists; delete+recreate if --force)
    │
    ├─ Step 3: Configure containerd on each node (always re-apply)
    │
    ├─ Step 4: Connect registry to kind network (skip if already connected)
    │
    └─ Step 5: Apply local-registry-hosting ConfigMap (always kubectl apply)
```

---

**End of Requirements Document**
