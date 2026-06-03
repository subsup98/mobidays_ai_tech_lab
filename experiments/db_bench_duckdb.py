from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from db.duckdb_client import _SCHEMA_SQL
from experiments.db_bench_common import (
    ACTION_ITEM_COLUMNS,
    BENCH_DIR,
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


def init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    for statement in _SCHEMA_SQL.split(";"):
        stmt = statement.strip()
        if stmt:
            conn.execute(stmt)


def upsert_sql(table: str, columns: list[str], conflict_columns: list[str]) -> str:
    placeholders = ", ".join("?" for _ in columns)
    column_sql = ", ".join(columns)
    conflict_sql = ", ".join(conflict_columns)
    updates = ", ".join(
        f"{column} = excluded.{column}"
        for column in columns
        if column not in conflict_columns
    )
    return (
        f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict_sql}) DO UPDATE SET {updates}"
    )


def fetch_one(conn: duckdb.DuckDBPyConnection, sql: str, params: list[Any]) -> dict[str, Any] | None:
    result = conn.execute(sql, params)
    columns = [desc[0] for desc in result.description]
    row = result.fetchone()
    return dict(zip(columns, row)) if row else None


def fetch_all(conn: duckdb.DuckDBPyConnection, sql: str, params: list[Any]) -> list[dict[str, Any]]:
    result = conn.execute(sql, params)
    columns = [desc[0] for desc in result.description]
    return [dict(zip(columns, row)) for row in result.fetchall()]


def insert_dataframe(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    rows: list[dict[str, Any]],
    columns: list[str],
    view_name: str,
) -> None:
    df = pd.DataFrame(rows, columns=columns)
    conn.register(view_name, df)
    try:
        column_sql = ", ".join(columns)
        conn.execute(f"INSERT INTO {table} ({column_sql}) SELECT {column_sql} FROM {view_name}")
    finally:
        conn.unregister(view_name)


def run(rows: int, db_path: Path) -> dict[str, float]:
    data = make_data(rows)
    utt_subset = data.utterances[:50]
    items = data.action_items[:rows]
    big_items = data.action_items[:10000]
    sources = make_sources(big_items, utt_subset, rows)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)
    wal_path = Path(f"{db_path}.wal")
    wal_path.unlink(missing_ok=True)
    conn = duckdb.connect(db_path)
    try:
        results: dict[str, float] = {}
        results["schema_init"] = time_call(lambda: init_schema(conn))
        insert_dataframe(conn, "meetings", [data.meeting], MEETING_COLUMNS, "bench_meetings")
        insert_dataframe(conn, "utterances", utt_subset, UTTERANCE_COLUMNS, "bench_utterances")

        single_sql = upsert_sql("action_items", ACTION_ITEM_COLUMNS, ["meeting_id", "dedup_key"])
        results["single_upsert"] = time_call(
            lambda: [
                conn.execute(single_sql, row_tuple(item, ACTION_ITEM_COLUMNS))
                for item in items
            ]
        )

        conn.execute("DELETE FROM action_item_sources")
        conn.execute("DELETE FROM action_items")
        results["bulk_load"] = time_call(
            lambda: insert_dataframe(conn, "action_items", items, ACTION_ITEM_COLUMNS, "bench_action_items")
        )

        conn.execute("DELETE FROM action_item_sources")
        conn.execute("DELETE FROM action_items")
        results["bulk_load_10k"] = time_call(
            lambda: insert_dataframe(conn, "action_items", big_items, ACTION_ITEM_COLUMNS, "bench_action_items_10k")
        )
        insert_dataframe(conn, "action_item_sources", sources, SOURCE_COLUMNS, "bench_sources")

        results.update(
            run_read_benchmarks(
                lambda sql, params: fetch_one(conn, sql, params),
                lambda sql, params: fetch_all(conn, sql, params),
                "?",
                data.meeting["meeting_id"],
                big_items,
            )
        )
        return results
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="DuckDB optimized benchmark")
    parser.add_argument("--rows", type=int, default=500)
    parser.add_argument("--db-path", type=Path, default=BENCH_DIR / "bench_duckdb_optimized.db")
    args = parser.parse_args()
    results = run(args.rows, args.db_path)
    print_backend_results("DuckDB", results, args.rows)


if __name__ == "__main__":
    main()
