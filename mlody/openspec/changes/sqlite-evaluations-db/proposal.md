## Why

Mlody currently writes one JSON file per evaluation run into
`~/.cache/mlody/evaluations/`. As run counts grow this flat-file store becomes
hard to query and offers no atomic durability. The immediate trigger is the need
to persist `value_description` — a human-readable summary of the model
configuration under test — which is not stored anywhere after a run completes.
Replacing the flat-file store with a single SQLite database gives every run a
durable, indexed, audit-quality record without adding any infrastructure.

## What Changes

- Add `mlody/db/` package containing `evaluations.py` (schema DDL constant,
  `open_db()` connection factory, `write_evaluation()` insert function) and
  `local_diff.py` (`compute_local_diff_sha()` helper).
- Add `mlody/db/evaluations_test.py` and `mlody/db/local_diff_test.py` with unit
  and integration tests.
- Add `mlody/db/BUILD.bazel` with `o_py_library` and `o_py_test` targets
  (managed via `bazel run :gazelle`).
- Add a UUID v7 library (`uuid-utils`) to `pyproject.toml` and re-pin via
  `o-repin`.
- The existing JSON write path is left intact; no existing files are touched or
  migrated.

## Capabilities

### New Capabilities

- `evaluations-db`: Every evaluation run is recorded as a row in
  `~/.cache/mlody/mlody.sqlite` with a UUID v7 primary key, full provenance
  metadata (username, hostname, requested_ref, resolved_sha, resolved_at, repo,
  local_only), `value_description`, and a `local_diff_sha` fingerprint of the
  relevant source subtrees at run time.

### Modified Capabilities

_(none)_

## Impact

- **New files:** `mlody/db/__init__.py`, `mlody/db/evaluations.py`,
  `mlody/db/local_diff.py`, `mlody/db/evaluations_test.py`,
  `mlody/db/local_diff_test.py`, `mlody/db/BUILD.bazel`
- **Modified files:** `pyproject.toml` (add `uuid-utils`), lock files
  (regenerated via `o-repin`)
- **Dependencies:** Python stdlib `sqlite3`, `hashlib`, `pathlib`, `subprocess`;
  `uuid-utils` (UUID v7 generation)
- **Downstream:** Any evaluation entry point that currently calls
  `write_metadata()` in `mlody/resolver/cache.py` gains a corresponding
  `write_evaluation()` call; the exact call site is determined by the
  implementing agent
- **APIs:**
  `write_evaluation(db_path, *, username, hostname, requested_ref, resolved_sha, resolved_at, repo, local_only, value_description) -> str`
  (returns UUID v7 string)
