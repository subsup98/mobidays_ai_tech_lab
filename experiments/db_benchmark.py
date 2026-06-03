"""
DB Benchmark: SQLite vs DuckDB vs PostgreSQL

Tests run on each backend:
  1. schema_init      - Create all tables + indexes
  2. single_upsert    - 500 rows, one upsert per transaction (worst-case OLTP)
  3. batch_upsert     - 500 rows in a single transaction
  4. batch_upsert_10k - 10,000 rows in a single transaction
  5. pk_lookup        - 200x SELECT by primary key
  6. agg_query        - 20x GROUP BY assignee + AVG(confidence) + COUNT
  7. join_query       - 20x action_items JOIN action_item_sources JOIN utterances
  8. full_scan        - 20x SELECT * ORDER BY final_confidence (no index hit)

PostgreSQL is optional: skipped gracefully if no server is reachable.

Usage:
    python experiments/db_benchmark.py
    python experiments/db_benchmark.py --pg-dsn postgresql://user:pass@host/db
    python experiments/db_benchmark.py --rows 2000
    python experiments/db_benchmark.py --rows 100 --big-rows 1000 --skip-pg
"""
from __future__ import annotations

import argparse
import hashlib
import os
import random
import string
import sys
import time
import uuid
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import sqlite3
from db.duckdb_client import DuckDBClient
from db.pg_client import PostgreSQLClient

# ---------------------------------------------------------------------------
# Data generation helpers
# ---------------------------------------------------------------------------

ASSIGNEES = ["수아", "지훈", "채린", "민준", "서연"]
CATEGORIES = ["creative", "media", "reporting", "strategy", "etc"]
PRIORITIES = ["low", "medium", "high"]
STATUSES = ["open", "in_progress", "done", "blocked"]


def _uid() -> str:
    return str(uuid.uuid4())


def _hash(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


def _rand_str(n: int = 12) -> str:
    return "".join(random.choices(string.ascii_lowercase, k=n))


def make_meeting(idx: int = 0) -> dict[str, Any]:
    mid = f"meeting_{idx:04d}"
    return {
        "meeting_id": mid,
        "title": f"회의 {idx}",
        "meeting_date": "2026-06-02",
        "audio_path": f"data/raw/sample_{idx}.mp3",
        "audio_hash": _hash(f"audio_{idx}"),
        "transcript_path": f"data/interim/transcript_{idx}.json",
        "source_type": "mock",
    }


def make_utterance(meeting_id: str, seq: int) -> dict[str, Any]:
    speaker = random.choice(ASSIGNEES)
    uid = _hash(meeting_id, str(seq))
    return {
        "utterance_id": uid,
        "meeting_id": meeting_id,
        "participant_id": None,
        "speaker_raw": speaker,
        "speaker_normalized": speaker,
        "text": f"발화 내용 {seq}: {_rand_str(20)}",
        "start_sec": seq * 5.0,
        "end_sec": seq * 5.0 + 4.5,
        "sequence_no": seq,
        "source": "mock",
    }


def make_action_item(
    meeting_id: str, seq: int, utterance_id: str
) -> dict[str, Any]:
    assignee = random.choice(ASSIGNEES)
    category = random.choice(CATEGORIES)
    priority = random.choice(PRIORITIES)
    sig = _hash(meeting_id, assignee, category, str(seq))
    dedup = _hash(meeting_id, assignee, category, "2026-06-30", sig)
    aid = _hash(meeting_id, str(seq), "action")
    llm_conf = round(random.uniform(0.5, 1.0), 3)
    val_score = round(random.uniform(0.4, 1.0), 3)
    final = round(min(llm_conf, val_score), 3)
    return {
        "action_item_id": aid,
        "dedup_key": dedup,
        "meeting_id": meeting_id,
        "chunk_id": None,
        "extraction_run_id": None,
        "sequence_no": seq,
        "assignee": assignee,
        "assignee_normalized": assignee,
        "description": f"태스크 {seq}: {_rand_str(30)}를 처리해 주세요.",
        "normalized_task_signature": sig,
        "category": category,
        "due_date": "2026-06-30",
        "priority": priority,
        "status": "open",
        "llm_confidence": llm_conf,
        "validation_score": val_score,
        "final_confidence": final,
        "review_required": 1 if final < 0.6 else 0,
        "risk_flags_json": "[]",
        "campaign_context": f"캠페인_{seq % 5}",
        "advertiser_context": f"광고주_{seq % 3}",
    }


def make_source(action_item_id: str, utterance_id: str) -> dict[str, Any]:
    return {
        "action_item_id": action_item_id,
        "utterance_id": utterance_id,
        "evidence_text": f"근거 발화 {_rand_str(20)}",
        "relevance_score": round(random.uniform(0.6, 1.0), 3),
    }


# ---------------------------------------------------------------------------
# SQLite thin wrapper matching the base interface
# ---------------------------------------------------------------------------

class SQLiteBenchClient:
    """Wraps sqlite3 directly for benchmark parity."""

    name = "SQLite"

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = OFF")
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")
        return self._conn

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def init_schema(self) -> None:
        schema_path = PROJECT_ROOT / "db" / "schema.sql"
        sql = schema_path.read_text(encoding="utf-8")
        conn = self._get_conn()
        conn.executescript(sql)
        # schema.sql enables FK; disable for benchmark to avoid FK overhead
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.commit()

    def upsert(
        self,
        table: str,
        values: dict[str, Any],
        conflict_columns: list[str],
        update_columns: list[str] | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        columns = list(values.keys())
        update_cols = [c for c in (update_columns or columns) if c not in conflict_columns]
        placeholders = ", ".join(f":{c}" for c in columns)
        col_sql = ", ".join(columns)
        conflict_sql = ", ".join(conflict_columns)
        if update_cols:
            update_sql = ", ".join(f"{c} = excluded.{c}" for c in update_cols)
            sql = (
                f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders}) "
                f"ON CONFLICT ({conflict_sql}) DO UPDATE SET {update_sql}"
            )
        else:
            sql = (
                f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders}) "
                f"ON CONFLICT ({conflict_sql}) DO NOTHING"
            )
        conn_ = connection if connection is not None else self._get_conn()
        conn_.execute(sql, dict(values))
        if connection is None:
            conn_.commit()

    def upsert_single_tx(self, table: str, values: dict, conflict_columns: list, update_columns: list | None = None) -> None:
        self.upsert(table, values, conflict_columns, update_columns)

    def upsert_in_tx(self, conn: sqlite3.Connection, table: str, values: dict, conflict_columns: list, update_columns: list | None = None) -> None:
        self.upsert(table, values, conflict_columns, update_columns, connection=conn)

    def begin_tx(self) -> sqlite3.Connection:
        conn = self._get_conn()
        return conn

    def commit(self, conn: sqlite3.Connection) -> None:
        conn.commit()

    def rollback(self, conn: sqlite3.Connection) -> None:
        conn.rollback()

    def fetch_all(self, query: str, params: Any = ()) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def fetch_one(self, query: str, params: Any = ()) -> dict | None:
        conn = self._get_conn()
        row = conn.execute(query, params).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

