# SPEC: evaluations-db

**Version:** 1.0 **Date:** 2026-03-27 **Architect:** @vitruvius **Status:**
Draft **Requirements:**
`mlody/openspec/changes/sqlite-evaluations-db/REQUIREMENTS.md` **Design:**
`mlody/openspec/changes/sqlite-evaluations-db/design.md`

---

## Executive Summary

This change introduces a single SQLite database at `~/.cache/mlody/mlody.sqlite`
to replace the flat-file evaluation store. Every evaluation run produces exactly
one new row in an `evaluations` table. The row captures provenance metadata
(username, hostname, requested_ref, resolved_sha, resolved_at, repo,
local_only), the new `value_description` field, and a `local_diff_sha`
fingerprint of the local source subtrees at run time. Existing JSON files are
left untouched.

The implementation is a new `mlody/db/` package with two modules:

- `evaluations.py` — schema DDL, connection factory, and `write_evaluation()`
- `local_diff.py` — `compute_local_diff_sha()` helper

No ORM, no network dependency, no schema migration required at this stage.

---

## Architecture Overview

```
mlody/db/
  __init__.py           (empty — package marker)
  evaluations.py        (open_db, SCHEMA_DDL, write_evaluation)
  local_diff.py         (compute_local_diff_sha)
  evaluations_test.py   (unit + integration)
  local_diff_test.py    (unit)
  BUILD.bazel           (gazelle-managed)
```

### Data flow

```
Evaluation entry point
  |
  +-- compute_local_diff_sha(repo_root)
  |     subprocess: git rev-parse --show-toplevel
  |     if repo_root is None -> return None, log warning
  |     enumerate files under mlody/ and common/python/starlarkish/
  |     sort by repo-relative path
  |     sha256(concat of "path:file_hex_digest\n") -> 64-char hex
  |
  +-- open_db(db_path)
  |     Path(db_path).parent.mkdir(parents=True, exist_ok=True)
  |     sqlite3.connect(db_path)
  |     os.chmod(db_path, 0o600)
  |     PRAGMA journal_mode=WAL
  |     CREATE TABLE IF NOT EXISTS evaluations (...)
  |     return connection
  |
  +-- write_evaluation(conn, *, username, hostname, ...)
        uuid7() -> id
        datetime.now(UTC).isoformat() -> created_at (if not supplied)
        INSERT INTO evaluations VALUES (?, ...) [parameterized]
        conn.commit()
        return id (UUID v7 string)
```

---

## Technical Stack

| Concern         | Choice                      | Rationale                                      |
| --------------- | --------------------------- | ---------------------------------------------- |
| Language        | Python 3.13.2               | Hermetic via rules_python                      |
| DB driver       | stdlib `sqlite3`            | NFR-COMPAT-002; no ORM needed                  |
| UUID v7         | `uuid-utils`                | Rust-backed, MIT licence, no transitive deps   |
| Hashing         | stdlib `hashlib.sha256`     | No extra dependency                            |
| Git subprocess  | stdlib `subprocess`         | Consistent with `mlody/resolver/git_client.py` |
| Build rules     | `o_py_library`, `o_py_test` | Omega convention                               |
| Test filesystem | `pyfakefs`                  | Already in `pyproject.toml`                    |

---

## Detailed Component Specifications

### 1. `mlody/db/evaluations.py`

#### 1.1 `SCHEMA_DDL` constant

A module-level string constant containing the full `CREATE TABLE IF NOT EXISTS`
DDL. Expressed as a named constant so the schema is readable without running the
program (NFR per section 15.2 of requirements).

```python
SCHEMA_DDL: Final[str] = """
CREATE TABLE IF NOT EXISTS evaluations (
    id                TEXT    PRIMARY KEY NOT NULL,
    created_at        TEXT    NOT NULL,
    completed_at      TEXT,
    username          TEXT    NOT NULL,
    hostname          TEXT    NOT NULL,
    requested_ref     TEXT    NOT NULL,
    resolved_sha      TEXT    NOT NULL,
    resolved_at       TEXT    NOT NULL,
    repo              TEXT,
    local_only        INTEGER NOT NULL,
    value_description TEXT    NOT NULL,
    local_diff_sha    TEXT
);
"""
```

Notes:

- `repo` is `NULLABLE` (design decision D-6: failure to obtain remote URL must
  not block the insert).
