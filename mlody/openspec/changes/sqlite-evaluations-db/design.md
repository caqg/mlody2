# Design: SQLite Evaluations Database

**Version:** 1.0 **Date:** 2026-03-27 **Architect:** @vitruvius **Status:**
Draft **Requirements:**
`mlody/openspec/changes/sqlite-evaluations-db/REQUIREMENTS.md`

---

## Problem Statement

Mlody's flat-file evaluation store (`~/.cache/mlody/evaluations/*.json`) has
three concrete deficiencies:

1. **No atomic durability.** A partial write leaves an unreadable file with no
   recovery path.
2. **No indexed access.** Correlating runs requires globbing and parsing every
   file.
3. **Missing `value_description`.** The field is computed at run time but never
   persisted, so the human-readable model configuration summary is lost the
   moment the process exits.

This design replaces the flat-file store with a single SQLite file at
`~/.cache/mlody/mlody.sqlite`. Scope is intentionally narrow: write path only.
Query commands, result/score fields, and status-transition tracking are all
deferred.

---

## Design Decisions

### D-1: New `mlody/db/` package — no changes to `mlody/resolver/`

The DB layer lives in a new `mlody/db/` package. It is a pure library with no
import of `mlody/resolver/`. Call sites in `resolver.py` (or wherever
evaluations are initiated) will import from `mlody.db.evaluations` and call
`write_evaluation()`. This keeps the DB layer independently testable and avoids
circular imports.

### D-2: Python stdlib `sqlite3` — no ORM

NFR-COMPAT-002 explicitly requires stdlib `sqlite3`. No SQLAlchemy, no Peewee.
The schema is small (one table, twelve columns) and the access pattern is
append-only inserts, so an ORM would add complexity with no benefit.

### D-3: `uuid-utils` for UUID v7

`uuid-utils` is a Rust-backed drop-in that extends Python's `uuid.UUID` with
`uuid7()`. It is the most mature UUID v7 library in the Python ecosystem as of
2026, carries an MIT licence, and has no transitive dependencies. The
alternative (`uuid7`) is pure Python and slower but acceptable as a fallback if
a dependency conflict is discovered. The implementing agent resolves OQ-3 at
install time by running `o-repin` and checking the lock for conflicts.

### D-4: `resolved_sha` is the committoid component — no separate alias column

OQ-6 is resolved here. `resolved_sha` already carries the full 40-character git
SHA, which is the canonical identifier for the committed state of the repo. A
separate `committoid_sha` column would be a direct duplicate. Consumers that
need to form the composite workspace key `(resolved_sha, local_diff_sha)` use
`resolved_sha` directly. No alias column is added.

### D-5: `created_at` and `resolved_at` are distinct columns

OQ-7 is resolved here. `resolved_at` is the wall-clock UTC timestamp at
evaluation completion — i.e. when the resolved value is returned to the user.
`created_at` is the timestamp at which the evaluation row is inserted into the
database. Both columns are retained because they represent different semantic
events.

Currently, `resolved_at` is set just before `ws.load()` is called inside
`resolve_workspace()` (in `mlody/resolver/resolver.py`), which means it is set
slightly before the result is actually printed. This is acceptable for now
because execution between that point and result output is minimal; the two
timestamps will be nearly identical. Moving `resolved_at` to after result
printing would require the caller (`show.py`) to propagate it back through
`resolve_workspace()`, a non-trivial refactor deferred to a future change. As
more pipeline stages are added (e.g. scoring, post-processing) the gap between
`resolved_at` and `created_at` will grow and the call site should be revisited.

### D-6: `repo` is nullable; `NULL` on failure — insert never rejected

OQ-8 is resolved here. `git remote get-url origin` can legitimately fail
(no-remote checkout, detached head, network timeout). Failing the entire insert
because of a missing remote URL would break the core guarantee (every run
produces a row). `repo` is therefore `NULLABLE` in the schema. A warning is
logged when `NULL` is stored. The existing `NOT NULL` list in the requirements
table (Section 8.1) marks `repo` as `NOT NULL`; this design overrides that with
`NULLABLE` for resilience. The `NOT NULL` constraint for `repo` is dropped.

### D-7: `local_diff_sha` uses Method A (per-file hash)