def timer(fn) -> tuple[float, Any]:
    t0 = time.perf_counter()
    result = fn()
    return time.perf_counter() - t0, result


class BenchmarkRunner:
    def __init__(self, name: str, n_rows: int = 500, big_rows: int = 10000) -> None:
        self.name = name
        self.n_rows = n_rows
        self.big_rows = big_rows
        self.results: dict[str, float] = {}

    def run_all(self, client: Any, meeting_id: str, utterances: list, action_items: list, sources: list) -> None:
        print(f"\n{'='*60}", flush=True)
        print(f"  Backend: {self.name}", flush=True)
        print(f"{'='*60}", flush=True)

        # 1. schema_init
        elapsed, _ = timer(client.init_schema)
        self.results["schema_init"] = elapsed
        print(f"  schema_init          {elapsed*1000:8.1f} ms", flush=True)

        # pre-insert meeting + utterances (needed for FK and JOIN tests)
        meeting = make_meeting(0)
        meeting["meeting_id"] = meeting_id
        self._insert_meeting(client, meeting)
        utt_subset = utterances[: min(50, len(utterances))]
        self._insert_utterances_batch(client, utt_subset)

        # 2. single_upsert (one tx per row)
        elapsed = self._bench_single_upsert(client, action_items[: self.n_rows])
        self.results["single_upsert"] = elapsed
        print(f"  single_upsert x{self.n_rows:<4d}  {elapsed*1000:8.1f} ms  ({elapsed/self.n_rows*1000:.2f} ms/row)", flush=True)

        # clear action_items for batch test
        self._clear_action_items(client)

        # 3. batch_upsert (all in one tx)
        elapsed = self._bench_batch_upsert(client, action_items[: self.n_rows])
        self.results["batch_upsert"] = elapsed
        print(f"  batch_upsert x{self.n_rows:<4d}  {elapsed*1000:8.1f} ms  ({elapsed/self.n_rows*1000:.2f} ms/row)", flush=True)

        # 4. larger batch upsert
        big_items = [
            make_action_item(meeting_id, i + 10000, random.choice(utt_subset)["utterance_id"])
            for i in range(self.big_rows)
        ]
        self._clear_action_items(client)
        elapsed = self._bench_batch_upsert(client, big_items)
        self.results["batch_upsert_10k"] = elapsed
        print(f"  batch_upsert x{self.big_rows:<4d}  {elapsed*1000:8.1f} ms  ({elapsed/self.big_rows*1000:.2f} ms/row)", flush=True)

        # sources must reference items that are currently in the DB (big_items)
        live_sources = [
            make_source(big_items[i]["action_item_id"], utt_subset[i % len(utt_subset)]["utterance_id"])
            for i in range(self.n_rows)
        ]
        self._insert_sources_batch(client, live_sources)

        # 5. pk_lookup ×200
        sample_ids = [ai["action_item_id"] for ai in big_items[:200]]
        elapsed = self._bench_pk_lookup(client, sample_ids)
        self.results["pk_lookup"] = elapsed
        print(f"  pk_lookup x200       {elapsed*1000:8.1f} ms  ({elapsed/200*1000:.3f} ms/lookup)", flush=True)

        # 6. agg_query ×20
        elapsed = self._bench_agg_query(client, meeting_id, 20)
        self.results["agg_query"] = elapsed
        print(f"  agg_query x20        {elapsed*1000:8.1f} ms  ({elapsed/20*1000:.2f} ms/query)", flush=True)

        # 7. join_query ×20
        elapsed = self._bench_join_query(client, meeting_id, 20)
        self.results["join_query"] = elapsed
        print(f"  join_query x20       {elapsed*1000:8.1f} ms  ({elapsed/20*1000:.2f} ms/query)", flush=True)

        # 8. full_scan ×20
        elapsed = self._bench_full_scan(client, 20)
        self.results["full_scan"] = elapsed
        print(f"  full_scan x20        {elapsed*1000:8.1f} ms  ({elapsed/20*1000:.2f} ms/scan)", flush=True)

    # ------------------------------------------------------------------
    # Backend-agnostic insert helpers
    # ------------------------------------------------------------------

    def _insert_meeting(self, client: Any, meeting: dict) -> None:
        _safe_upsert(client, "meetings", meeting, ["meeting_id"])

    def _insert_utterances_batch(self, client: Any, utts: list) -> None:
        _safe_upsert_many(client, "utterances", utts, ["meeting_id", "sequence_no"])

    def _insert_sources_batch(self, client: Any, sources: list) -> None:
        _safe_upsert_many(client, "action_item_sources", sources, ["action_item_id", "utterance_id"])

    def _clear_action_items(self, client: Any) -> None:
        _safe_execute(client, "DELETE FROM action_item_sources")
        _safe_execute(client, "DELETE FROM action_items")

    def _bench_single_upsert(self, client: Any, items: list) -> float:
        t0 = time.perf_counter()
        for item in items:
            _safe_upsert(client, "action_items", item, ["meeting_id", "dedup_key"])
        return time.perf_counter() - t0

    def _bench_batch_upsert(self, client: Any, items: list) -> float:
        t0 = time.perf_counter()
        _safe_upsert_many(client, "action_items", items, ["meeting_id", "dedup_key"])
        return time.perf_counter() - t0

    def _bench_pk_lookup(self, client: Any, ids: list) -> float:
        t0 = time.perf_counter()
        for aid in ids:
            _safe_fetch_one(client, "SELECT * FROM action_items WHERE action_item_id = %s", [aid])
        return time.perf_counter() - t0

    def _bench_agg_query(self, client: Any, meeting_id: str, repeats: int) -> float:
        sql = (
            "SELECT assignee_normalized, COUNT(*) AS cnt, "
            "AVG(final_confidence) AS avg_conf, SUM(review_required) AS risky "
            "FROM action_items WHERE meeting_id = %s "
            "GROUP BY assignee_normalized ORDER BY cnt DESC"
        )
        t0 = time.perf_counter()
        for _ in range(repeats):
            _safe_fetch_all(client, sql, [meeting_id])
        return time.perf_counter() - t0

    def _bench_join_query(self, client: Any, meeting_id: str, repeats: int) -> float:
        sql = (
            "SELECT a.action_item_id, a.assignee_normalized, a.description, "
            "s.evidence_text, u.text AS utterance_text "
            "FROM action_items a "
            "LEFT JOIN action_item_sources s ON a.action_item_id = s.action_item_id "
            "LEFT JOIN utterances u ON s.utterance_id = u.utterance_id "
            "WHERE a.meeting_id = %s "
            "ORDER BY a.final_confidence ASC "
            "LIMIT 50"
        )
        t0 = time.perf_counter()
        for _ in range(repeats):
            _safe_fetch_all(client, sql, [meeting_id])
        return time.perf_counter() - t0

    def _bench_full_scan(self, client: Any, repeats: int) -> float:
        sql = "SELECT * FROM action_items ORDER BY final_confidence ASC, created_at DESC"
        t0 = time.perf_counter()
        for _ in range(repeats):
            _safe_fetch_all(client, sql, [])
        return time.perf_counter() - t0


