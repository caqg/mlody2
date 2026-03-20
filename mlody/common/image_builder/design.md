# mlody-image-builder — Architecture Design

## 1. Overview

`mlody-image-builder` is a standalone Python binary that builds and pushes OCI
container images from one or more Bazel targets at a pinned commit SHA. The tool
is a reproducibility primitive: it always builds from a known, immutable source
tree rather than the caller's working directory.

Invocation shape:

```
mlody-image-builder [--sha SHA] [--registry REGISTRY] [--remote URL] TARGETS...
```

The tool is completely decoupled from the existing `mlody` CLI and ships as its
own `o_py_binary` target at `//mlody/common/image-builder:mlody_image_builder`.

---

## 2. Six-Phase Pipeline

The tool executes six sequential phases. Every phase is a discrete, testable
unit. Failure in a phase produces a specific exit code and a JSON error object
on stdout; execution does not continue to subsequent phases (except within Phase
4, where all targets are attempted before reporting).

```
Invocation
    |
    v
[Phase 1] Remote resolution
    Resolve the git remote URL:
    - --remote supplied -> use it directly
    - otherwise -> run `git remote get-url origin` in cwd
    |
    v
[Phase 2] Clone / cache
    Cache key: full 40-char SHA
    Cache root: ~/.cache/mlody/builds/
    - hit  -> reuse directory; run `bazel clean` (non-expunge)
    - miss -> shallow-fetch + checkout; run `bazel clean`
    - Lock: O_CREAT|O_EXCL sentinel file per SHA (same pattern as resolver)
    |
    v
[Phase 3] Bazel build via repository_rule
    Invoke inside the cloned repo:
      bazel build @dynamic_image//:image \
        --repo_env=TARGETS="//foo:bar,//baz:qux"
    The `dynamic` repository_rule (defined in repo.bzl at the monorepo root of
    the cloned repo) reads TARGETS from ctx.os.environ and generates a
    BUILD.bazel inside the @dynamic_image external repository containing a
    filegroup -> pkg_tar -> oci_image chain. The tool does NOT write files
    directly to the cloned repo filesystem.
    |
    v
[Phase 4] Tag derivation
    One tag per input Bazel target:
      tag = sanitize(label) + "-" + sha[:16]
    Sanitization: replace [^A-Za-z0-9_.-] with "-"; enforce 128-char max.
    |
    v
[Phase 5] Image push
    Push the built OCI image to --registry with all derived tags.
    Auth: read from ~/.docker/config.json via a replaceable RegistryAuth
    abstraction.
    |
    v
[Success] JSON metadata -> stdout. Exit 0.
[Failure] JSON error    -> stdout. Specific exit code per failure category.
```

---

## 3. Component Breakdown

```
mlody/common/image-builder/
    __main__.py          Entry point; wires click CLI to pipeline
    pipeline.py          Top-level orchestrator; calls each phase in order
    phases/
        remote.py        Phase 1: remote URL resolution
        clone.py         Phase 2: cache lookup, shallow clone, bazel clean
        build.py         Phase 3: bazel build invocation
        tags.py          Phase 4: tag derivation and sanitization
        push.py          Phase 5: image push
    auth.py              RegistryAuth abstraction + DockerConfigAuth impl
    errors.py            Exit-code constants and typed error classes
    output.py            JSON result/error serialization (stdout)
    log.py               Structured stderr logging (JSON lines)
    BUILD.bazel          Gazelle-managed; o_py_binary + o_py_library + o_py_test
    README.md            CLI usage, exit code table
    REQUIREMENTS.md      (already exists)
    design.md            (this file)
    spec.md              Detailed implementation spec
    tests/
        test_tags.py     Unit tests for tag sanitization
        test_clone.py    Unit tests for cache/clone logic (pyfakefs)
        test_build.py    Unit tests for bazel invocation logic
        test_errors.py   Unit tests for exit code mapping
        test_output.py   Unit tests for JSON serialization
```

### 3.1 `__main__.py`

Thin entry point. Defines the `click` command with all arguments (`TARGETS`,
`--sha`, `--registry`, `--remote`). Catches pipeline errors, writes JSON output,
and calls `sys.exit` with the correct exit code. Never contains business logic.

