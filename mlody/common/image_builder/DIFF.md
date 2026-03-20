# Diff-aware image building — design notes

## Current state

When `--dirty-policy=apply` is used, local changes (tracked diff + untracked
files) are applied on top of the cloned SHA before the Bazel build runs. The
clone cache is invalidated so the changes always land on a clean base. The diff
itself is not recorded anywhere in the resulting image.

## Phase 1 — OCI label tagging (implemented)

Short summary metadata is stored as OCI image labels so that `docker inspect` or
`crane config` can answer "was this image built dirty?":

| Label                                    | Value                          |
| ---------------------------------------- | ------------------------------ |
| `org.opencontainers.image.revision`      | full 40-char commit SHA        |
| `com.polymath.mlody.dirty`               | `true` / `false`               |
| `com.polymath.mlody.dirty_files_changed` | count of changed tracked files |
| `com.polymath.mlody.dirty_untracked`     | count of untracked files added |

The labels are embedded in the generated `_dynamic_image/BUILD.bazel` as the
`labels` attribute of the `oci_image` target.

## Phase 2 — Diff file in the image layer (future)

The full patch is baked into the image as `/etc/mlody-image-builder/dirty.patch`
so it can be extracted at any time:

```sh
docker run --rm <image> cat /etc/mlody-image-builder/dirty.patch
```

Implementation sketch:

1. `build.py` writes the patch string to `_dynamic_image/dirty.patch` in the
   clone when a diff was applied.
2. A second `pkg_tar` target (`:build_info_layer`) packages it at
   `/etc/mlody-image-builder/`.
3. `oci_image` receives both `tars = [":layer", ":build_info_layer"]`.

The patch string is already available in `CloneResult.applied_patch`.

## Phase 3 — Content-addressed cache keying (future)

Today, when diffs are applied the original clean clone is discarded and the
dirty clone takes its `<sha>/` slot. Future behaviour:

- Clean clone stays at `<sha>/` (never evicted by dirty builds).
- Dirty clone lives at `<sha>-<diff_sha16>/` where `diff_sha16` is the first 16
  hex chars of `sha1(patch + "\0".join(untracked_paths))`.
- This means a second `--dirty-policy=apply` run with the same uncommitted
  changes gets a cache hit for free, even though the clean clone is intact.
- The `CloneResult` returned to the pipeline carries the actual dest path, so
  callers need no changes.

### Cache eviction

To prevent unbounded growth from many dirty variants of the same SHA:

- A configurable `--max-dirty-clones-per-sha` (default 3) caps the number of
  `<sha>-*/` entries.
- When the cap is exceeded, the oldest (by mtime) dirty clone is removed before
  creating a new one.
- Author identity (git `user.email`) can optionally be mixed into the key to
  isolate per-developer caches on shared build hosts.