- `completed_at` and `local_diff_sha` are nullable per requirements.
- All other columns are `NOT NULL`.
- SQLite type affinity: `TEXT` for strings and ISO 8601 timestamps; `INTEGER`
  for the boolean `local_only` (0 or 1).
- Future schema changes must use `ALTER TABLE ADD COLUMN` only; dropping and
  recreating the table is forbidden.

#### 1.2 `open_db(db_path: Path) -> sqlite3.Connection`

Opens (or creates) the SQLite database at `db_path` and returns a ready
connection.

Responsibilities:

1. `db_path.parent.mkdir(parents=True, exist_ok=True)` — ensures
   `~/.cache/mlody/` exists (design D-10).
2. `sqlite3.connect(db_path)` — creates the file if absent.
3. `os.chmod(db_path, 0o600)` — enforces owner-only permissions (NFR-SEC-001).
   Called unconditionally; idempotent on already-correct files.
4. `conn.execute("PRAGMA journal_mode=WAL")` — enables WAL mode (design D-8).
5. `conn.execute(SCHEMA_DDL)` — idempotent table creation (FR-002).
6. `conn.commit()` — commits the DDL.
7. Returns the open connection.

Signature:

```python
def open_db(db_path: Path) -> sqlite3.Connection: ...
```

The caller is responsible for closing the connection. A context manager
(`with open_db(...) as conn`) is not required at this stage but is not
prohibited.

#### 1.3 `write_evaluation(conn, *, username, hostname, requested_ref, resolved_sha, resolved_at, repo, local_only, value_description, local_diff_sha, created_at) -> str`

Inserts one row into `evaluations` and returns the UUID v7 string primary key.

Full signature:

```python
def write_evaluation(
    conn: sqlite3.Connection,
    *,
    username: str,
    hostname: str,
    requested_ref: str,
    resolved_sha: str,
    resolved_at: str,
    repo: str | None,
    local_only: bool,
    value_description: str,
    local_diff_sha: str | None = None,
    created_at: str | None = None,
) -> str: ...
```

Behaviour:

- Generates a UUID v7 string via `str(uuid_utils.uuid7())` (FR-004).
- If `created_at` is `None`, sets it to `datetime.now(timezone.utc).isoformat()`
  (FR-003 business rule).
