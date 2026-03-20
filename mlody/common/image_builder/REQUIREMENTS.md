# Requirements Document: mlody-image-builder

**Version:** 1.1 **Date:** 2026-03-19 **Prepared by:** Requirements Analyst AI
**Status:** Draft

---

## 1. Executive Summary

`mlody-image-builder` is a standalone Python binary that builds and pushes OCI
container images from Bazel targets at a pinned commit SHA. Given one or more
Bazel target labels, a full 40-digit commit SHA, and a destination container
registry, the tool clones the repository at that exact SHA, dynamically
generates the Bazel build plumbing required to produce a single OCI image,
builds it, tags it (one tag per input target), and pushes the result to the
registry.

The primary value is reproducibility: images are always built from a known,
immutable source tree rather than the caller's working directory. The tool is a
pipeline primitive inside the mlody ML framework, intended to be invoked from
CI/CD automation as well as by engineers directly.

---

## 2. Project Scope

### 2.1 In Scope

- Standalone `mlody-image-builder` Python binary with its own Bazel
  `o_py_binary` target. Not integrated into the existing `mlody` CLI.
- Accepting one or more Bazel target labels, a 40-digit commit SHA, a container
  registry destination, and an optional `--remote` URL override as inputs.
- Resolving the git remote URL from the local working directory when `--remote`
  is not supplied.
- Shallow-cloning the repository at the given SHA into
  `~/.cache/mlody/builds/<SHA>/`.
- Reusing an existing cached clone when present, running `bazel clean` (normal
  clean, not `--expunge`) before rebuilding.
- Dynamically generating build plumbing via a Bazel-native `repository_rule`
  (`dynamic`) declared in a `repo.bzl` at the monorepo root of the cloned repo.
  The rule reads `TARGETS` from `ctx.os.environ` (passed via
  `--repo_env=TARGETS=...`) and generates a `BUILD.bazel` containing a
  `filegroup` → `genrule` (tar) → `oci_image` chain. The tool invokes
  `bazel build @dynamic_image//:image --repo_env=TARGETS="//foo:bar,//baz:qux"`
  rather than writing a `BUILD.bazel` directly to the filesystem.
- Running `bazel build` on the generated `oci_image` target inside the cloned
  repo.
- Tagging the image with one tag per input Bazel target combined with the first
  16 characters of the commit SHA.
- Pushing the image with all tags to the specified registry.
- Using `~/.docker/config.json` for registry authentication, with the auth
  mechanism designed as a replaceable abstraction point.
- Attempting to build ALL input targets before reporting failures (no early
  abort on first failure).
- Emitting distinct exit codes per failure category: clone failure, build
  failure, push failure.
- Emitting structured JSON on both success and failure.
- Reusing existing clone infrastructure from `mlody/resolver/resolver.py`
  (check-then-clone with file locking).

### 2.2 Out of Scope

- Concurrent or parallel builds across multiple SHAs or target sets.
- Subcommand integration into the existing `mlody` CLI.
- HuggingFace model downloads, weight baking, or any model-related operations.
- Write-back of any artifacts to the source repository.
- Vault or explicit-credential authentication (reserved for a future phase).

### 2.3 Assumptions

- The caller has network access to the git remote and to the target container
  registry.
- `git` and `bazel` (via Bazelisk) are available on `PATH` at runtime.
- `rules_oci` and `rules_pkg` are present in the cloned repo's MODULE.bazel or
  WORKSPACE.
- The `~/.cache/mlody/builds/` directory is writable by the process.
- `~/.docker/config.json` contains valid credentials for the target registry.

### 2.4 Constraints

- Must be implemented as a standalone `o_py_binary`, not a library or
  sub-command.
- Python 3.13.2 (hermetic via rules_python), strict basedpyright type checking,
  ruff formatting/linting.
- Must follow monorepo conventions: absolute imports, `o_py_binary` /
  `o_py_library` / `o_py_test` Bazel macros, Gazelle-managed BUILD files.

---

## 3. Stakeholders