### 3.2 `pipeline.py`

`ImageBuilderPipeline` dataclass / class that holds parsed inputs and
coordinates the phase sequence. Returns a typed result or raises a
`BuilderError` subclass. Keeping the pipeline separate from the click entry
point allows it to be tested via `click.testing.CliRunner` and also directly
from unit tests.

### 3.3 `phases/remote.py`

`resolve_remote(remote_override: str | None, cwd: Path) -> str`

Runs `git remote get-url origin` via `subprocess.run` when `remote_override` is
`None`, otherwise returns `remote_override`. Raises `RemoteResolutionError` on
subprocess failure. No network I/O; just a local git query.

### 3.4 `phases/clone.py`

`ensure_clone(sha: str, remote_url: str, cache_root: Path) -> Path`

Implements the check-then-clone-with-lock pattern drawn directly from
`mlody/resolver/cache.py` and `mlody/resolver/git_client.py`. Key differences
from the resolver:

- Cache sentinel is a `.git/HEAD` file (the clone is a full worktree, not an
  mlody workspace, so `mlody/roots.mlody` is absent and cannot serve as
  sentinel).
- Shallow fetch: `git fetch --depth 1 origin <SHA>` followed by
  `git checkout <SHA>`. No sparse checkout — image builds need the full tree.
- After the cache-hit or fresh-clone path, always runs `bazel clean` (not
  `--expunge`) inside the clone directory.
- Lock file: `~/.cache/mlody/builds/<SHA>.lock` using O_CREAT|O_EXCL.

The clone module does NOT import from `mlody.resolver` directly to avoid an
undeclared Bazel dependency cycle. It reuses the same patterns but is
self-contained. The `GitClient`-style subprocess wrapper is reproduced locally
as a thin private helper.

### 3.5 `phases/build.py`

`run_bazel_build(clone_dir: Path, targets: list[str]) -> BazelResult`

Invokes:

```
bazel build @dynamic_image//:image \
    --repo_env=TARGETS="//foo:bar,//baz:qux"
```

`targets` is joined with commas for `--repo_env`. Returns stdout/stderr for
diagnostic inclusion in JSON error output. Raises `BuildError` on non-zero exit.
Does NOT implement "build all before reporting" at this phase level — the single
`@dynamic_image//:image` target already assembles all inputs in one Bazel
invocation; the "no early abort" requirement (FR-011) is satisfied because the
`repo.bzl` `repository_rule` produces a single combined target.

### 3.6 `phases/tags.py`

`derive_tags(targets: list[str], sha: str) -> list[str]`

Pure function. For each target:

1. Strip leading `//`, replace `:` with `-`, replace remaining `[^A-Za-z0-9_.-]`
   characters with `-`.
2. Append `-` + `sha[:16]`.
3. Truncate to 128 characters.
4. Ensure the first character is `[A-Za-z0-9_]` (strip leading dashes from the
   sanitized path portion; the `//` strip guarantees this in practice for
   well-formed labels).

Example: `//mlody/lsp:lsp_server` + SHA starting `abcdef1234567890` →
`mlody-lsp-lsp_server-abcdef1234567890`.

### 3.7 `phases/push.py`

`push_image(clone_dir: Path, registry: str, tags: list[str], auth: RegistryAuth) -> PushResult`

Uses `rules_oci`'s `oci_push` CLI or the `crane` / `skopeo` approach. Because
`rules_oci` builds images into a Bazel output directory as a directory layout
(OCI image layout), the push step must either:

**Option A (preferred):** Invoke the `oci_push` target that `rules_oci`
generates alongside `oci_image`. This runs as a Bazel `bazel run` call:
`bazel run @dynamic_image//:image.push -- --tag <tag>` for each tag. This keeps
the push logic inside Bazel's own tooling and avoids a separate OCI library
dependency in the Python binary.

**Option B (fallback):** Use `crane` or `skopeo` as a subprocess to push the OCI
image layout directory produced by the build. Requires `crane` or `skopeo` on
PATH.