# ---------------------------------------------------------------------------
# Backend-agnostic dispatch helpers
# (SQLiteBenchClient uses sqlite3 natively; DuckDB/PG use BaseDBClient)
# ---------------------------------------------------------------------------

def _is_sqlite(client: Any) -> bool:
    return isinstance(client, SQLiteBenchClient)


def _adapt_sql(sql: str, client: Any) -> str:
    """Convert %s placeholders to ? for SQLite and DuckDB."""
    if isinstance(client, PostgreSQLClient):
        return sql
    return sql.replace("%s", "?")


def _safe_upsert(client: Any, table: str, values: dict, conflict_cols: list) -> None:
    if _is_sqlite(client):
        client.upsert_single_tx(table, values, conflict_cols)
    else:
        client.upsert(table, values, conflict_cols)


def _safe_upsert_many(client: Any, table: str, rows: list, conflict_cols: list) -> None:
    if _is_sqlite(client):
        conn = client.begin_tx()
        for row in rows:
            client.upsert_in_tx(conn, table, row, conflict_cols)
        client.commit(conn)
    else:
        client.upsert_many(table, rows, conflict_cols)


def _safe_execute(client: Any, sql: str) -> None:
    if _is_sqlite(client):
        conn = client._get_conn()
        conn.execute(sql)
        conn.commit()
    else:
        client.execute(sql)


