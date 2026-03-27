# Requirements Document: SQLite Evaluations Database

**Version:** 1.1 **Date:** 2026-03-27 **Prepared by:** Requirements Analyst AI
(@socrates) **Status:** Draft

---

## 1. Executive Summary

Mlody currently records evaluation results by writing one JSON file per
evaluation run into `~/.cache/mlody/evaluations/`. As the number of runs grows,
this flat-file approach becomes unwieldy: there is no atomic durability, no
indexed access, and no easy way to correlate runs across time. The immediate
pain point surfacing this change is the need to persist a `value_description`
field — a human-readable summary of the model configuration under test — which
is currently not stored anywhere after the run completes.

This change replaces the flat-file store with a single SQLite database file
located at `~/.cache/mlody/mlody.sqlite`. A single `evaluations` table will hold
all run records. The scope is intentionally narrow: write path only. Query CLI
commands, result/score fields, and status-transition tracking are explicitly
deferred to later changes.

The expected business value is a durable, queryable-in-the-future audit trail of
every evaluation run, identified by stable UUIDs, with enough metadata to
reconstruct what model configuration was being tested, on which machine, by
which user, and from which exact code state.

---

## 2. Project Scope

### 2.1 In Scope

- Define and create the `evaluations` table schema in SQLite.
- Persist every new evaluation run as a row in that table.
- Compute and store `local_diff_sha` at write time (see FR-005 for the exact
  algorithm).
- Store `value_description` (the new field motivating this change).
- Store provenance fields from `cache.go` / `-meta.json`: `username`,
  `hostname`, `requested_ref`, `resolved_sha`, `resolved_at`, `repo`,
  `local_only`.
- Use UUID v7 as the primary key for all new rows.
- DB file lives at `~/.cache/mlody/mlody.sqlite` (one file per user/host).
- Leave existing JSON files on disk untouched; ignore them going forward.

### 2.2 Out of Scope

- CLI query commands against the evaluations table.
- Result fields (score, numeric metrics, pass/fail outcomes).
- Status-transition tracking (e.g. `pending → running → complete`).
- Bazel-precise subtree selection for `local_diff_sha` (hardcoded paths used for
  now; Bazel-precise selection is deferred).
- Migration or seeding from existing JSON evaluation files.
- Multi-host synchronization or replication.
- Any read path beyond basic row insertion verification.

### 2.3 Assumptions

- The `~/.cache/mlody/` directory is created by the existing Mlody runtime
  before the DB is first accessed. [Assumption - Requires Validation]
- SQLite's default WAL or journal mode is acceptable for single-user local use;
  no concurrent multi-process writes are expected.
- UUID v7 generation is handled by a Python library (e.g. `uuid-utils` or
  `uuid7`); the exact library is an implementation detail left to the
  implementing agent.
- `local_diff_sha` is computed by hashing the recursive contents of `mlody/` and
  `common/python/starlarkish/` relative to the repo root (see FR-005). A `NULL`
  value means the repo root could not be determined.
- `resolved_sha` (the 40-character git SHA) serves as the committoid component
  of the composite workspace key `(resolved_sha, local_diff_sha)`. Whether a
  separate `committoid_sha` alias column is needed is an architectural decision
  deferred to @vitruvious.
- `created_at` and `resolved_at` may overlap in practice (both recorded near run
  start); the architect decides whether to unify or keep them as distinct
  columns.

### 2.4 Constraints

- Must work on macOS and Linux (the two platforms Mlody developers use).
- No additional infrastructure: SQLite only, no server, no network dependency.
- Python 3.13.2 hermetic runtime via `rules_python`; any new dependency must go
  through the `uv pip compile` / `o-repin` workflow and be declared in
  `pyproject.toml` without version pinning.

---

## 3. Stakeholders