OQ-9 is resolved here. Method A (sort files by repo-relative path, hash each
file's contents, hash the concatenated `path:digest\n` lines) avoids the
subprocess dependency on `tar` and `sha256sum` and is easier to unit-test with
`pyfakefs`. Both methods produce a deterministic 64-character hex digest. Method
A is chosen.

### D-8: WAL mode enabled — NFR-AVAIL-002 resolved as "Should Have"

The DB connection factory enables WAL mode immediately after opening the
connection (`PRAGMA journal_mode=WAL`). This has no downside for single-writer
use (the requirements scenario) and prepares for a future read path without a
schema or file migration.

### D-9: DB file created with `0600` permissions via `os.chmod`

After `Path.mkdir(parents=True, exist_ok=True)` creates `~/.cache/mlody/`,
`open_db()` calls `os.chmod(db_path, 0o600)` after first creation. The
`~/.cache/mlody/` directory itself is created with mode `0o700` following the
precedent set by `ensure_cache_root()` in `mlody/resolver/cache.py`.

### D-10: `~/.cache/mlody/` is created by `open_db()` — no assumption on caller

Assumption A in the requirements ("the `~/.cache/mlody/` directory is created by
the existing Mlody runtime") is not guaranteed in all code paths. OQ-5 is
resolved by having `open_db()` call `Path.mkdir(parents=True, exist_ok=True)`
unconditionally before opening the DB file. This is idempotent and safe.

---

## Architecture Sketch

### Package layout

```
mlody/db/
  __init__.py          (empty)
  evaluations.py       (open_db, SCHEMA_DDL, write_evaluation)
  local_diff.py        (compute_local_diff_sha)
  evaluations_test.py  (unit + integration tests for evaluations.py)
  local_diff_test.py   (unit tests for compute_local_diff_sha)
  BUILD.bazel          (managed by gazelle)
```

No changes to any existing file outside `pyproject.toml` and lock files (and the
call site that invokes `write_evaluation()`).

### Call flow

```
Evaluation entry point
  -> compute_local_diff_sha(repo_root)   [mlody.db.local_diff]
       git rev-parse --show-toplevel -> repo_root (or None)
       if None: return None, log warning
       enumerate files under mlody/ and common/python/starlarkish/
       sort by repo-relative path
       sha256(path:digest\n ...) -> 64-char hex string
  -> open_db(db_path)                    [mlody.db.evaluations]
       Path.mkdir(parents=True, exist_ok=True)
       sqlite3.connect(db_path)
       os.chmod(db_path, 0o600)
       PRAGMA journal_mode=WAL
       CREATE TABLE IF NOT EXISTS evaluations (...)
       return connection
  -> write_evaluation(conn, ...)         [mlody.db.evaluations]
       uuid7() -> id
       datetime.now(UTC).isoformat() -> created_at
       INSERT INTO evaluations VALUES (?, ?, ...) [parameterized]
       conn.commit()
       return id
```

### Schema

```sql
CREATE TABLE IF NOT EXISTS evaluations (
    id               TEXT    PRIMARY KEY NOT NULL,
    created_at       TEXT    NOT NULL,
    completed_at     TEXT,
    username         TEXT    NOT NULL,
    hostname         TEXT    NOT NULL,
    requested_ref    TEXT    NOT NULL,
    resolved_sha     TEXT    NOT NULL,
    resolved_at      TEXT    NOT NULL,
    repo             TEXT,
    local_only       INTEGER NOT NULL,
    value_description TEXT   NOT NULL,
    local_diff_sha   TEXT
);
```

Note: `repo` is `NULLABLE` per D-6 above. `completed_at` and `local_diff_sha`
are nullable per the requirements. All other columns are `NOT NULL`.

---

## Constraints and Risks

| Risk                                                                      | Mitigation                                                                                                           |
| ------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| R-001: `uuid-utils` dependency conflict                                   | Run `o-repin` and inspect lock; fall back to `uuid7` (pure Python) if needed                                         |
| R-002: `local_diff_sha` computation too slow (>100 ms for large subtrees) | Method A hashes each file independently — can be benchmarked; if slow, add a fast-path cache keyed on `resolved_sha` |
| R-003: DB file permissions not `0600` on all platforms                    | Explicit `os.chmod` after creation; test asserts `stat.S_IMODE(os.stat(path).st_mode) == 0o600`                      |
| R-004: Future schema changes break existing rows                          | Only `ALTER TABLE ADD COLUMN` is safe; enforce via code review; document in `evaluations.py` header                  |
| R-005: `~/.cache/mlody/` absent on fresh install                          | Resolved by D-10: `open_db()` calls `Path.mkdir(parents=True, exist_ok=True)`                                        |
| R-006: `git remote get-url origin` fails                                  | Resolved by D-6: `repo` is nullable; failure is caught, warning logged, `NULL` stored                                |

---

## Open Questions

All open questions from the requirements document that were assigned to
@vitruvius are resolved:

- **OQ-6** (separate `committoid_sha` alias): resolved by D-4 — not needed.
- **OQ-7** (`created_at` vs `resolved_at`): resolved by D-5 — both retained as
  distinct columns.
- **OQ-8** (`repo` nullable on failure): resolved by D-6 — `repo` is nullable.

Remaining open questions assigned to the Mlody team or @vulcan-python:

- **OQ-1** (`completed_at` update logic): deferred; column is nullable and
  populated by a future change.
- **OQ-2** (WAL mode default): resolved as "yes" by D-8.
- **OQ-3** (UUID v7 library): resolved as `uuid-utils` by D-3; confirming no
  licence conflict is an implementation-time check.
- **OQ-4** (sync vs async DB write): resolved as synchronous per the assumption
  in the requirements.
- **OQ-5** (`~/.cache/mlody/` creation): resolved by D-10.
- **OQ-9** (Method A vs B for `local_diff_sha`): resolved as Method A by D-7.
