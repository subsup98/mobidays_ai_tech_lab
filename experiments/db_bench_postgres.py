from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None

from db.pg_client import _SCHEMA_SQL
from experiments.db_bench_common import (
    ACTION_ITEM_COLUMNS,
    MEETING_COLUMNS,
    SOURCE_COLUMNS,
    UTTERANCE_COLUMNS,
    make_data,
    make_sources,
    print_backend_results,
    row_tuple,
    run_read_benchmarks,
    time_call,
)


def init_schema(conn: Any) -> None:
    with conn.cursor() as cur:
        for statement in _SCHEMA_SQL.split(";"):
            stmt = statement.strip()
            if stmt:
                cur.execute(stmt)
    conn.commit()


def drop_tables(conn: Any) -> None:
    tables = [
        "slack_payloads",
        "action_item_events",
        "issue_keywords",
        "action_item_sources",
        "action_items",
        "extraction_runs",
        "chunk_utterances",
        "chunks",
        "utterances",
        "participants",
        "stt_runs",
        "meetings",
    ]
    with conn.cursor() as cur:
        for table in tables:
            cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    conn.commit()


def upsert_sql(table: str, columns: list[str], conflict_columns: list[str]) -> str:
    column_sql = ", ".join(columns)
    conflict_sql = ", ".join(conflict_columns)
    updates = ", ".join(
        f"{column} = EXCLUDED.{column}"
        for column in columns
        if column not in conflict_columns
    )
    return (
        f"INSERT INTO {table} ({column_sql}) VALUES %s "
        f"ON CONFLICT ({conflict_sql}) DO UPDATE SET {updates}"
    )


def bulk_upsert(
    conn: Any,
    table: str,
    rows: list[dict[str, Any]],
    columns: list[str],
    conflict_columns: list[str],
) -> None:
    sql = upsert_sql(table, columns, conflict_columns)
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            sql,
            [row_tuple(row, columns) for row in rows],
            page_size=1000,
        )
    conn.commit()


def single_upsert(conn: Any, rows: list[dict[str, Any]]) -> None:
    sql = upsert_sql("action_items", ACTION_ITEM_COLUMNS, ["meeting_id", "dedup_key"])
    with conn.cursor() as cur:
        for row in rows:
            psycopg2.extras.execute_values(cur, sql, [row_tuple(row, ACTION_ITEM_COLUMNS)])
            conn.commit()


def fetch_one(conn: Any, sql: str, params: list[Any]) -> dict[str, Any] | None:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None


def fetch_all(conn: Any, sql: str, params: list[Any]) -> list[dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


def run(rows: int, dsn: str, connect_timeout: int) -> dict[str, float]:
    if psycopg2 is None:
        raise RuntimeError("psycopg2 is not installed. Run: pip install psycopg2-binary")

    data = make_data(rows)
    utt_subset = data.utterances[:50]
    items = data.action_items[:rows]
    big_items = data.action_items[:10000]
    sources = make_sources(big_items, utt_subset, rows)

    conn = psycopg2.connect(dsn, connect_timeout=connect_timeout)
    try:
        conn.autocommit = False
        drop_tables(conn)
        results: dict[str, float] = {}
        results["schema_init"] = time_call(lambda: init_schema(conn))
        bulk_upsert(conn, "meetings", [data.meeting], MEETING_COLUMNS, ["meeting_id"])
        bulk_upsert(conn, "utterances", utt_subset, UTTERANCE_COLUMNS, ["meeting_id", "sequence_no"])

        results["single_upsert"] = time_call(lambda: single_upsert(conn, items))

        with conn.cursor() as cur:
            cur.execute("DELETE FROM action_item_sources")
            cur.execute("DELETE FROM action_items")
        conn.commit()
        results["bulk_load"] = time_call(
            lambda: bulk_upsert(conn, "action_items", items, ACTION_ITEM_COLUMNS, ["meeting_id", "dedup_key"])
        )

        with conn.cursor() as cur:
            cur.execute("DELETE FROM action_item_sources")
            cur.execute("DELETE FROM action_items")
        conn.commit()
        results["bulk_load_10k"] = time_call(
            lambda: bulk_upsert(conn, "action_items", big_items, ACTION_ITEM_COLUMNS, ["meeting_id", "dedup_key"])
        )
        bulk_upsert(conn, "action_item_sources", sources, SOURCE_COLUMNS, ["action_item_id", "utterance_id"])

        results.update(
            run_read_benchmarks(
                lambda sql, params: fetch_one(conn, sql, params),
                lambda sql, params: fetch_all(conn, sql, params),
                "%s",
                data.meeting["meeting_id"],
                big_items,
            )
        )
        return results
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="PostgreSQL optimized benchmark")
    parser.add_argument("--rows", type=int, default=500)
    parser.add_argument(
        "--pg-dsn",
        default=os.environ.get("PGDSN", "postgresql://postgres:postgres@localhost:5432/mobidays_bench"),
    )
    parser.add_argument("--connect-timeout", type=int, default=3)
    args = parser.parse_args()
    results = run(args.rows, args.pg_dsn, args.connect_timeout)
    print_backend_results("PostgreSQL", results, args.rows)


if __name__ == "__main__":
    main()