| Role               | Name/Group         | Responsibilities                          | Contact                     |
| ------------------ | ------------------ | ----------------------------------------- | --------------------------- |
| Primary User       | ML Engineers       | Invoke tool from CI and local workstation | [Pending Stakeholder Input] |
| System Owner       | Polymath Solutions | Define requirements; approve design       | [Pending Stakeholder Input] |
| Solution Architect | [TBD]              | Design and implement solution             | [Pending Stakeholder Input] |

---

## 4. Business Requirements

### 4.1 Business Objectives

- **BR-001:** Enable reproducible OCI image builds from any historical commit
  SHA without requiring the caller to check out that commit locally.
- **BR-002:** Eliminate manual tag management by deriving tags deterministically
  from Bazel target labels and the commit SHA.
- **BR-003:** Provide a pipeline-safe tool with structured output and distinct
  failure codes so that CI systems can respond appropriately to each failure
  mode.

### 4.2 Success Metrics

- **KPI-001:** Given a valid SHA and target list, the tool produces and pushes a
  tagged image without manual intervention. Target: 100% of happy-path
  invocations succeed end-to-end.
- **KPI-002:** Each failure category (clone, build, push) is distinguishable by
  exit code. Target: 100% of failures map to the correct exit code.
- **KPI-003:** Repeated invocations with the same SHA reuse the cached clone
  without re-fetching git objects.

---

## 5. User Requirements

### 5.1 User Personas

**Persona 1: CI/CD Automation**

- Goals: Invoke the tool non-interactively, parse JSON output, branch on exit
  code.
- Needs: Machine-readable structured output, distinct exit codes, no interactive
  prompts.

**Persona 2: ML Engineer (local workstation)**

- Goals: Build and push an image for a specific historical commit for debugging
  or deployment.
- Needs: Simple CLI invocation, reuse of cached clones to avoid redundant
  downloads.

### 5.2 User Stories

**Epic 1: Image Build and Push**

- **US-001:** As a CI pipeline, I want to invoke `mlody-image-builder` with a
  list of Bazel targets, a commit SHA, and a registry destination so that a
  versioned OCI image is built and pushed automatically.
  - Acceptance Criteria: Given valid inputs, when the tool is invoked, then the
    image is built, tagged, and pushed, and the tool exits 0 with JSON metadata
    on stdout.
  - Priority: Must Have

- **US-002:** As an ML engineer, I want the tool to reuse a previously cached
  clone for the same SHA so that I do not wait for a full re-clone on repeated
  invocations.
  - Acceptance Criteria: Given a clone already present at
    `~/.cache/mlody/builds/<SHA>/`, when the tool is invoked with the same SHA,
    then no new `git fetch` is issued and the build proceeds directly (after
    `bazel clean`, non-expunge).
  - Priority: Must Have

- **US-003:** As a CI pipeline, I want distinct exit codes for clone failure,
  build failure, and push failure so that the pipeline can take different
  remediation actions per failure type.
  - Acceptance Criteria: Clone failure, build failure, and push failure each
    produce a different non-zero exit code, documented in the tool's help text.
  - Priority: Must Have

- **US-004:** As an ML engineer, I want one image tag per input Bazel target
  combined with a 16-character SHA prefix so that I can identify which target
  produced which tag.
  - Acceptance Criteria: For input target `//mlody/lsp:lsp_server` and a 40-char
    SHA beginning with `abcdef1234567890`, the resulting tag is
    `mlody-lsp-lsp_server-abcdef1234567890` pushed to the specified registry.
  - Priority: Must Have

- **US-005:** As an ML engineer, I want to override the git remote URL via
  `--remote` so that I can build from a fork or mirror without changing my local
  git config.
  - Acceptance Criteria: When `--remote <URL>` is supplied, the tool uses that
    URL for cloning instead of `git remote get-url origin`.
  - Priority: Must Have

---

## 6. Functional Requirements

### 6.1 CLI Interface

**FR-001: Binary Entry Point**

- Description: The tool is a standalone Python binary named
  `mlody-image-builder` with its own `o_py_binary` Bazel target. It is NOT a
  subcommand of the existing `mlody` CLI.
- Priority: Must Have

**FR-002: Command-Line Arguments**