**Decision: Option A** — Defer to `rules_oci`'s own push mechanism, which
handles credential resolution from `~/.docker/config.json` natively. The Python
layer only computes tags and invokes `bazel run`. This is simpler and less
surface area. The `RegistryAuth` abstraction wraps the environment variables /
credential file path passed to the `bazel run` invocation.

### 3.8 `auth.py`

```python
class RegistryAuth(Protocol):
    def env_vars(self) -> dict[str, str]: ...

class DockerConfigAuth:
    """Reads ~/.docker/config.json; passes DOCKER_CONFIG env var to bazel run."""
    def __init__(self, config_path: Path | None = None) -> None: ...
    def env_vars(self) -> dict[str, str]: ...
```

The `Protocol` is the abstraction point. Future implementations (vault, explicit
credentials) swap in a different concrete class without touching calling code.
Credentials must never be logged or included in JSON output.

### 3.9 `errors.py`

```python
class ExitCode(enum.IntEnum):
    SUCCESS        = 0
    CLONE_FAILURE  = 2
    BUILD_FAILURE  = 3
    PUSH_FAILURE   = 4

class BuilderError(Exception):
    exit_code: ExitCode
    ...

class CloneError(BuilderError): ...
class BazelBuildError(BuilderError): ...
class PushError(BuilderError): ...
```