- Inserts all fields using a fully parameterized
  `INSERT INTO evaluations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
  statement (NFR-SEC-002).
- Calls `conn.commit()`.
- Returns the UUID v7 string (`id`).
- Does **not** catch exceptions — any DB error propagates to the caller
  (NFR-AVAIL-001).

Validation (raises `ValueError` before the INSERT if violated):

- `resolved_sha` must be exactly 40 hexadecimal characters.
- `value_description` must be a non-empty string.
- `local_only` must be a `bool` (stored as `int(local_only)` in the DB).

### 2. `mlody/db/local_diff.py`

#### 2.1 `compute_local_diff_sha(repo_root: Path | None) -> str | None`

Computes a deterministic SHA-256 fingerprint of the source subtrees relevant to
Mlody evaluation reproducibility.

Signature:

```python
def compute_local_diff_sha(repo_root: Path | None) -> str | None: ...
```

Algorithm (Method A — design decision D-7):

1. If `repo_root` is `None`, log a warning and return `None`.
2. Define the two subtrees: `repo_root / "mlody"` and
   `repo_root / "common" / "python" / "starlarkish"`.
3. For each subtree that exists, recursively enumerate all files (using
   `Path.rglob("*")`, files only). If a subtree does not exist, treat it as
   empty (zero files) — do not return `None`.
4. Collect all `(repo_relative_path_str, file_bytes)` pairs across both
   subtrees.
5. Sort the collected pairs by `repo_relative_path_str` (lexicographic).
6. For each sorted pair, compute `hashlib.sha256(file_bytes).hexdigest()`.
7. Build the combined string:
   `"\n".join(f"{path}:{digest}" for path, digest in sorted_pairs)`.
8. Return `hashlib.sha256(combined_string.encode()).hexdigest()` — a
   64-character hex string.

Edge cases:

- Both subtrees absent: step 3 produces zero pairs; step 7 produces `""`; step 8
  returns the SHA-256 of a single newline byte (`\n`) (not `None`).
- `repo_root` is `None`: returns `None` (step 1).

#### 2.2 `get_repo_root() -> Path | None`

A helper that runs `git rev-parse --show-toplevel` and returns the path, or
`None` on failure (non-zero exit, `FileNotFoundError`, any subprocess error).
Logs a warning on failure.

Signature:

```python
def get_repo_root() -> Path | None: ...
```

This function is separated from `compute_local_diff_sha` so that callers that
already have the repo root (e.g., `GitClient` users) can skip the subprocess
call.

---

## Data Architecture

### Schema

See `SCHEMA_DDL` in section 1.1.

### Column notes

| Column              | Type    | Nullable | Notes                                                                                                                |
| ------------------- | ------- | -------- | -------------------------------------------------------------------------------------------------------------------- |
| `id`                | TEXT    | No       | UUID v7 string; time-ordered primary key                                                                             |
| `created_at`        | TEXT    | No       | ISO 8601 UTC (`datetime.now(UTC).isoformat()`)                                                                       |
| `completed_at`      | TEXT    | Yes      | NULL until a future change populates it                                                                              |
| `username`          | TEXT    | No       | `os.getlogin()` or `pwd.getpwuid(os.getuid()).pw_name` as fallback                                                   |
| `hostname`          | TEXT    | No       | `socket.gethostname()`                                                                                               |
| `requested_ref`     | TEXT    | No       | Committoid as typed by user; from `-meta.json`                                                                       |
| `resolved_sha`      | TEXT    | No       | Full 40-char git SHA; composite key component                                                                        |
| `resolved_at`       | TEXT    | No       | ISO 8601 UTC wall-clock timestamp at evaluation completion; currently set just before result output (see design D-5) |
| `repo`              | TEXT    | Yes      | `git remote get-url origin`; NULL on failure                                                                         |
| `local_only`        | INTEGER | No       | 0 or 1                                                                                                               |
| `value_description` | TEXT    | No       | Non-empty string                                                                                                     |
| `local_diff_sha`    | TEXT    | Yes      | 64-char hex or NULL if repo root unavailable                                                                         |

### Composite workspace key

The logical composite key `(resolved_sha, local_diff_sha)` uniquely identifies a
workspace state. `resolved_sha` is the committed state; `local_diff_sha`
captures local edits on top. No unique constraint is placed on this pair — a
user may run evaluations multiple times against the same workspace state, each
producing its own row.

### Data retention

No automatic deletion or archival. Rows accumulate indefinitely. A future change
will define a retention policy.

---

## Security and Authentication

- **File permissions:** `open_db()` calls `os.chmod(db_path, 0o600)` after
  creation. The parent directory `~/.cache/mlody/` is created with mode `0o700`
  (following `ensure_cache_root()` precedent in `mlody/resolver/cache.py`).
- **Parameterized queries:** All SQL uses `?` placeholders; no string
  interpolation into SQL (NFR-SEC-002).
- **No authentication:** Single-user local tool; no auth layer required.
- **No network egress:** DB is a local file; no data leaves the machine.

---

## Implementation Plan

### Phase 1 — `mlody/db/` package skeleton

1. Create `mlody/db/__init__.py` (empty).
2. Create `mlody/db/local_diff.py` with `get_repo_root()` and
   `compute_local_diff_sha()`.
3. Create `mlody/db/local_diff_test.py` with unit tests (see Testing Strategy).
4. Run `bazel run :gazelle` to generate `mlody/db/BUILD.bazel`.
5. Run `bazel test //mlody/db:local_diff_test` — all tests must pass.

### Phase 2 — `evaluations.py` and schema

6. Add `uuid-utils` to `pyproject.toml`.
7. Run `o-repin` to update lock files; verify no dependency conflict.
8. Run `bazel run :gazelle` again to pick up the new `@pip//uuid_utils` dep.
9. Create `mlody/db/evaluations.py` with `SCHEMA_DDL`, `open_db()`, and
   `write_evaluation()`.
10. Create `mlody/db/evaluations_test.py` with unit and integration tests (see
    Testing Strategy).
11. Run `bazel test //mlody/db:evaluations_test` — all tests must pass.

### Phase 3 — Call site wiring

12. Identify the evaluation entry point (the site that currently calls
    `write_metadata()` or creates an evaluation record).
13. Add a call to `compute_local_diff_sha()` and `write_evaluation()` at that
    site.
14. Run the full mlody test suite: `bazel test //mlody/...`.

### Phase 4 — Lint pass

15. `bazel build --config=lint //mlody/db/...` — zero errors.

### Dependency order

```
Phase 1 (local_diff)
  -> Phase 2 (evaluations + uuid-utils dep)
       -> Phase 3 (call site wiring)
            -> Phase 4 (lint)
```