- Description: The binary accepts the following arguments:

  | Argument     | Type              | Required | Description                                                                 |
  | ------------ | ----------------- | -------- | --------------------------------------------------------------------------- |
  | `TARGETS`    | positional (1..N) | Yes      | One or more Bazel target labels (e.g. `//mlody/lsp:lsp_server`)             |
  | `--sha`      | string (40 chars) | Yes      | Full 40-digit hexadecimal commit SHA                                        |
  | `--registry` | string            | Yes      | Container registry destination (e.g. `registry.example.com/repo`)           |
  | `--remote`   | string            | No       | Git remote URL override. Defaults to `git remote get-url origin` in the cwd |

- Priority: Must Have

### 6.2 Phase 1 — Remote Resolution

**FR-003: Git Remote URL Resolution**

- Description: When `--remote` is not supplied, the tool executes
  `git remote get-url origin` in the current working directory and uses the
  result as the clone source. When `--remote` is supplied, that value is used
  directly.
- Priority: Must Have

### 6.3 Phase 2 — Repository Clone and Cache

**FR-004: Shallow Clone at SHA**

- Description: The tool clones the repository at the given SHA to
  `~/.cache/mlody/builds/<SHA>/` using a shallow fetch:
  `git fetch --depth 1 origin <SHA>` followed by checkout of that SHA.
- Priority: Must Have

**FR-005: Cache Reuse**

- Description: If `~/.cache/mlody/builds/<SHA>/` already exists, the tool skips
  cloning and reuses the existing directory. `bazel clean` (normal clean, NOT
  `--expunge`) is run inside the cloned repo before the build phase in both the
  fresh-clone and cache-hit cases.
- Priority: Must Have

**FR-006: File Locking for Clone**

- Description: The clone step reuses the check-then-clone with file-locking
  pattern from `mlody/resolver/resolver.py` to prevent race conditions if
  multiple processes target the same SHA simultaneously.
- Priority: Should Have

### 6.4 Phase 3 — Dynamic Repository Rule

**FR-007: Bazel-Native repository_rule for Image Generation**

- Description: Image build plumbing is generated inside Bazel's own dependency
  graph via a `repository_rule` named `dynamic`, declared in a `repo.bzl` file
  at the monorepo root of the cloned repo. The rule:
  1. Reads the `TARGETS` environment variable from `ctx.os.environ` (populated
     by the tool via `--repo_env=TARGETS=<comma-separated labels>`).
  2. Generates a `BUILD.bazel` inside the external repository containing a
     `filegroup` → `genrule` (tar) → `oci_image` chain for all supplied targets.
  3. Exposes the assembled image as `@dynamic_image//:image`.
- The tool invokes Bazel as:
  `bazel build @dynamic_image//:image --repo_env=TARGETS="//foo:bar,//baz:qux"`
- This approach keeps image generation fully within Bazel's dependency graph and
  avoids direct filesystem side effects. Writing a `BUILD.bazel` directly to the
  cloned repo filesystem is explicitly rejected.
- Dependencies: `rules_oci` and `rules_pkg` must be available in the cloned
  repo's `MODULE.bazel` or `WORKSPACE`.
- Priority: Must Have

### 6.5 Phase 4 — Bazel Build

**FR-008: Build the @dynamic_image//:image Target**

- Description: The tool runs
  `bazel build @dynamic_image//:image --repo_env=TARGETS=<labels>` inside the
  cloned repo directory. The `dynamic` repository rule generates the required
  `BUILD.bazel` at repository-fetch time within Bazel's own execution.
- Error handling: If the build fails, the tool records the failure but continues
  attempting any remaining targets before reporting (see FR-011).
- Priority: Must Have

### 6.6 Phase 5 — Image Tagging

**FR-009: Tag Derivation**

- Description: One tag is produced per input Bazel target. The tag is derived by
  sanitizing the target label and appending the first 16 characters of the
  commit SHA (SHA16).
  - Sanitization: replace any character outside `[A-Za-z0-9_.-]` with `-`. The
    leading character must match `[A-Za-z0-9_]`. Maximum tag length: 128
    characters.
  - Example: `//mlody/lsp:lsp_server` with SHA16 `abcdef1234567890` produces tag
    `mlody-lsp-lsp_server-abcdef1234567890`.
- Priority: Must Have

### 6.7 Phase 6 — Image Push

