from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

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


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA temp_store = MEMORY")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript((PROJECT_ROOT / "db" / "schema.sql").read_text(encoding="utf-8"))
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.commit()


def fetch_one(conn: sqlite3.Connection, sql: str, params: list[Any]) -> dict[str, Any] | None:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def fetch_all(conn: sqlite3.Connection, sql: str, params: list[Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def executemany_upsert(
    conn: sqlite3.Connection,
    table: str,
    rows: list[dict[str, Any]],
    columns: list[str],
    conflict_columns: list[str],
) -> None:
    sql = upsert_sql(table, columns, conflict_columns)
    conn.executemany(sql, [row_tuple(row, columns) for row in rows])
    conn.commit()


def single_upsert(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    sql = upsert_sql("action_items", ACTION_ITEM_COLUMNS, ["meeting_id", "dedup_key"])
    for row in rows:
        conn.execute(sql, row_tuple(row, ACTION_ITEM_COLUMNS))
        conn.commit()


def run(rows: int, db_path: Path) -> dict[str, float]:
    data = make_data(rows)
    utt_subset = data.utterances[:50]
    items = data.action_items[:rows]
    big_items = data.action_items[:10000]
    sources = make_sources(big_items, utt_subset, rows)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)
    conn = connect(db_path)
    try:
        results: dict[str, float] = {}
        results["schema_init"] = time_call(lambda: init_schema(conn))
        executemany_upsert(conn, "meetings", [data.meeting], MEETING_COLUMNS, ["meeting_id"])
        executemany_upsert(conn, "utterances", utt_subset, UTTERANCE_COLUMNS, ["meeting_id", "sequence_no"])

        results["single_upsert"] = time_call(lambda: single_upsert(conn, items))

        conn.execute("DELETE FROM action_item_sources")
        conn.execute("DELETE FROM action_items")
        conn.commit()
        results["bulk_load"] = time_call(
            lambda: executemany_upsert(conn, "action_items", items, ACTION_ITEM_COLUMNS, ["meeting_id", "dedup_key"])
        )

        conn.execute("DELETE FROM action_item_sources")
        conn.execute("DELETE FROM action_items")
        conn.commit()
        results["bulk_load_10k"] = time_call(
            lambda: executemany_upsert(conn, "action_items", big_items, ACTION_ITEM_COLUMNS, ["meeting_id", "dedup_key"])
        )
        executemany_upsert(conn, "action_item_sources", sources, SOURCE_COLUMNS, ["action_item_id", "utterance_id"])

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
    parser = argparse.ArgumentParser(description="SQLite optimized benchmark")
    parser.add_argument("--rows", type=int, default=500)
    parser.add_argument("--db-path", type=Path, default=BENCH_DIR / "bench_sqlite_optimized.db")
    args = parser.parse_args()
    results = run(args.rows, args.db_path)
    print_backend_results("SQLite", results, args.rows)


if __name__ == "__main__":
    main()
