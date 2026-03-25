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

* Actual working example

```sh
bazel run  mlody/common/image_builder:mlody_image_builder --spawn_strategy=standalone  -- //repo/smoketest/python/external_import:external_import --sha $(git rev-parse HEAD) --registry localhost:5000/mlody
```
```sh
INFO: Analyzed target //mlody/common/image_builder:mlody_image_builder (0 packages loaded, 0 targets configured).
INFO: Found 1 target...
Target //mlody/common/image_builder:mlody_image_builder up-to-date:
  bazel-bin/mlody/common/image_builder/mlody_image_builder
  bazel-bin/mlody/common/image_builder/mlody_image_builder.venv.pth
INFO: Elapsed time: 0.067s, Critical Path: 0.01s
INFO: 1 process: 1 internal.
INFO: Build completed successfully, 1 total action
INFO: Running command line: bazel-bin/mlody/common/image_builder/mlody_image_builder <args omitted>
{"level": "info", "phase": "remote", "remote_url": "poly-repo.github.com:poly-repo/omega.git", "source": "git remote get-url origin"}
{"level": "info", "phase": "clone", "sha": "084bd57587b0937ad07cb63e66287f56fc1c8dd9", "cache": "hit", "dest": "/home/mav/.cache/mlody/builds/084bd57587b0937ad07cb63e66287f56fc1c8dd9"}
{"level": "info", "phase": "clone", "sha": "084bd57587b0937ad07cb63e66287f56fc1c8dd9", "step": "bazel_clean", "dest": "/home/mav/.cache/mlody/builds/084bd57587b0937ad07cb63e66287f56fc1c8dd9"}
{"level": "info", "phase": "build", "targets": ["//repo/smoketest/python/external_import"], "cmd": "bazel build //_dynamic_image:image"}
{"level": "info", "phase": "build", "status": "success", "targets": ["//repo/smoketest/python/external_import"]}
{"level": "info", "phase": "push", "tag": "repo-smoketest-python-external_import-084bd57587b0937a", "registry": "localhost:5000/mlody"}
{"level": "info", "phase": "push", "status": "success", "digest": "sha256:8be19e4bae5e3f8eed1b1ab9fa74c1a29250e3c148f95fddcd3d9b9e49d02f9e", "references": ["localhost:5000/mlody:repo-smoketest-python-external_import-084bd57587b0937a"]}
{
  "image_digest": "sha256:8be19e4bae5e3f8eed1b1ab9fa74c1a29250e3c148f95fddcd3d9b9e49d02f9e",
  "image_references": [
    "localhost:5000/mlody:repo-smoketest-python-external_import-084bd57587b0937a"
  ],
  "commit_sha": "084bd57587b0937ad07cb63e66287f56fc1c8dd9",
  "input_targets": [
    "//repo/smoketest/python/external_import"
  ]
}
```
In case you want to reproduce this at a later stage you can use directly the sha `084bd57587b0937ad07cb63e66287f56fc1c8dd9`

```sh
docker pull localhost:5000/mlody:repo-smoketest-python-external_import-external_import-084bd57587b0937a
docker run --rm localhost:5000/mlody:repo-smoketest-python-external_import-external_import-084bd57587b0937a  /repo/smoketest/python/external_import/external_import
```
```sh
 _   _      _ _         __        __         _     _   _ 
| | | | ___| | | ___    \ \      / /__  _ __| | __| | | |
| |_| |/ _ \ | |/ _ \    \ \ /\ / / _ \| '__| |/ _` | | |
|  _  |  __/ | | (_) |    \ V  V / (_) | |  | | (_| | |_|
|_| |_|\___|_|_|\___( )    \_/\_/ \___/|_|  |_|\__,_| (_)
                    |/                                   

```



  706  docker pull localhost:5000/mlody:repo-smoketest-python-external_import-external_import-084bd57587b0937a
  707  docker run --rm localhost:5000/mlody-test:repo-smoketest-python-external_import-external_import-084bd57587b0937a  /repo/smoketest/python/external_import/external_import
  708  docker run --rm localhost:5000/mlody:repo-smoketest-python-external_import-external_import-084bd57587b0937a  /repo/smoketest/python/external_import/external_import
  709  history