**FR-010: Push to Registry with All Tags**

- Description: The tool pushes the built OCI image to the registry specified by
  `--registry`, applying all derived tags. The authentication mechanism reads
  credentials from `~/.docker/config.json` and is encapsulated behind an
  abstraction interface to allow future replacement (e.g. explicit credentials,
  vault integration) without changing calling code.
- Priority: Must Have

### 6.8 Error Handling

**FR-011: Build All Before Reporting**

- Description: The tool attempts to build ALL input targets before surfacing
  failures. It does not abort on the first build failure.
- Priority: Must Have

**FR-012: Distinct Exit Codes**

- Description: The tool exits with distinct non-zero codes per failure category.
  Exit 0 means full success; any non-zero exit means failure. The exact non-zero
  values for each category are left to the implementer's discretion, subject to
  the following constraints:
  - Clone failure, build failure, and push failure must each map to a different
    non-zero value.
  - The assigned values must be documented in `--help` output and the tool's
    `README.md`.
- Priority: Must Have

**FR-013: Structured JSON Error Output**

- Description: On failure, the tool emits a structured JSON object to **stdout**
  describing the failure type, affected targets, and relevant diagnostic
  information. Stderr is reserved exclusively for structured log lines emitted
  during execution (see NFR-012).
- Priority: Must Have

### 6.9 Success Output

**FR-014: JSON Metadata on Success**

- Description: On success, the tool prints a JSON object to stdout containing at
  minimum:
  - `image_digest`: content-addressable digest of the pushed image.
  - `image_references`: list of fully-qualified image references
    (`registry/repo:tag`) for each tag pushed.
  - `commit_sha`: the full 40-digit SHA used.
  - `input_targets`: the list of Bazel target labels supplied as input.
- Priority: Must Have

---

## 7. Non-Functional Requirements

### 7.1 Performance Requirements

- **NFR-001:** The cache-hit path must not re-fetch git objects from the remote.
- **NFR-002:** No specific latency SLA; the tool is intended for batch/CI
  contexts where build time is acceptable.

### 7.2 Scalability Requirements

- **NFR-003:** Concurrent invocations targeting the same SHA are protected by
  file locking (FR-006). Multi-SHA or multi-invocation concurrency is out of
  scope.

### 7.3 Availability and Reliability

- **NFR-004:** The tool is a CLI binary; no uptime SLA applies. Reliability is
  expressed through correct exit codes and structured error output.

### 7.4 Security Requirements

- **NFR-005:** Registry credentials are read exclusively from
  `~/.docker/config.json`. Credentials must not appear in logs, JSON output, or
  error messages.
- **NFR-006:** The authentication mechanism is encapsulated as an abstraction
  point to allow future integration with secrets managers or explicit credential
  injection without interface changes.

### 7.5 Usability Requirements

- **NFR-007:** The tool must provide a `--help` flag with documentation of all
  arguments and the exit code table.

### 7.6 Maintainability Requirements

- **NFR-008:** Must comply with monorepo Python conventions: strict
  basedpyright, ruff, absolute imports, Gazelle-managed BUILD files,
  `o_py_binary` / `o_py_library` / `o_py_test` macros.
- **NFR-009:** Clone logic must reuse `mlody/resolver/resolver.py` patterns
  rather than reimplementing equivalent logic.

### 7.7 Compatibility Requirements

- **NFR-010:** Python 3.13.2 hermetic runtime via rules_python.
- **NFR-011:** Requires `git` and `bazel` (Bazelisk) available on `PATH` at
  runtime.

### 7.8 Observability Requirements

- **NFR-012:** The tool may emit structured, machine-readable log lines to
  stderr during execution (e.g.
  `{"level": "info", "phase": "clone", "sha": "..."}`). Rich spinners, progress
  bars, and interactive console UI are prohibited. All JSON result output (both
  success and failure) goes exclusively to stdout.

---

## 8. Data Requirements

### 8.1 Data Entities

- **Commit SHA:** 40-digit hexadecimal string; used as cache key and (first 16
  chars) as tag suffix.
- **SHA16:** First 16 characters of the commit SHA; used in tag construction.
- **Bazel Target Label:** String of the form `//path/to:target`; one or more per
  invocation.