### Estimated complexity

| Phase                       | Scope                            | Effort  |
| --------------------------- | -------------------------------- | ------- |
| Phase 1 — local_diff        | ~60 lines Python + tests         | Small   |
| Phase 2 — evaluations + dep | ~80 lines Python + tests + repin | Small   |
| Phase 3 — call site wiring  | ~10 lines at call site           | Trivial |
| Phase 4 — lint pass         | Zero new code                    | Trivial |

### BUILD.bazel changes required

All managed by `bazel run :gazelle`. The implementing agent must run Gazelle
after:

- Creating the `mlody/db/` package (Phase 1).
- Adding `uuid-utils` to `pyproject.toml` and repinning (Phase 2 step 8).

No manual edits to `BUILD.bazel` files.

---

## Testing Strategy

### `mlody/db/local_diff_test.py`

All tests use `pyfakefs` (`fs` fixture) to avoid touching the real filesystem.
Subprocess calls to `git` are patched with `unittest.mock.patch`.

| Test                                       | Scenario                                                 | Expected                                                     |
| ------------------------------------------ | -------------------------------------------------------- | ------------------------------------------------------------ |
| `test_get_repo_root_success`               | `git rev-parse` returns a path                           | Returns `Path` of that path                                  |
| `test_get_repo_root_git_failure`           | `git rev-parse` exits non-zero                           | Returns `None`; warning logged                               |
| `test_get_repo_root_git_not_found`         | `git` not on PATH                                        | Returns `None`; warning logged                               |
| `test_compute_none_repo_root`              | `repo_root=None`                                         | Returns `None`; warning logged                               |
| `test_compute_both_subtrees_absent`        | Neither `mlody/` nor `common/python/starlarkish/` exists | Returns SHA-256 of a single newline byte (`\n`) (not `None`) |
| `test_compute_one_subtree_absent`          | Only `mlody/` exists with one file                       | Returns non-null digest; changes when file content changes   |
| `test_compute_deterministic`               | Same files, same content, called twice                   | Returns identical digest                                     |
| `test_compute_untracked_file_changes_hash` | Add a new file under `mlody/`                            | Digest differs from baseline                                 |
| `test_compute_modified_file_changes_hash`  | Modify an existing file under `mlody/`                   | Digest differs from baseline                                 |
| `test_compute_sort_order_independent`      | Two files; enumerate in different OS order               | Always returns same digest                                   |

### `mlody/db/evaluations_test.py`

Integration tests use a real `sqlite3` connection against an in-memory DB
(`sqlite3.connect(":memory:")`) or a `tmp_path` fixture file. No `pyfakefs`
needed for the DB tests.

| Test                                                   | Scenario                                                           | Expected                                                                      |
| ------------------------------------------------------ | ------------------------------------------------------------------ | ----------------------------------------------------------------------------- |
| `test_open_db_creates_table`                           | `open_db(tmp_path / "mlody.sqlite")`                               | `evaluations` table exists; `sqlite_master` confirms                          |
| `test_open_db_idempotent`                              | `open_db()` called twice on same path                              | No error; table unchanged                                                     |
| `test_open_db_wal_mode`                                | After `open_db()`                                                  | `PRAGMA journal_mode` returns `"wal"`                                         |
| `test_open_db_file_permissions`                        | After `open_db()` on a real path                                   | `stat.S_IMODE(os.stat(path).st_mode) == 0o600`                                |
| `test_write_evaluation_returns_uuid7`                  | Single insert                                                      | Returned string parses as UUID; version bits indicate v7                      |
| `test_write_evaluation_row_count`                      | Insert once                                                        | `SELECT COUNT(*) FROM evaluations` returns 1                                  |
| `test_write_evaluation_all_columns`                    | Insert with all fields                                             | Row values match provided inputs exactly                                      |
| `test_write_evaluation_twice_distinct_ids`             | Insert twice                                                       | Two rows; IDs differ; IDs are time-ordered (second > first lexicographically) |
| `test_write_evaluation_nullable_repo`                  | `repo=None`                                                        | Row inserted; `repo` column is NULL                                           |
| `test_write_evaluation_nullable_local_diff_sha`        | `local_diff_sha=None`                                              | Row inserted; `local_diff_sha` column is NULL                                 |
| `test_write_evaluation_created_at_default`             | `created_at` not provided                                          | `created_at` is a valid ISO 8601 UTC string                                   |
| `test_write_evaluation_empty_value_description_raises` | `value_description=""`                                             | Raises `ValueError` before insert                                             |
| `test_write_evaluation_bad_resolved_sha_raises`        | `resolved_sha="abc"` (not 40 chars)                                | Raises `ValueError` before insert                                             |
| `test_write_evaluation_json_files_untouched`           | JSON files exist in `~/.cache/mlody/evaluations/` before and after | Files are identical (FR-006 / KPI-003)                                        |