def _safe_fetch_all(client: Any, sql: str, params: list) -> list:
    adapted = _adapt_sql(sql, client)
    if _is_sqlite(client):
        return client.fetch_all(adapted, params)
    return client.fetch_all(adapted, params)


def _safe_fetch_one(client: Any, sql: str, params: list) -> dict | None:
    adapted = _adapt_sql(sql, client)
    if _is_sqlite(client):
        return client.fetch_one(adapted, params)
    return client.fetch_one(adapted, params)


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def metric_labels(big_rows: int) -> list[tuple[str, str]]:
    return [
    ("schema_init", "Schema init"),
        ("single_upsert", "Single upsert"),
        ("batch_upsert", "Batch upsert"),
        ("batch_upsert_10k", f"Batch upsert x{big_rows}"),
        ("pk_lookup", "PK lookup x200"),
        ("agg_query", "Agg query x20"),
        ("join_query", "JOIN query x20"),
        ("full_scan", "Full scan x20"),
    ]


def print_report(all_results: dict[str, dict[str, float]], big_rows: int) -> None:
    backends = list(all_results.keys())
    col_w = 16
    label_w = 22
    metrics = metric_labels(big_rows)

    print("\n\n" + "=" * 80)
    print("  DB BENCHMARK RESULTS  (lower = faster, all times in ms)")
    print("=" * 80)

    header = f"{'Metric':<{label_w}}" + "".join(f"{b:>{col_w}}" for b in backends)
    print(header)
    print("-" * (label_w + col_w * len(backends)))

    for key, label in metrics:
        row = f"{label:<{label_w}}"
        vals = {b: all_results[b].get(key) for b in backends}
        available = {b: v for b, v in vals.items() if v is not None}
        fastest = min(available.values()) if available else None

        for b in backends:
            v = vals[b]
            if v is None:
                cell = "N/A"
            else:
                ms = v * 1000
                marker = " *" if (fastest is not None and v == fastest and len(available) > 1) else "  "
                cell = f"{ms:.1f}{marker}"
            row += f"{cell:>{col_w}}"
        print(row)

    print("-" * (label_w + col_w * len(backends)))
    print("  * = fastest for this metric")

    print("\n  Relative speed (vs SQLite baseline):")
    if "SQLite" in all_results:
        baseline = all_results["SQLite"]
        header2 = f"{'Metric':<{label_w}}" + "".join(f"{b:>{col_w}}" for b in backends if b != "SQLite")
        print(header2)
        print("-" * (label_w + col_w * (len(backends) - 1)))
        for key, label in metrics:
            base_t = baseline.get(key)
            if base_t is None:
                continue
            row = f"{label:<{label_w}}"
            for b in backends:
                if b == "SQLite":
                    continue
                t = all_results[b].get(key)
                if t is None:
                    cell = "N/A"
                else:
                    ratio = base_t / t
                    direction = "faster" if ratio > 1 else "slower"
                    cell = f"{ratio:.2f}x {direction}"
                row += f"{cell:>{col_w}}"
            print(row)

    print("=" * 80)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="DB benchmark: SQLite vs DuckDB vs PostgreSQL")
    parser.add_argument("--rows", type=int, default=500, help="Rows for single/batch upsert (default 500)")
    parser.add_argument(
        "--big-rows",
        type=int,
        default=10000,
        help="Rows for larger batch/full-scan test (default 10000)",
    )
    parser.add_argument(
        "--pg-dsn",
        default=os.environ.get("PGDSN", "postgresql://postgres:postgres@localhost:5432/mobidays_bench"),
        help="PostgreSQL DSN (skip PG if not reachable)",
    )
    parser.add_argument("--skip-sqlite", action="store_true")
    parser.add_argument("--skip-duckdb", action="store_true")
    parser.add_argument("--skip-pg", action="store_true")
    args = parser.parse_args()

    random.seed(42)
    n = args.rows
    big_n = args.big_rows

    # Generate shared synthetic data once
    meeting_id = "bench_meeting_0001"
    utterances = [make_utterance(meeting_id, i) for i in range(max(50, n, big_n))]
    action_items = [
        make_action_item(meeting_id, i, utterances[i % len(utterances)]["utterance_id"])
        for i in range(max(n, big_n))
    ]
    sources = [
        make_source(action_items[i]["action_item_id"], utterances[i % len(utterances)]["utterance_id"])
        for i in range(n)
    ]

    all_results: dict[str, dict[str, float]] = {}
    bench_dir = PROJECT_ROOT / "data" / "bench"
    bench_dir.mkdir(parents=True, exist_ok=True)

    # --- SQLite ---
    if not args.skip_sqlite:
        sqlite_path = bench_dir / "bench_sqlite.db"
        sqlite_path.unlink(missing_ok=True)
        client = SQLiteBenchClient(str(sqlite_path))
        runner = BenchmarkRunner("SQLite", n_rows=n, big_rows=big_n)
        try:
            runner.run_all(client, meeting_id, utterances, action_items, sources)
            all_results["SQLite"] = runner.results
        finally:
            client.close()

    # --- DuckDB ---
    if not args.skip_duckdb:
        duckdb_path = bench_dir / "bench_duckdb.db"
        duckdb_path.unlink(missing_ok=True)
        client = DuckDBClient(duckdb_path)
        runner = BenchmarkRunner("DuckDB", n_rows=n, big_rows=big_n)
        try:
            runner.run_all(client, meeting_id, utterances, action_items, sources)
            all_results["DuckDB"] = runner.results
        finally:
            client.close()

    # --- PostgreSQL (optional) ---
    if not args.skip_pg:
        print(f"\n  Trying PostgreSQL at {args.pg_dsn} ...")
        try:
            pg_client = PostgreSQLClient(dsn=args.pg_dsn, connect_timeout=3)
            pg_client._get_conn()  # test connection
            # drop and recreate bench tables for clean run
            _drop_pg_tables(pg_client)
            runner = BenchmarkRunner("PostgreSQL", n_rows=n, big_rows=big_n)
            try:
                runner.run_all(pg_client, meeting_id, utterances, action_items, sources)
                all_results["PostgreSQL"] = runner.results
            finally:
                pg_client.close()
        except Exception as exc:
            print(f"  PostgreSQL skipped: {exc}")
            print("  To run PG: start a local server or Docker, then pass --pg-dsn")

    if all_results:
        print_report(all_results, big_rows=big_n)
    else:
        print("No backends ran successfully.")


def _drop_pg_tables(client: PostgreSQLClient) -> None:
    tables = [
        "slack_payloads", "action_item_events", "issue_keywords",
        "action_item_sources", "action_items", "extraction_runs",
        "chunk_utterances", "chunks", "utterances", "participants",
        "stt_runs", "meetings",
    ]
    conn = client._get_conn()
    with conn.cursor() as cur:
        for t in tables:
            cur.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    conn.commit()


if __name__ == "__main__":
    main()