- **Registry Destination:** String of the form `registry.example.com/repo`.
- **Clone Cache Entry:** Directory at `~/.cache/mlody/builds/<SHA>/` containing
  a git worktree at the specified SHA.
- **Generated BUILD.bazel:** Ephemeral file written into the clone directory for
  each build.
- **Image Digest:** SHA256 digest string returned by the OCI registry after
  push.

### 8.2 Data Retention and Archival

- Clone cache directories under `~/.cache/mlody/builds/` persist until manually
  removed. Cache eviction policy is out of scope for this version.

### 8.3 Data Privacy and Compliance

- No user PII is handled. Registry credentials from `~/.docker/config.json` must
  not be echoed in logs or output.

---

## 9. Integration Requirements

### 9.1 External Systems

| System             | Purpose            | Direction | Format       | Auth                    |
| ------------------ | ------------------ | --------- | ------------ | ----------------------- |
| Git remote         | Source repo clone  | Inbound   | Git protocol | As configured in git    |
| Container registry | Image push         | Outbound  | OCI HTTP API | `~/.docker/config.json` |
| Bazel (Bazelisk)   | Build execution    | Local     | CLI          | N/A                     |
| rules_oci          | OCI image assembly | Local     | Bazel rules  | N/A                     |
| rules_pkg          | Layer packaging    | Local     | Bazel rules  | N/A                     |

### 9.2 Internal Dependencies

| Module                       | Usage                                            |
| ---------------------------- | ------------------------------------------------ |
| `mlody/resolver/resolver.py` | Reuse check-then-clone and file-locking patterns |

---

## 10. User Interface Requirements

### 10.1 CLI Interface

- Single binary, invoked as `mlody-image-builder [OPTIONS] TARGETS...`.
- `--help` produces usage documentation including argument descriptions and exit
  code table.
- All JSON output (success metadata and error details) goes to **stdout**.
- The tool may emit structured, machine-readable log lines to **stderr** during
  execution (e.g. `{"level": "info", "phase": "clone", "sha": "..."}`). No rich
  spinners, progress bars, or interactive console UI are permitted.

---

## 11. Reporting and Analytics Requirements

Not applicable. The tool emits per-invocation JSON output; no persistent
reporting or analytics are required.

---

## 12. Security and Compliance Requirements

### 12.1 Authentication and Authorization

- Registry authentication via `~/.docker/config.json` (Docker credential store
  format).
- Authentication layer is an abstraction point; concrete implementation is
  replaceable without changing the calling interface.

### 12.2 Data Security

- Credentials must not appear in log output, JSON metadata, or error messages.

### 12.3 Compliance

- No specific regulatory compliance requirements identified.

---

## 13. Infrastructure and Deployment Requirements

### 13.1 Hosting and Environment

- Local workstation and CI runner environments.
- Cache directory: `~/.cache/mlody/builds/` (must be writable).

### 13.2 Deployment

- Distributed as a Bazel `o_py_binary` target within the monorepo. No separate
  packaging required for this version.

---

## 14. Testing and Quality Assurance Requirements

### 14.1 Testing Scope

- Unit tests for tag sanitization logic (FR-009): label-to-tag conversion,
  character replacement, and length truncation.
- Unit tests for BUILD.bazel generation (FR-007): verify generated file
  structure for N input targets.
- Unit tests for exit code mapping (FR-012).
- Integration tests for the full clone → build → push pipeline are [TBD —
  require a test registry and git server fixture].

### 14.2 Acceptance Criteria

- All functional requirements have at least one passing automated test.
- `bazel test` passes with `--config=lint` (ruff + basedpyright) on all new
  files.

---

## 15. Training and Documentation Requirements

### 15.1 User Documentation

- `--help` output is the primary user-facing documentation.
- Exit code table must be included in `--help` output and in a `README.md` in
  the tool's source directory.

### 15.2 Technical Documentation

- Inline docstrings on all public functions and classes.
- Architecture notes on the BUILD.bazel generation approach recorded in the
  relevant `spec.md` (to be produced by @vitruvious).

---

## 16. Risks and Mitigation Strategies

