# DB Benchmark Notes

This project includes a small synthetic benchmark for comparing SQLite, DuckDB,
and optionally PostgreSQL for the meeting action-item workload.

## What It Tests

The benchmark focuses on operations used by this PoC:

| Metric | Meaning |
|---|---|
| Schema init | Create tables and indexes |
| Single upsert | One action-item upsert per transaction |
| Batch upsert | Many action-item upserts in one transaction |
| Larger batch upsert | Configurable larger write test |
| PK lookup | Repeated primary-key reads |
| Agg query | Dashboard-style group by assignee/confidence |
| JOIN query | Action item plus evidence utterance lookup |
| Full scan | Sort all action items by confidence |

## Quick Command

Use this for a fast local comparison:

```powershell
.\.venv\Scripts\python.exe experiments\db_benchmark.py --skip-pg --rows 50 --big-rows 200
```

Use this for a larger SQLite-only smoke test:

```powershell
.\.venv\Scripts\python.exe experiments\db_benchmark.py --skip-duckdb --skip-pg --rows 500 --big-rows 10000
```

PostgreSQL is optional and requires a running server:

```powershell
.\.venv\Scripts\python.exe experiments\db_benchmark.py --pg-dsn postgresql://user:pass@localhost:5432/mobidays_bench
```

## Current Local Result

On the local Windows environment, with:

```text
--rows 50 --big-rows 200 --pg-dsn postgresql://postgres:postgres@localhost:5432/mobidays_bench
```

SQLite was fastest for every measured operation in this local PoC workload:

| Metric | SQLite | DuckDB | PostgreSQL |
|---|---:|---:|---:|
| Schema init | 11.9 ms | 41.7 ms | 152.9 ms |
| Single upsert x50 | 8.4 ms | 450.4 ms | 30.1 ms |
| Batch upsert x50 | 1.4 ms | 388.8 ms | 14.6 ms |
| Batch upsert x200 | 6.1 ms | 1124.5 ms | 53.7 ms |
| PK lookup x200 | 3.3 ms | 108.9 ms | 36.5 ms |
| Agg query x20 | 3.9 ms | 27.7 ms | 6.3 ms |
| JOIN query x20 | 4.0 ms | 32.3 ms | 30.3 ms |
| Full scan x20 | 28.7 ms | 38.5 ms | 68.6 ms |

## Interpretation

SQLite is the best fit for the submitted MVP because the workload is local,
small-to-medium, and operational:

- idempotent inserts and upserts
- status updates from the dashboard
- primary-key and meeting-scoped lookups
- simple aggregations for operator views

DuckDB remains useful for offline analytics or large columnar scans, but it is
not the best default for this app's row-oriented update path. PostgreSQL was
slower than SQLite in this single-user local benchmark, but it remains the
natural upgrade path if multiple users, server deployment, concurrent writes, or
centralized access control become requirements.

## Current App DB Split

The current dashboard can also run in a two-DB mode:

- PostgreSQL for operational action-item data.
- A separate local vector DB file for similarity search.

This keeps OLTP-style status updates and evidence joins separate from vector
retrieval. The vector DB path is controlled by `VECTOR_DB_PATH`.