| Role               | Name/Group          | Responsibilities                                         | Contact                     |
| ------------------ | ------------------- | -------------------------------------------------------- | --------------------------- |
| Product Owner      | Mlody team          | Define scope, accept/reject requirements                 | [Pending Stakeholder Input] |
| Implementing Agent | @vulcan-python      | Write the migration, schema, and write-path Python code  | N/A                         |
| Architect          | @vitruvious         | Produce SPEC.md and design decisions                     | N/A                         |
| End Users          | ML pipeline authors | Run evaluations locally; benefit from durable audit logs | Internal                    |

---

## 4. Business Requirements

### 4.1 Business Objectives

- **BR-001:** Provide a durable, append-only record of every evaluation run so
  that results can be audited and correlated over time.
- **BR-002:** Capture `value_description` at run time so that the human-readable
  model configuration summary is never lost after a run completes.
- **BR-003:** Establish a stable primary-key scheme (UUID v7) that supports
  future distributed or time-ordered querying without key collisions.
- **BR-004:** Record sufficient provenance metadata (user, host, ref, SHA, repo)
  to reconstruct the exact environment that produced any given evaluation row.

### 4.2 Success Metrics

- **KPI-001:** Every evaluation run produces exactly one new row in
  `evaluations`. Target: 100% write success rate under normal conditions.
  Measurement: Integration test asserting row count increments by 1 per run.
- **KPI-002:** `value_description` is non-null for all runs where the field is
  provided by the caller. Measurement: DB constraint + test assertion.
- **KPI-003:** Existing JSON files remain unmodified on disk after the change is
  deployed. Measurement: File-system snapshot comparison in integration test.
- **KPI-004:** All provenance columns (`username`, `hostname`, `requested_ref`,
  `resolved_sha`, `resolved_at`, `repo`, `local_only`) are non-null for every
  inserted row. Measurement: NOT NULL constraints + test assertion.

---

## 5. User Requirements

### 5.1 User Personas

**Persona 1: ML Pipeline Author**

- Role: Writes and runs Mlody evaluation pipelines locally.
- Goals: Run evaluations and trust that results are persisted durably without
  manual intervention.
- Pain Points: JSON files scattered in `~/.cache/mlody/evaluations/` are hard to
  query and can be silently lost.
- Needs: Transparent, zero-config persistence that works the same on every
  machine without setup.

### 5.2 User Stories

**Epic 1: Durable Evaluation Persistence**

- **US-001:** As an ML pipeline author, I want each evaluation run to be
  automatically recorded in a local database so that I have a durable audit
  trail without managing files manually.
  - Acceptance Criteria:
    - Given: An evaluation run completes successfully.
    - When: Mlody writes the run record.
    - Then: A new row exists in `mlody.sqlite/evaluations` with all required
      fields populated and a unique UUID v7 primary key.
  - Priority: Must Have

- **US-002:** As an ML pipeline author, I want the `value_description` field
  stored with each run so that I can later identify which model configuration
  produced which results.
  - Acceptance Criteria:
    - Given: The evaluation run carries a `value_description` string.
    - When: The row is inserted.
    - Then: `value_description` in the DB matches the value provided at run time
      exactly.
  - Priority: Must Have

- **US-003:** As an ML pipeline author, I want my existing JSON evaluation files
  to remain untouched after upgrading Mlody so that I do not lose historical
  data.
  - Acceptance Criteria:
    - Given: JSON files exist in `~/.cache/mlody/evaluations/` before the
      upgrade.
    - When: Mlody is updated and new runs are executed.
    - Then: The JSON files are still present on disk with their original
      contents; no migration script has modified or deleted them.
  - Priority: Must Have

---

## 6. Functional Requirements

### 6.1 Database Initialization

**FR-001: DB File Location**

- Description: Mlody must locate or create the SQLite database at
  `~/.cache/mlody/mlody.sqlite` (expanding `~` to the current user's home
  directory).
- Inputs: Runtime environment (current user home directory).
- Processing: Resolve the path; create the file if it does not exist.
- Outputs: Open SQLite connection to the file.
- Business Rules: Must not create the DB in the current working directory or a
  temp path.