### Run commands

```sh
bazel test //mlody/db:local_diff_test      # unit tests for local_diff
bazel test //mlody/db:evaluations_test     # unit + integration tests for evaluations
bazel test //mlody/...                     # full mlody suite
bazel build --config=lint //mlody/db/...   # lint
```

---

## Deployment and Operations

- **Zero-config deployment:** `open_db()` creates the DB file and parent
  directories automatically on first call.
- **DB location:** `~/.cache/mlody/mlody.sqlite` (fixed path; not configurable
  at this stage).
- **No migration tooling** required for this change. Future schema additions use
  `ALTER TABLE ADD COLUMN`.
- **Backup:** User's responsibility (Time Machine, rsync). No automated backup.
- **Existing JSON files:** Left untouched. No migration. No deletion.

---

## Non-Functional Requirements

| NFR            | Requirement                                                     | How met                                                                                 |
| -------------- | --------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| NFR-PERF-001   | Insert < 100 ms including `local_diff_sha` for ≤ 50 MB of files | Method A hashes files in a tight loop; SQLite WAL insert is O(1). Benchmark in Phase 2. |
| NFR-SCALE-001  | Correct with ≤ 100,000 rows                                     | No full-table scans in the write path; UUID v7 PK is an ordered TEXT index.             |
| NFR-AVAIL-001  | DB write failures propagate; never silent crash                 | `write_evaluation()` does not catch exceptions.                                         |
| NFR-AVAIL-002  | WAL mode (Should Have)                                          | `open_db()` executes `PRAGMA journal_mode=WAL`.                                         |
| NFR-SEC-001    | DB file permissions `0600`                                      | `os.chmod(db_path, 0o600)` in `open_db()`.                                              |
| NFR-SEC-002    | Parameterized queries only                                      | All SQL uses `?` placeholders.                                                          |
| NFR-USE-001    | Zero manual setup                                               | `open_db()` creates directories and file automatically.                                 |
| NFR-MAINT-001  | Future changes via `ALTER TABLE ADD COLUMN`                     | Documented in `evaluations.py` header; enforced by code review.                         |
| NFR-COMPAT-001 | macOS 13+ and Linux                                             | stdlib only; no platform-specific code.                                                 |
| NFR-COMPAT-002 | stdlib `sqlite3` driver                                         | Used exclusively.                                                                       |

---

## Risks and Mitigation

| Risk                                           | Impact | Probability | Mitigation                                               |
| ---------------------------------------------- | ------ | ----------- | -------------------------------------------------------- |
| R-001: `uuid-utils` dep conflict               | Medium | Low         | Vet lock file at Phase 2 step 7; fall back to `uuid7`    |
| R-002: `local_diff_sha` slow on large subtrees | Medium | Low         | Benchmark Phase 1; cache on `resolved_sha` if needed     |
| R-003: File permissions not `0600`             | High   | Low         | Explicit `os.chmod`; test assertion on real path         |
| R-004: Future schema changes break rows        | High   | Medium      | `ALTER TABLE ADD COLUMN` only; code review gate          |
| R-005: `~/.cache/mlody/` absent                | Medium | Medium      | `Path.mkdir(parents=True, exist_ok=True)` in `open_db()` |
| R-006: `git remote get-url origin` fails       | Medium | Low         | `repo` is nullable; warn and continue                    |

---

## Future Considerations

- **CLI query commands:** A `mlody eval list` command reading from
  `mlody.sqlite`. Separate requirements document.
- **Result/score columns:** `completed_at`, pass/fail outcome, numeric metrics.
  Add via `ALTER TABLE ADD COLUMN` in a future change.
- **Status transitions:** `pending -> running -> complete`. Separate change.
- **Bazel-precise `local_diff_sha`:** Hardcoded subtrees replaced by
  Bazel-query-derived file lists. Separate change.
- **Configurable DB path:** `MLODY_DB_PATH` environment variable override.
- **Data retention policy:** Automatic archival or deletion after N rows/days.
