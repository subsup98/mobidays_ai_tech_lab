"""Pre-build FAISS embeddings index from all action items in the DB.

Usage:
    python scripts/build_embeddings.py
    python scripts/build_embeddings.py --db data/app.db
    python scripts/build_embeddings.py --db-backend postgres --pg-dsn postgresql://...
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv()

from analytics.embeddings import generate_and_store_embeddings, vector_db_path
from db.sqlite_client import SQLiteClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Build FAISS embeddings index")
    parser.add_argument("--db", default=os.getenv("DATABASE_PATH", "data/app.db"))
    parser.add_argument("--db-backend", default=os.getenv("DB_BACKEND", "sqlite"))
    parser.add_argument("--pg-dsn", default=os.getenv("DATABASE_URL", ""))
    args = parser.parse_args()

    if args.db_backend == "postgres":
        from db.pg_client import PostgreSQLClient
        client = PostgreSQLClient(dsn=args.pg_dsn)
    else:
        client = SQLiteClient(args.db)
    client.init_schema()

    print(f"DB: {args.db_backend} / {args.db or args.pg_dsn}")
    print(f"Vector DB: {vector_db_path()}")
    print("임베딩 생성 중...")

    n = generate_and_store_embeddings(client, meeting_id=None)
    print(f"완료: {n}건 인덱싱됨")


if __name__ == "__main__":
    main()