- Priority: Must Have
- Dependencies: None.

**FR-002: Schema Bootstrap**

- Description: On first connection (or any connection where the table is
  absent), Mlody must create the `evaluations` table using
  `CREATE TABLE IF NOT EXISTS`.
- Inputs: Open DB connection.
- Processing: Execute DDL (see Section 8.1).
- Outputs: Table exists and is ready for inserts.
- Business Rules: Must be idempotent; must not alter or drop existing rows.
- Priority: Must Have
- Dependencies: FR-001.

### 6.2 Evaluation Write Path

**FR-003: Insert Evaluation Row**

- Description: When an evaluation run is initiated, Mlody must insert one row
  into the `evaluations` table.
- Inputs: All column values described in Section 8.1.
- Processing: Construct and execute a parameterized `INSERT` statement.
- Outputs: Row committed to the DB; the UUID v7 primary key is returned to the
  caller.
- Business Rules:
  - All `NOT NULL` columns must be provided; the insert must fail loudly (raise
    an exception) if they are absent.
  - `created_at` must be set to the current UTC timestamp at insert time if not
    explicitly provided.
  - `completed_at` is nullable and may be omitted on insert (populated later
    when the run finishes, if applicable — exact update logic deferred to a
    future change).
- Priority: Must Have
- Dependencies: FR-002.

**FR-004: UUID v7 Primary Key Generation**

- Description: Each new evaluation row must be assigned a UUID v7 as its `id`
  primary key.
- Inputs: Current timestamp (used by UUID v7 algorithm).
- Processing: Generate UUID v7 via a Python library.
- Outputs: UUID string stored in the `id` column.
- Business Rules: IDs must be globally unique and time-ordered; UUID v4 is not
  acceptable.
- Priority: Must Have
- Dependencies: FR-003.

**FR-005: local_diff_sha Computation**

- Description: At insert time, Mlody must compute a SHA representing the state
  of local file content under the relevant source subtrees and store it in
  `local_diff_sha`.
- Inputs: Repo root directory (determined at runtime, e.g. via
  `git rev-parse --show-toplevel`).
- Processing:
  1. Determine the repo root. If it cannot be determined, store `NULL` and log a
     warning; do not fail the insert.
  2. Recursively enumerate all files under `mlody/` and
     `common/python/starlarkish/` relative to the repo root.
  3. Compute the SHA-256 of the combined file contents using one of the
     following equivalent methods (implementing agent chooses whichever is
     faster):
     - **Method A (per-file hash):** For each file, sorted by repo-relative
       path, compute `sha256(file_contents)`. Concatenate all hex digests (with
       paths) in sorted order and compute a final `sha256` of the concatenated
       string.
     - **Method B (ephemeral tar):** Create an ephemeral, deterministic tar
       archive of the two subtrees (sorted, no timestamps) piped directly to
       `sha256sum`; use the resulting digest.
  4. Store the resulting 64-character hex digest in `local_diff_sha`.
- Outputs: `local_diff_sha` column value (`NULL` or 64-character hex string).
- Business Rules:
  - Both uncommitted edits and untracked files are captured automatically
    because the hash covers file content on disk, not git state.
  - Scope is hardcoded to `mlody/` and `common/python/starlarkish/`. Bazel-
    precise subtree selection is explicitly deferred (see Section 2.2).
  - `NULL` is valid only when the repo root cannot be determined; it does NOT
    indicate a clean working tree.
  - If the subtree directories do not exist, treat them as empty (hash over zero
    files) rather than storing `NULL`.
- Priority: Must Have
- Dependencies: FR-003.

### 6.3 Coexistence with JSON Files

**FR-006: JSON Files Left Intact**

- Description: The existing JSON write path must remain operational or be
  removed, but under no circumstances may Mlody delete, overwrite, or migrate
  existing JSON files in `~/.cache/mlody/evaluations/`.