| Risk ID | Description                                                                                               | Impact | Probability | Mitigation                                                                                                                 | Owner |
| ------- | --------------------------------------------------------------------------------------------------------- | ------ | ----------- | -------------------------------------------------------------------------------------------------------------------------- | ----- |
| R-001   | Shallow fetch of an old SHA fails if the remote has force-pushed                                          | High   | Low         | Document limitation; consider falling back to full fetch on shallow failure                                                | [TBD] |
| R-002   | `bazel clean` on cache hit discards the action cache, increasing rebuild time on repeated invocations     | Medium | Medium      | Accepted trade-off; `--expunge` is explicitly avoided to preserve the local cache between runs. Monitor build times in CI. | [TBD] |
| R-003   | `repo.bzl` / `dynamic` repository rule absent from the cloned repo (older SHAs pre-date its introduction) | High   | Medium      | Document minimum SHA requirement; consider bundling `repo.bzl` as a fallback via the tool itself                           | [TBD] |
| R-004   | `~/.docker/config.json` requires native credential helpers absent in CI                                   | Medium | Medium      | Document credential setup; abstraction layer allows injection of explicit credentials in future                            | [TBD] |

---

## 17. Dependencies

| Dependency                         | Type     | Status  | Impact if Delayed                     | Owner |
| ---------------------------------- | -------- | ------- | ------------------------------------- | ----- |
| rules_oci available in cloned repo | External | Assumed | Cannot generate valid oci_image BUILD | [TBD] |
| rules_pkg available in cloned repo | External | Assumed | Cannot generate valid pkg_tar layers  | [TBD] |
| `mlody/resolver/resolver.py` reuse | Internal | Exists  | Must reimplement clone/lock logic     | [TBD] |

---

## 18. Open Questions and Action Items

| ID   | Question / Action                                                                                  | Owner                   | Target Date | Status                                                                                                                                                                                                                                            |
| ---- | -------------------------------------------------------------------------------------------------- | ----------------------- | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| OQ-1 | What specific non-zero exit codes are assigned to clone / build / push failures?                   | Requirements Analyst AI | 2026-03-19  | Closed — Zero = success; any non-zero = failure. The specific non-zero values per category are left to the implementer's discretion. All three categories (clone, build, push) must use distinct values, documented in `--help` and `README.md`.  |
| OQ-2 | Should JSON error output go to stdout or stderr?                                                   | Requirements Analyst AI | 2026-03-19  | Closed — Both success JSON metadata and error JSON go to **stdout**. Stderr is reserved for structured log lines only (see OQ-5 / NFR-012).                                                                                                       |
| OQ-3 | Is `bazel clean --expunge` required on cache hit, or is a lighter clean sufficient?                | Requirements Analyst AI | 2026-03-19  | Closed — Use `bazel clean` (normal clean, NOT `--expunge`) on cache hit. See FR-005.                                                                                                                                                              |
| OQ-4 | Where in the cloned repo should the generated BUILD.bazel be written to avoid collisions?          | Requirements Analyst AI | 2026-03-19  | Closed — No BUILD.bazel is written to the cloned repo filesystem. Instead, a Bazel-native `repository_rule` (`dynamic`) declared in `repo.bzl` at the monorepo root generates the `@dynamic_image` external repo at Bazel fetch time. See FR-007. |
| OQ-5 | Should the tool emit progress lines to stderr during execution, or remain silent until completion? | Requirements Analyst AI | 2026-03-19  | Closed — The tool may emit structured, machine-readable log lines to stderr (e.g. `{"level": "info", "phase": "clone", "sha": "..."}`). No rich spinners, progress bars, or interactive UI. See NFR-012.                                          |

---

## 19. Revision History

| Version | Date       | Author                  | Changes                                                                                                                                                                                               |
| ------- | ---------- | ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1.0     | 2026-03-19 | Requirements Analyst AI | Initial draft (rewrite — interview Q&A only)                                                                                                                                                          |
| 1.1     | 2026-03-19 | Requirements Analyst AI | Closed OQ-1 through OQ-5; updated FR-005, FR-007, FR-008, FR-012, FR-013, NFR-012; replaced B+C hybrid filesystem approach with Bazel repository_rule; corrected --expunge to bazel clean throughout. |

