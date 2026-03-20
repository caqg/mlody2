# mlody-image-builder

`mlody-image-builder` is a standalone Python binary that builds and pushes OCI
container images from one or more Bazel targets at a pinned commit SHA. It is a
reproducibility primitive: it always builds from a known, immutable source tree
rather than the caller's working directory. The tool clones the repository at
the exact SHA, invokes `bazel build` using a dynamically generated
`@dynamic_image` repository rule, derives per-target OCI tags, and pushes the
image to the specified registry using `crane`.

## Synopsis

```
mlody-image-builder [OPTIONS] TARGETS...
```

## Arguments and Options

| Flag / Argument | Required | Description                                                                                |
| --------------- | -------- | ------------------------------------------------------------------------------------------ |
| `TARGETS...`    | Yes      | One or more Bazel target labels, e.g. `//mlody/lsp:lsp_server //mlody/core:worker`.        |
| `--sha`         | Yes      | Full 40-digit hexadecimal commit SHA. The repository is cloned at exactly this commit.     |
| `--registry`    | Yes      | Container registry destination, e.g. `registry.example.com/mlody`.                         |
| `--remote`      | No       | Git remote URL override. Defaults to `git remote get-url origin` in the current directory. |

## Exit Codes

| Code | Category         | Condition                                                  |
| ---- | ---------------- | ---------------------------------------------------------- |
| 0    | Success          | All phases completed; image built and pushed successfully. |
| 1    | Unexpected error | Unhandled Python exception.                                |
| 2    | Clone failure    | Git remote URL resolution or shallow clone failed.         |
| 3    | Build failure    | `bazel build @dynamic_image//:image` failed.               |
| 4    | Push failure     | Image push to registry failed for any tag.                 |

## Example

```bash
mlody-image-builder \
  --sha a3f1c2d4e5b6a7f8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2 \
  --registry registry.example.com/mlody \
  //mlody/lsp:lsp_server //mlody/core:worker
```

Expected JSON output on stdout (exit 0):

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

## Cache Directory

Cloned repositories are cached at `~/.cache/mlody/builds/<SHA>/`. Each cache
entry is a shallow git clone of the repository at the given SHA.

To evict the entire cache:

```bash
rm -rf ~/.cache/mlody/builds/
```

To evict a single SHA:

```bash
rm -rf ~/.cache/mlody/builds/<40-char-sha>/
```

## Prerequisites

- `git` — must be on `PATH`
- `bazel` (Bazelisk recommended) — must be on `PATH`
- `crane` — must be on `PATH`; install from
  <https://github.com/google/go-containerregistry/releases>
- Docker credentials in `~/.docker/config.json` (or a compatible credential
  helper configured there)

## Structured Logging

The tool emits one JSON object per line to **stderr** for observability:

```
{"level": "info", "phase": "clone", "sha": "a3f1c2d4...", "cache": "miss", "dest": "..."}
{"level": "info", "phase": "build", "targets": ["//mlody/lsp:lsp_server"], "cmd": "bazel build ..."}
{"level": "info", "phase": "push", "tag": "mlody-lsp-lsp_server-a3f1c2d4", "registry": "..."}
```

All structured output goes to stderr. Only the final JSON result (success or
error) goes to stdout.
