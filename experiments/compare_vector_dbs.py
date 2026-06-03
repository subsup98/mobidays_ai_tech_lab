"""Compare FAISS and Chroma for action-item similarity search.

Default scale models 200 users * 3 daily action updates = 600 records.
"""
from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import chromadb
import faiss
import numpy as np

from analytics.embeddings import _embed, build_embedding_text
from db.pg_client import PostgreSQLClient


DEFAULT_PG_DSN = "postgresql://postgres:postgres@localhost:5432/mobidays_app"
DEFAULT_QUERIES = [
    "비주얼 카피 정리",
    "임시 컷 담당자 푸시",
    "픽셀 보정 내일 공유",
    "A/B 테스트 다시 세팅",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare FAISS and Chroma vector search.")
    parser.add_argument("--pg-dsn", default=DEFAULT_PG_DSN)
    parser.add_argument("--records", type=int, default=600)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output", default="data/interim/vector_db_comparison.json")
    args = parser.parse_args()

    base_items = load_action_items(args.pg_dsn)
    records = expand_records(base_items, args.records)
    queries = DEFAULT_QUERIES

    faiss_result = bench_faiss(records, queries, top_k=args.top_k)
    chroma_result = bench_chroma(records, queries, top_k=args.top_k)

    report = {
        "records": len(records),
        "queries": queries,
        "faiss": faiss_result,
        "chroma": chroma_result,
        "recommendation": recommend(faiss_result, chroma_result),
    }

    output_path = PROJECT_ROOT / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))


def load_action_items(pg_dsn: str) -> list[dict[str, Any]]:
    client = PostgreSQLClient(pg_dsn)
    rows = client.fetch_all(
        """
        SELECT
            a.action_item_id,
            a.meeting_id,
            a.description,
            a.assignee_normalized,
            a.category,
            a.campaign_context,
            a.final_confidence,
            m.title AS meeting_title
        FROM action_items a
        JOIN meetings m ON m.meeting_id = a.meeting_id
        ORDER BY a.action_item_id
        """
    )
    if not rows:
        raise RuntimeError("No action items found in PostgreSQL.")
    return rows


def expand_records(base_items: list[dict[str, Any]], target_count: int) -> list[dict[str, Any]]:
    records = []
    for idx in range(target_count):
        base = dict(base_items[idx % len(base_items)])
        user_no = idx % 200
        day_no = idx // 600
        base["action_item_id"] = f"{base['action_item_id']}_u{user_no:03d}_n{idx:05d}"
        base["meeting_id"] = f"{base['meeting_id']}_day{day_no:03d}"
        base["meeting_title"] = f"{base['meeting_title']} / user {user_no:03d}"
        records.append(base)
    return records


def vectorize_records(records: list[dict[str, Any]]) -> tuple[np.ndarray, list[str], list[str], list[dict[str, Any]]]:
    vectors = []
    ids = []
    docs = []
    metadatas = []
    for record in records:
        text = build_embedding_text(record)
        vector, model_name = _embed(text)
        vectors.append(vector)
        ids.append(record["action_item_id"])
        docs.append(record["description"])
        metadatas.append(
            {
                "meeting_id": record["meeting_id"],
                "assignee_normalized": record.get("assignee_normalized") or "",
                "category": record.get("category") or "",
                "final_confidence": float(record.get("final_confidence") or 0),
                "meeting_title": record.get("meeting_title") or "",
                "model_name": model_name,
            }
        )
    return np.asarray(vectors, dtype="float32"), ids, docs, metadatas


def query_vectors(queries: list[str]) -> np.ndarray:
    return np.asarray([_embed(query)[0] for query in queries], dtype="float32")


def bench_faiss(records: list[dict[str, Any]], queries: list[str], top_k: int) -> dict[str, Any]:
    vectors, ids, docs, metadatas = vectorize_records(records)
    q_vectors = query_vectors(queries)

    start = time.perf_counter()
    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors)
    build_sec = time.perf_counter() - start

    latencies = []
    examples = []
    for query, q_vector in zip(queries, q_vectors):
        start = time.perf_counter()
        scores, indices = index.search(q_vector.reshape(1, -1), top_k)
        latencies.append(time.perf_counter() - start)
        examples.append(
            {
                "query": query,
                "top": [
                    {
                        "id": ids[int(i)],
                        "description": docs[int(i)],
                        "category": metadatas[int(i)]["category"],
                        "score": round(float(score), 4),
                    }
                    for score, i in zip(scores[0], indices[0])
                    if int(i) >= 0
                ],
            }
        )

    return summarize("faiss", build_sec, latencies, examples)


def bench_chroma(records: list[dict[str, Any]], queries: list[str], top_k: int) -> dict[str, Any]:
    vectors, ids, docs, metadatas = vectorize_records(records)
    q_vectors = query_vectors(queries)
    chroma_dir = PROJECT_ROOT / "data" / "interim" / "chroma_vector_bench"
    shutil.rmtree(chroma_dir, ignore_errors=True)

    start = time.perf_counter()
    client = chromadb.PersistentClient(path=str(chroma_dir))
    collection = client.get_or_create_collection(
        name="action_items",
        metadata={"hnsw:space": "cosine"},
    )
    batch_size = 1000
    vector_list = vectors.tolist()
    for start_idx in range(0, len(ids), batch_size):
        end_idx = start_idx + batch_size
        collection.upsert(
            ids=ids[start_idx:end_idx],
            embeddings=vector_list[start_idx:end_idx],
            documents=docs[start_idx:end_idx],
            metadatas=metadatas[start_idx:end_idx],
        )
    build_sec = time.perf_counter() - start

    latencies = []
    examples = []
    for query, q_vector in zip(queries, q_vectors):
        start = time.perf_counter()
        result = collection.query(
            query_embeddings=[q_vector.tolist()],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )
        latencies.append(time.perf_counter() - start)
        examples.append(
            {
                "query": query,
                "top": [
                    {
                        "id": result["ids"][0][idx],
                        "description": result["documents"][0][idx],
                        "category": result["metadatas"][0][idx]["category"],
                        "distance": round(float(result["distances"][0][idx]), 4),
                    }
                    for idx in range(len(result["ids"][0]))
                ],
            }
        )

    return summarize("chroma", build_sec, latencies, examples)


def summarize(name: str, build_sec: float, latencies: list[float], examples: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "name": name,
        "build_ms": round(build_sec * 1000, 3),
        "avg_query_ms": round(statistics.mean(latencies) * 1000, 3),
        "p95_query_ms": round(percentile(latencies, 0.95) * 1000, 3),
        "max_query_ms": round(max(latencies) * 1000, 3),
        "examples": examples,
    }


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return ordered[index]


def recommend(faiss_result: dict[str, Any], chroma_result: dict[str, Any]) -> str:
    if faiss_result["avg_query_ms"] < chroma_result["avg_query_ms"]:
        return (
            "FAISS is faster for raw local vector search. "
            "Chroma is still useful when metadata/document management matters more."
        )
    return (
        "Chroma is fast enough and simpler for metadata/document-backed search. "
        "FAISS remains a good choice for larger pure-vector indexes."
    )


if __name__ == "__main__":
    main()