---

## Appendices

### Appendix A: Glossary

| Term            | Definition                                                                                                                                                                                                                          |
| --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| SHA             | A 40-digit hexadecimal Git commit identifier                                                                                                                                                                                        |
| SHA16           | The first 16 characters of the commit SHA, used as the tag suffix                                                                                                                                                                   |
| oci_image       | A Bazel target defined by `rules_oci` that assembles an OCI container image from one or more layers                                                                                                                                 |
| pkg_tar         | A Bazel target defined by `rules_pkg` that packages build outputs into a tar archive for use as a layer                                                                                                                             |
| Shallow fetch   | `git fetch --depth 1` — retrieves only the commit graph needed to check out a specific revision                                                                                                                                     |
| Cache hit       | The condition where `~/.cache/mlody/builds/<SHA>/` already exists from a previous invocation                                                                                                                                        |
| repository_rule | A Bazel mechanism (`repository_rule`) that runs arbitrary logic at fetch time to synthesize an external repository, including its `BUILD.bazel`. Used here to generate the `@dynamic_image` repo containing the `oci_image` target. |

### Appendix B: References

- `mlody/resolver/resolver.py` — existing clone + file-lock pattern to reuse
- [rules_oci documentation](https://github.com/bazel-contrib/rules_oci)
- [rules_pkg documentation](https://github.com/bazelbuild/rules_pkg)
- OCI image tag character constraints: `[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}`

### Appendix C: Illustrative CLI Usage

```sh
# Build and push an image from two targets at a specific SHA.
mlody-image-builder \
  //mlody/lsp:lsp_server \
  //mlody/core:worker \
  --sha a3f1c2d4e5b6a7f8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2 \
  --registry registry.example.com/mlody

# Override the git remote (e.g. build from a fork).
mlody-image-builder \
  //mlody/lsp:lsp_server \
  --sha a3f1c2d4e5b6a7f8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2 \
  --registry registry.example.com/mlody \
  --remote git@github.com:org/fork.git
```

**Example success output (stdout):**

```json
{
  "image_digest": "sha256:abc123...",
  "image_references": [
    "registry.example.com/mlody:mlody-lsp-lsp_server-a3f1c2d4e5b6a7f8",
    "registry.example.com/mlody:mlody-core-worker-a3f1c2d4e5b6a7f8"
  ],
  "commit_sha": "a3f1c2d4e5b6a7f8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
  "input_targets": ["//mlody/lsp:lsp_server", "//mlody/core:worker"]
}
```

### Appendix D: Phased Flow

```
Invocation: mlody-image-builder TARGETS --sha <SHA> --registry <DEST> [--remote <URL>]
      |
      v
[Phase 1] Resolve git remote URL
  - --remote supplied? use it.
  - Otherwise: git remote get-url origin in cwd.
      |
      v
[Phase 2] Clone / cache
  - ~/.cache/mlody/builds/<SHA>/ exists? reuse (cache hit).
  - Otherwise: git fetch --depth 1 origin <SHA> + checkout.
  - Run bazel clean (NOT --expunge) inside clone.
      |
      v
[Phase 3] Bazel repository_rule (dynamic)
  - repo.bzl at monorepo root defines repository_rule "dynamic".
  - Rule reads TARGETS from ctx.os.environ (--repo_env=TARGETS=...).
  - Generates BUILD.bazel inside @dynamic_image with:
      filegroup → genrule (tar) → oci_image chain.
  - No direct filesystem writes to the cloned repo by the tool.
      |
      v
[Phase 4] bazel build @dynamic_image//:image --repo_env=TARGETS=<labels>
  - Bazel fetches the dynamic repo (triggering BUILD.bazel generation).
  - Attempt ALL targets before reporting failures.
      |
      v
[Phase 5] Derive tags
  - One tag per input target.
  - Tag = sanitize(label) + "-" + SHA[:16]
      |
      v
[Phase 6] Push image with all tags to --registry
  - Auth via ~/.docker/config.json abstraction.
      |
      v
[Success] Print JSON metadata to stdout. Exit 0.
[Failure] Print JSON error. Exit with failure-type-specific code.
```

---

**End of Requirements Document**
