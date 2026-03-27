# Tasks: sqlite-evaluations-db

## Task 1 — `mlody/db/` package skeleton and `local_diff.py`

Create the new `mlody/db/` package:

- `mlody/db/__init__.py` (empty)
- `mlody/db/local_diff.py` with:
  - `get_repo_root() -> Path | None` — runs `git rev-parse --show-toplevel`;
    returns `None` and logs a warning on any failure
  - `compute_local_diff_sha(repo_root: Path | None) -> str | None` — Method A
    per spec §2.1: enumerate files under `mlody/` and
    `common/python/starlarkish/`, sort by repo-relative path, hash each file,
    hash the combined `path:digest\n` string; return 64-char hex or `None` if
    `repo_root` is `None`
- `mlody/db/local_diff_test.py` with all 10 unit tests from spec §Testing
  Strategy (`test_get_repo_root_success`, `test_get_repo_root_git_failure`,
  `test_get_repo_root_git_not_found`, `test_compute_none_repo_root`,
  `test_compute_both_subtrees_absent`, `test_compute_one_subtree_absent`,
  `test_compute_deterministic`, `test_compute_untracked_file_changes_hash`,
  `test_compute_modified_file_changes_hash`,
  `test_compute_sort_order_independent`)
- Run `bazel run :gazelle` to generate `mlody/db/BUILD.bazel`
- Run `bazel test //mlody/db:local_diff_test` — all tests must pass

Status: [x]

---

## Task 2 — Add `uuid-utils` dependency and repin

- Add `uuid-utils` to the `dependencies` list in `pyproject.toml` (no version
  pin; comment explaining it is the UUID v7 library)
- Run `o-repin` to regenerate lock files
- Verify no dependency conflict in the updated lock; if conflict exists, fall
  back to `uuid7` instead
- Run `bazel run :gazelle` again to pick up the new `@pip//uuid_utils` dep in
  `mlody/db/BUILD.bazel`

Status: [x]

---

## Task 3 — `mlody/db/evaluations.py`

Create `mlody/db/evaluations.py` with:

- `SCHEMA_DDL: Final[str]` — the full
  `CREATE TABLE IF NOT EXISTS evaluations (...)` DDL constant (exact schema from
  spec §1.1; `repo` is nullable, `completed_at` and `local_diff_sha` nullable,
  all others `NOT NULL`)
- `open_db(db_path: Path) -> sqlite3.Connection` — creates parent dirs with
  `0o700`, opens the DB, `os.chmod(db_path, 0o600)`, enables WAL mode, runs
  `SCHEMA_DDL`, commits, returns connection
- `write_evaluation(conn: sqlite3.Connection, *, username: str, hostname: str, requested_ref, resolved_sha, resolved_at, repo, local_only, value_description, local_diff_sha=None, created_at=None) -> str`
  — `conn`, `username`, and `hostname` are supplied by the caller (no internal
  computation of username/hostname); validates inputs (40-char `resolved_sha`,
  non-empty `value_description`), generates UUID v7 via `uuid_utils.uuid7()`,
  inserts via parameterized query, commits, returns the UUID string

Status: [x]

---

## Task 4 — `mlody/db/evaluations_test.py`

Create `mlody/db/evaluations_test.py` with all 14 tests from spec §Testing
Strategy:

- `test_open_db_creates_table`
- `test_open_db_idempotent`
- `test_open_db_wal_mode`
- `test_open_db_file_permissions`
- `test_write_evaluation_returns_uuid7`
- `test_write_evaluation_row_count`
- `test_write_evaluation_all_columns`
- `test_write_evaluation_twice_distinct_ids`
- `test_write_evaluation_nullable_repo`
- `test_write_evaluation_nullable_local_diff_sha`
- `test_write_evaluation_created_at_default`
- `test_write_evaluation_empty_value_description_raises`
- `test_write_evaluation_bad_resolved_sha_raises`
- `test_write_evaluation_json_files_untouched`

Run `bazel test //mlody/db:evaluations_test` — all tests must pass.

Status: [x]

---

## Task 5 — Call site wiring

Identify the evaluation entry point in the mlody codebase (the site that
currently calls `write_metadata()` in `mlody/resolver/cache.py` or equivalent)
and add:

1. A call to `compute_local_diff_sha(get_repo_root())` to obtain
   `local_diff_sha`
2. A call to `open_db(Path.home() / ".cache" / "mlody" / "mlody.sqlite")` to
   obtain `conn`
3. A call to `write_evaluation(conn, ...)` with all required fields (username
   from `os.getlogin()` with `pwd` fallback, hostname from
   `socket.gethostname()`, and provenance fields from `-meta.json` or the
   resolver result)
4. Connection closed in a `finally` block (or via context manager)

Run `bazel test //mlody/...` — full mlody suite must pass.

Status: [x]

---

## Task 6 — Lint pass

Run `bazel build --config=lint //mlody/db/...` and fix all warnings and errors.

Status: [x]