- Inputs: N/A.
- Processing: No action on existing JSON files.
- Outputs: JSON files unchanged.
- Business Rules: No migration script; no seeding of DB from JSON history.
- Priority: Must Have
- Dependencies: None.

---

## 7. Non-Functional Requirements

### 7.1 Performance Requirements

- **NFR-PERF-001:** A single evaluation row insert must complete in under 100 ms
  on a laptop-class machine (including `local_diff_sha` computation for the
  scoped subtrees up to 50 MB of total file content).

### 7.2 Scalability Requirements

- **NFR-SCALE-001:** The schema and write path must remain correct with up to
  100,000 rows in the `evaluations` table without schema changes.

### 7.3 Availability & Reliability

- **NFR-AVAIL-001:** DB write failures must never crash the evaluation run
  silently. Any exception must propagate to the caller or be logged at ERROR
  level with a clear message.
- **NFR-AVAIL-002:** SQLite WAL mode is recommended to reduce lock contention if
  a future read path is added; this is a Should Have for this change. [Pending
  Stakeholder Input]

### 7.4 Security Requirements

- **NFR-SEC-001:** The DB file must be created with permissions `0600`
  (owner-read/write only) to prevent other local users from reading evaluation
  metadata.
- **NFR-SEC-002:** All SQL statements must use parameterized queries; no string
  interpolation into SQL.

### 7.5 Usability Requirements

- **NFR-USE-001:** DB initialization must be fully automatic and transparent to
  the user; zero manual setup steps.

### 7.6 Maintainability Requirements

- **NFR-MAINT-001:** Schema changes in future iterations must be handled via
  explicit migration statements, not by dropping and recreating the table.
  (Mechanism TBD — deferred to future change.)

### 7.7 Compatibility Requirements

- **NFR-COMPAT-001:** Must work on macOS 13+ and Linux (Debian/Ubuntu).
- **NFR-COMPAT-002:** Must use the Python standard-library `sqlite3` module as
  the DB driver; no ORM dependency is required at this stage.

---

## 8. Data Requirements

### 8.1 Data Entities

**Table: `evaluations`**

