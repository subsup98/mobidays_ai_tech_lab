from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from experiments.db_bench_common import METRICS
from experiments.db_bench_duckdb import run as run_duckdb
from experiments.db_bench_sqlite import run as run_sqlite


def print_comparison(results: dict[str, dict[str, float]]) -> None:
    backends = list(results)
    label_width = 18
    column_width = 16
    print("\n\n" + "=" * (label_width + column_width * len(backends)))
    print("  OPTIMIZED DB BENCHMARK COMPARISON  (lower = faster, ms)")
    print("=" * (label_width + column_width * len(backends)))
    print(f"{'Metric':<{label_width}}" + "".join(f"{backend:>{column_width}}" for backend in backends))
    print("-" * (label_width + column_width * len(backends)))
    for key, label in METRICS:
        row = f"{label:<{label_width}}"
        values = {backend: results[backend].get(key) for backend in backends}
        available = [value for value in values.values() if value is not None]
        fastest = min(available) if available else None
        for backend in backends:
            value = values[backend]
            if value is None:
                cell = "N/A"
            else:
                marker = " *" if value == fastest and len(available) > 1 else "  "
                cell = f"{value * 1000:.1f}{marker}"
            row += f"{cell:>{column_width}}"
        print(row)
    print("-" * (label_width + column_width * len(backends)))
    print("  * = fastest for this metric")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run optimized DB benchmarks")
    parser.add_argument("--rows", type=int, default=500)
    parser.add_argument("--skip-sqlite", action="store_true")
    parser.add_argument("--skip-duckdb", action="store_true")
    parser.add_argument("--skip-pg", action="store_true")
    parser.add_argument(
        "--pg-dsn",
        default=os.environ.get("PGDSN", "postgresql://postgres:postgres@localhost:5432/mobidays_bench"),
    )
    args = parser.parse_args()

    results: dict[str, dict[str, float]] = {}
    if not args.skip_sqlite:
        results["SQLite"] = run_sqlite(args.rows, PROJECT_ROOT / "data" / "bench" / "bench_sqlite_optimized.db")
    if not args.skip_duckdb:
        results["DuckDB"] = run_duckdb(args.rows, PROJECT_ROOT / "data" / "bench" / "bench_duckdb_optimized.db")
    if not args.skip_pg:
        try:
            from experiments.db_bench_postgres import run as run_postgres

            results["PostgreSQL"] = run_postgres(args.rows, args.pg_dsn, 3)
        except Exception as exc:
            print(f"\nPostgreSQL skipped: {exc}")
    print_comparison(results)


if __name__ == "__main__":
    main()