Exit code 1 is reserved for unexpected / unhandled errors (Python's default).
Codes 2–4 are the tool's documented failure categories.

### 3.10 `output.py`

Two top-level functions:

```python
def emit_success(result: SuccessResult) -> None:
    """Print JSON success payload to stdout."""

def emit_error(error: BuilderError) -> None:
    """Print JSON error payload to stdout."""
```

All JSON output goes to stdout. Stderr is used only by `log.py`.

### 3.11 `log.py`

```python
def log(level: str, phase: str, **kwargs: object) -> None:
    """Emit one JSON line to stderr. Never emits to stdout."""
```

Produces `{"level": "info", "phase": "clone", "sha": "..."}` style lines. No
`rich` spinners or progress bars. `rich` is not used in this tool at all — the
observability requirement explicitly prohibits interactive UI.

---

## 4. Key Architectural Decisions

### 4.1 No direct filesystem BUILD.bazel injection

The tool does NOT write a `BUILD.bazel` into the cloned repo. Instead, a
`repository_rule` named `dynamic` in `repo.bzl` at the monorepo root generates
the `@dynamic_image` external repository at Bazel fetch time. This was the
explicitly chosen design in OQ-4. Benefits:

- No filesystem side effects on the cloned repo.
- The generated dependency graph is fully declared inside Bazel's own model.
- Rebuilds with different `TARGETS` automatically re-fetch the `dynamic` repo
  (Bazel treats `--repo_env` changes as fetch invalidations for
  `repository_rule`s that read `ctx.os.environ`).

### 4.2 Clone module is self-contained, not a re-export of resolver

`mlody/resolver` is tightly coupled to the `mlody` workspace concept (sentinel
is `mlody/roots.mlody`, sparse checkout covers only `mlody/`, etc.). The image
builder needs a full non-sparse checkout for Bazel builds. Importing
`mlody.resolver` directly would also create a Bazel dependency on
`//mlody/resolver:resolver_lib` which drags in `//mlody/core:core_lib`. The
clone phase reimplements the lock + check + clone pattern (the pattern being the
reusable idea from NFR-009, not the implementation) in a small, self-contained
module.

### 4.3 Push via `bazel run`, not a Python OCI library

Using `bazel run @dynamic_image//:image.push` delegates credential resolution
and OCI protocol handling to `rules_oci`, which already has a tested, maintained
push implementation. This avoids adding a Python OCI client library (e.g.,
`oci`, `docker`) as a dependency and keeps the Python binary's surface area
small.

### 4.4 Single `@dynamic_image//:image` target satisfies "build all"

FR-011 requires attempting all targets before reporting failures. Because all
input targets are assembled into one `@dynamic_image//:image` by the `repo.bzl`
repository_rule, a single `bazel build` invocation covers all of them. Bazel's
own analysis-phase error reporting will surface all failures within that single
invocation. This satisfies the intent of FR-011 without requiring the Python
layer to loop over targets individually.

### 4.5 No `rich` UI

NFR-012 explicitly prohibits spinners, progress bars, and interactive console
UI. The tool emits only JSON lines to stderr. `rich` is not in the dependency
list. Structured JSON logging to stderr (via `log.py`) is the only observability
surface.

### 4.6 `rules_oci` and `rules_pkg` in the cloned repo

The tool assumes `rules_oci` and `rules_pkg` are present in the cloned repo's
`MODULE.bazel`. This is an explicit assumption (Section 2.3 of REQUIREMENTS.md).
The current Omega monorepo does not yet have these dependencies — they must be
added to `MODULE.bazel` as part of this change (see spec.md for the exact
additions). This is a prerequisite for the `repo.bzl` rule to work correctly.

---

## 5. Data Flow

```
CLI args (TARGETS, --sha, --registry, [--remote])
    |
    +--[Phase 1]--> remote_url: str
    |
    +--[Phase 2]--> clone_dir: Path (~/.cache/mlody/builds/<SHA>/)
    |
    +--[Phase 3]--> BazelResult (OCI image layout dir within Bazel output)
    |
    +--[Phase 4]--> tags: list[str]
    |
    +--[Phase 5]--> PushResult (image_digest, image_references)
    |
    +--[output]--> JSON to stdout; exit 0
```

On failure at any phase:

```
BuilderError (exit_code, message, context)
    |
    +--[output]--> JSON error to stdout; sys.exit(exit_code)
```

---

## 6. Exit Code Table

| Code | Category         | Condition                                     |
| ---- | ---------------- | --------------------------------------------- |
| 0    | Success          | All phases completed; image pushed            |
| 1    | Unexpected error | Unhandled exception (Python default)          |
| 2    | Clone failure    | git remote resolution or shallow clone failed |
| 3    | Build failure    | `bazel build @dynamic_image//:image` failed   |
| 4    | Push failure     | `bazel run .../image.push` failed for any tag |

---

## 7. repo.bzl Design (Starlark)

The `repo.bzl` file lives at the monorepo root of the cloned repository. It is
already expected to be present in the cloned repo (assumption A in Section 2.3).
For older SHAs that predate its introduction, the tool emits a clear error (see
Risk R-003 in REQUIREMENTS.md).

Sketch of the generated `BUILD.bazel` inside `@dynamic_image`:

```python
# Generated by dynamic repository_rule; do not edit.
load("@rules_oci//oci:defs.bzl", "oci_image", "oci_push")
load("@rules_pkg//pkg:tar.bzl", "pkg_tar")

filegroup(
    name = "artifacts",
    srcs = [
        "//mlody/lsp:lsp_server",
        "//mlody/core:worker",
    ],
)

pkg_tar(
    name = "layer",
    srcs = [":artifacts"],
)

oci_image(
    name = "image",
    base = "@distroless_base",
    tars = [":layer"],
)

oci_push(
    name = "image_push",
    image = ":image",
    repository = "<registry>",
)
```

The `dynamic` `repository_rule` in `repo.bzl`:

```python
def _dynamic_impl(ctx):
    targets = ctx.os.environ.get("TARGETS", "")
    target_list = [t.strip() for t in targets.split(",") if t.strip()]
    # ... generate BUILD.bazel content from target_list ...
    ctx.file("BUILD.bazel", content)
    ctx.file("WORKSPACE", "")

dynamic = repository_rule(
    implementation = _dynamic_impl,
    environ = ["TARGETS"],
)
```

The `environ = ["TARGETS"]` declaration ensures Bazel re-fetches the repository
when the `TARGETS` value changes, making the cache-invalidation implicit.

---

## 8. Fit Within the Omega Monorepo

- New directory: `mlody/common/image-builder/`
- New Bazel target: `//mlody/common/image-builder:mlody_image_builder`
  (`o_py_binary`)
- New Bazel targets: `//mlody/common/image-builder:image_builder_lib`
  (`o_py_library`) + per-module test targets (`o_py_test`)
- `MODULE.bazel` additions: `rules_oci`, `rules_pkg` (prerequisite; see spec.md)
- No changes to `mlody/cli/`, `mlody/resolver/`, or any other existing module.
- Gazelle manages `BUILD.bazel` — do not hand-edit.