| Column              | Type      | Constraints            | Description                                                                                                                                                         |
| ------------------- | --------- | ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`                | `TEXT`    | `PRIMARY KEY NOT NULL` | UUID v7 string (e.g. `019528ab-...`). Time-ordered, globally unique.                                                                                                |
| `created_at`        | `TEXT`    | `NOT NULL`             | ISO 8601 UTC timestamp of row insertion (e.g. `2026-03-27T14:00:00Z`).                                                                                              |
| `completed_at`      | `TEXT`    | `NULLABLE`             | ISO 8601 UTC timestamp when the run finished. NULL until completion recorded.                                                                                       |
| `username`          | `TEXT`    | `NOT NULL`             | OS username of the user who started the evaluation (from `os.getlogin()` or equivalent).                                                                            |
| `hostname`          | `TEXT`    | `NOT NULL`             | Hostname of the machine where the evaluation is running (from `socket.gethostname()` or equivalent).                                                                |
| `requested_ref`     | `TEXT`    | `NOT NULL`             | The original committoid string as typed by the user (e.g. `main`, `abc1234`, `v1.2.0`). Sourced from `cache.go` / `-meta.json`.                                     |
| `resolved_sha`      | `TEXT`    | `NOT NULL`             | Full 40-character resolved git SHA for `requested_ref`. Also serves as the committoid component of the composite workspace key `(resolved_sha, local_diff_sha)`.    |
| `resolved_at`       | `TEXT`    | `NOT NULL`             | ISO 8601 UTC timestamp of when the ref was resolved. Sourced from `resolved_at` in the existing `-meta.json`. Architect to decide relationship to `created_at`.     |
| `repo`              | `TEXT`    | `NOT NULL`             | Remote URL of the `origin` remote (from `git remote get-url origin` or equivalent).                                                                                 |
| `local_only`        | `INTEGER` | `NOT NULL`             | Boolean flag (0/1): 1 if the ref was found only locally and has not been pushed to the remote; 0 otherwise.                                                         |
| `value_description` | `TEXT`    | `NOT NULL`             | Human-readable description of the model configuration / value being tested.                                                                                         |
| `local_diff_sha`    | `TEXT`    | `NULLABLE`             | SHA-256 hex digest of recursive file content under `mlody/` and `common/python/starlarkish/` at run time; NULL only if repo root cannot be determined (see FR-005). |

Notes:

- SQLite stores all values as TEXT, INTEGER, REAL, BLOB, or NULL. `TEXT` is used
  for all string and timestamp fields; `INTEGER` is used for the boolean
  `local_only` field. No separate `DATETIME` type is needed.
- `resolved_sha` doubles as the committoid component of the composite workspace
  key `(resolved_sha, local_diff_sha)`. Whether a separate alias column
  `committoid_sha` is needed is an architectural decision for @vitruvious.
- The columns `pipeline`, `workspace_label`, and `model_id` that appeared in
  earlier drafts have been removed: `pipeline` and `workspace_label` have no
  confirmed meaning in the Mlody domain, and `model_id` was not present in
  `cache.go` or `-meta.json`.
- Additional columns for results, scores, and status are explicitly deferred
  (see Section 2.2).

### 8.2 Data Quality Requirements

- `id` must be a valid UUID v7; the application must reject or log a warning for
  any row without a conforming ID.
- `created_at`, `completed_at` (when set), `resolved_at` must be valid ISO 8601
  UTC strings.
- `value_description` must be a non-empty string; empty string is not equivalent
  to NULL.
- `resolved_sha` must be exactly 40 hexadecimal characters.
- `local_only` must be 0 or 1; no other integer values are valid.

### 8.3 Data Retention & Archival

- No automatic deletion or archival policy is defined for this change. Rows
  accumulate indefinitely. [TBD — future change]

### 8.4 Data Privacy & Compliance

- The `value_description`, `repo`, and `username` fields may contain information
  considered internal. The DB file must be readable only by the owning user
  (NFR-SEC-001).
- No PII is expected in any column at this time. [Assumption - Requires
  Validation]

---

## 9. Integration Requirements

### 9.1 External Systems

| System          | Purpose                                                                    | Direction  | Format | Auth | Error Handling                              |
| --------------- | -------------------------------------------------------------------------- | ---------- | ------ | ---- | ------------------------------------------- |
| Local git       | Determine repo root, resolve ref, compute `local_diff_sha`, get `repo` URL | Read       | stdout | None | Log warning + store NULL / skip on failure  |
| Local SQLite DB | Persist evaluation rows                                                    | Read/Write | SQL    | None | Propagate exception to caller               |
| Mlody runtime   | Provide field values at run creation                                       | Internal   | Python | N/A  | Caller responsible for providing valid data |
| OS environment  | Provide `username` and `hostname`                                          | Read       | stdlib | None | Log warning + fail loudly if unavailable    |

### 9.2 API Requirements

- No external HTTP APIs involved in this change.
- Internal Python API: a module/function `write_evaluation(...)` (exact
  signature is an implementation detail for the architect/implementing agent)
  accepting the fields defined in Section 8.1 and returning the `id` of the
  newly created row.

---

## 10. User Interface Requirements

Not applicable. This change is purely a backend/storage change with no UI
surface.

---

## 11. Reporting & Analytics Requirements

Not in scope for this change. Future CLI query commands will be defined in a
separate requirements document.

---

## 12. Security & Compliance Requirements

### 12.1 Authentication & Authorization

- Single-user local tool; no authentication layer required.
- DB file permissions: `0600` (owner read/write only).

### 12.2 Data Security

- Parameterized queries only (see NFR-SEC-002).
- DB file stored under `~/.cache/`; no network egress of DB contents.

### 12.3 Compliance

- No regulatory compliance requirements (HIPAA, GDPR, PCI-DSS) are in scope.
  [Assumption - Requires Validation]

---

## 13. Infrastructure & Deployment Requirements

### 13.1 Hosting & Environment

- Fully local: developer laptop (macOS or Linux).
- No server, container, or cloud resource required.

### 13.2 Deployment

- DB file is created automatically on first run.
- New Python dependency (UUID v7 library) must be added to `pyproject.toml` and
  re-pinned via `o-repin`.
- Bazel BUILD files must be updated via `bazel run :gazelle` after dependency
  changes.

### 13.3 Disaster Recovery

- SQLite file is a single file on local disk. Recovery is the user's
  responsibility (e.g. Time Machine, rsync). No automated backup required.

---

## 14. Testing & Quality Assurance Requirements

### 14.1 Testing Scope

- Unit tests for `local_diff_sha` computation:
  - Repo root cannot be determined → `NULL` + warning.
  - Subtree directories absent → hash over zero files (not `NULL`).
  - Untracked files present in subtrees → hash changes relative to a baseline.
  - Modified (uncommitted) files present → hash changes relative to a baseline.
- Unit tests for UUID v7 generation (format validation, monotonic ordering for
  two consecutive calls).
- Integration test: end-to-end evaluation run → assert row exists in SQLite with
  all NOT NULL columns populated, including `username`, `hostname`,
  `requested_ref`, `resolved_sha`, `resolved_at`, `repo`, `local_only`.
- Integration test: run twice → assert row count is 2, IDs are distinct and
  time-ordered.
- Integration test: JSON files in `~/.cache/mlody/evaluations/` are not modified
  by new runs.

### 14.2 Acceptance Criteria

Each functional requirement (FR-001 through FR-006) must have at least one
passing automated test before the change is considered complete.

---

## 15. Training & Documentation Requirements

### 15.1 User Documentation

- No user-facing documentation changes required for this change (transparent to
  users).

### 15.2 Technical Documentation

- Inline docstrings on the new DB module.
- Schema DDL must be expressed as a named constant in the source so it is
  readable without running the program.

### 15.3 Training

- Not applicable.

---

## 16. Risks & Mitigation Strategies

| Risk ID | Description                                                                     | Impact | Probability | Mitigation                                                                                       | Owner          |
| ------- | ------------------------------------------------------------------------------- | ------ | ----------- | ------------------------------------------------------------------------------------------------ | -------------- |
| R-001   | UUID v7 library adds a transitive dependency conflict with existing deps        | Medium | Low         | Vet the library against the existing lock file before merging; prefer `uuid-utils` (Rust-backed) | @vulcan-python |
| R-002   | Recursive file hashing is slow on large subtrees, blocking evaluation start     | Medium | Low         | Benchmark Method A vs Method B; if either exceeds 100 ms, consider caching the hash              | @vulcan-python |
| R-003   | SQLite file permissions are not set to `0600` on all platforms                  | High   | Low         | Explicitly call `os.chmod` after file creation; add a test assertion                             | @vulcan-python |
| R-004   | Future schema changes break existing rows if not handled by migrations          | High   | Medium      | Document that `ALTER TABLE ADD COLUMN` is the only safe change; enforce via code review          | Mlody team     |
| R-005   | `~/.cache/mlody/` does not exist on a fresh install and DB creation fails       | Medium | Medium      | Call `pathlib.Path.mkdir(parents=True, exist_ok=True)` before opening DB                         | @vulcan-python |
| R-006   | `git remote get-url origin` fails (no remote configured) causing insert failure | Medium | Low         | Catch the error, store `NULL` for `repo`, log a warning; do not fail the insert                  | @vulcan-python |

---

## 17. Dependencies

| Dependency             | Type     | Status  | Impact if Delayed                         | Owner          |
| ---------------------- | -------- | ------- | ----------------------------------------- | -------------- |
| UUID v7 Python library | External | Pending | Cannot generate time-ordered primary keys | @vulcan-python |
| `o-repin` lock update  | Internal | Pending | Bazel build breaks if lock is out of sync | @vulcan-python |
| Gazelle BUILD update   | Internal | Pending | Bazel cannot find new module              | @vulcan-python |

---

## 18. Open Questions & Action Items

| ID   | Question/Action                                                                                                                                   | Owner          | Target Date | Status |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------------- | -------------- | ----------- | ------ |
| OQ-1 | Should `completed_at` be updated in this change or deferred? Currently assumed deferred.                                                          | Mlody team     | TBD         | Open   |
| OQ-2 | Confirm WAL mode should be enabled by default (NFR-AVAIL-002).                                                                                    | Mlody team     | TBD         | Open   |
| OQ-3 | Which UUID v7 library to use: `uuid-utils`, `uuid7`, or another? Confirm no license conflicts.                                                    | @vulcan-python | TBD         | Open   |
| OQ-4 | Should the DB write be synchronous (blocking the run start) or fire-and-forget async? Assumed synchronous.                                        | Mlody team     | TBD         | Open   |
| OQ-5 | Is `~/.cache/mlody/` guaranteed to exist before the DB is first opened, or must this code create it?                                              | @vulcan-python | TBD         | Open   |
| OQ-6 | Should `resolved_sha` be reused directly as the committoid component of the workspace key, or should a separate `committoid_sha` column be added? | @vitruvious    | TBD         | Open   |
| OQ-7 | Should `created_at` subsume `resolved_at`, or are they meaningfully distinct timestamps that must both be stored?                                 | @vitruvious    | TBD         | Open   |
| OQ-8 | If `git remote get-url origin` fails (no remote), should `repo` be nullable or should the insert be rejected?                                     | Mlody team     | TBD         | Open   |
| OQ-9 | For `local_diff_sha` Method A vs B: is there a preference, or should the implementing agent benchmark and choose?                                 | Mlody team     | TBD         | Open   |

---

## 19. Revision History

| Version | Date       | Author                  | Changes                                                                                                                                       |
| ------- | ---------- | ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| 1.0     | 2026-03-27 | Requirements Analyst AI | Initial draft                                                                                                                                 |
| 1.1     | 2026-03-27 | Requirements Analyst AI | Add provenance columns from cache.go; remove pipeline/workspace_label/model_id; fix FR-005 hashing algorithm; clarify composite workspace key |

---

## Appendices

### Appendix A: Glossary

| Term                | Definition                                                                                                                                                                  |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `local_diff_sha`    | SHA-256 hex digest of the recursive file content under `mlody/` and `common/python/starlarkish/` at evaluation run time. NULL only when the repo root cannot be determined. |
| `requested_ref`     | The committoid string as typed by the user (branch name, short SHA, tag, etc.) before resolution to a full SHA.                                                             |
| `resolved_sha`      | The full 40-character git SHA that `requested_ref` resolved to. Also acts as the committoid component of the composite workspace key.                                       |
| `local_only`        | Boolean (0/1): indicates the resolved ref exists locally but has not been pushed to the `origin` remote.                                                                    |
| UUID v7             | A time-ordered UUID variant (RFC 9562) where the first 48 bits encode a Unix millisecond timestamp, enabling chronological sorting.                                         |
| `value_description` | A human-readable string summarizing the model configuration (e.g. HuggingFace model ID + adapter settings) under evaluation.                                                |
| WAL mode            | SQLite Write-Ahead Logging mode; improves concurrency between readers and a single writer.                                                                                  |

### Appendix B: References

- RFC 9562 — Universally Unique IDentifiers (UUIDs), Section 5.7 (UUID
  Version 7)
- SQLite documentation: <https://www.sqlite.org/docs.html>
- Mlody codebase: `mlody/` directory of the Omega monorepo
- Existing cache metadata: `cache.go` and `-meta.json` files in the Mlody
  codebase (authoritative source for `requested_ref`, `resolved_sha`,
  `resolved_at`, `repo`, `local_only`)

---

**End of Requirements Document**
