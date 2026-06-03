from __future__ import annotations

import hashlib
import random
import string
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BENCH_DIR = PROJECT_ROOT / "data" / "bench"

ASSIGNEES = ["alice", "bob", "charlie", "dana", "erin"]
CATEGORIES = ["creative", "media", "reporting", "strategy", "etc"]
PRIORITIES = ["low", "medium", "high"]

ACTION_ITEM_COLUMNS = [
    "action_item_id",
    "dedup_key",
    "meeting_id",
    "chunk_id",
    "extraction_run_id",
    "sequence_no",
    "assignee",
    "assignee_normalized",
    "description",
    "normalized_task_signature",
    "category",
    "due_date",
    "priority",
    "status",
    "llm_confidence",
    "validation_score",
    "final_confidence",
    "review_required",
    "risk_flags_json",
    "campaign_context",
    "advertiser_context",
]

UTTERANCE_COLUMNS = [
    "utterance_id",
    "meeting_id",
    "participant_id",
    "speaker_raw",
    "speaker_normalized",
    "text",
    "start_sec",
    "end_sec",
    "sequence_no",
    "source",
]

SOURCE_COLUMNS = [
    "action_item_id",
    "utterance_id",
    "evidence_text",
    "relevance_score",
]

MEETING_COLUMNS = [
    "meeting_id",
    "title",
    "meeting_date",
    "audio_path",
    "audio_hash",
    "transcript_path",
    "source_type",
]


@dataclass
class BenchmarkData:
    meeting: dict[str, Any]
    utterances: list[dict[str, Any]]
    action_items: list[dict[str, Any]]


def hash_key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


def rand_str(length: int = 12) -> str:
    return "".join(random.choices(string.ascii_lowercase, k=length))


def make_meeting(meeting_id: str) -> dict[str, Any]:
    return {
        "meeting_id": meeting_id,
        "title": "Benchmark meeting",
        "meeting_date": "2026-06-02",
        "audio_path": "data/raw/bench.mp3",
        "audio_hash": hash_key("bench-audio"),
        "transcript_path": "data/interim/bench.json",
        "source_type": "mock",
    }


def make_utterance(meeting_id: str, seq: int) -> dict[str, Any]:
    speaker = random.choice(ASSIGNEES)
    return {
        "utterance_id": hash_key(meeting_id, str(seq), "utterance"),
        "meeting_id": meeting_id,
        "participant_id": None,
        "speaker_raw": speaker,
        "speaker_normalized": speaker,
        "text": f"Benchmark utterance {seq}: {rand_str(20)}",
        "start_sec": seq * 5.0,
        "end_sec": seq * 5.0 + 4.5,
        "sequence_no": seq,
        "source": "mock",
    }


def make_action_item(meeting_id: str, seq: int, utterance_id: str) -> dict[str, Any]:
    assignee = random.choice(ASSIGNEES)
    category = random.choice(CATEGORIES)
    signature = hash_key(meeting_id, assignee, category, str(seq))
    llm_confidence = round(random.uniform(0.5, 1.0), 3)
    validation_score = round(random.uniform(0.4, 1.0), 3)
    final_confidence = round(min(llm_confidence, validation_score), 3)
    return {
        "action_item_id": hash_key(meeting_id, str(seq), "action"),
        "dedup_key": hash_key(meeting_id, assignee, category, "2026-06-30", signature),
        "meeting_id": meeting_id,
        "chunk_id": None,
        "extraction_run_id": None,
        "sequence_no": seq,
        "assignee": assignee,
        "assignee_normalized": assignee,
        "description": f"Benchmark task {seq}: process {rand_str(30)}",
        "normalized_task_signature": signature,
        "category": category,
        "due_date": "2026-06-30",
        "priority": random.choice(PRIORITIES),
        "status": "open",
        "llm_confidence": llm_confidence,
        "validation_score": validation_score,
        "final_confidence": final_confidence,
        "review_required": 1 if final_confidence < 0.6 else 0,
        "risk_flags_json": "[]",
        "campaign_context": f"campaign_{seq % 5}",
        "advertiser_context": f"advertiser_{seq % 3}",
    }


def make_source(action_item_id: str, utterance_id: str) -> dict[str, Any]:
    return {
        "action_item_id": action_item_id,
        "utterance_id": utterance_id,
        "evidence_text": f"Evidence {rand_str(20)}",
        "relevance_score": round(random.uniform(0.6, 1.0), 3),
    }


def make_data(rows: int, seed: int = 42) -> BenchmarkData:
    random.seed(seed)
    meeting_id = "bench_meeting_0001"
    utterances = [make_utterance(meeting_id, i) for i in range(max(50, rows))]
    action_items = [
        make_action_item(meeting_id, i, utterances[i % len(utterances)]["utterance_id"])
        for i in range(max(rows, 10000))
    ]
    return BenchmarkData(
        meeting=make_meeting(meeting_id),
        utterances=utterances,
        action_items=action_items,
    )


def make_sources(action_items: list[dict[str, Any]], utterances: list[dict[str, Any]], rows: int) -> list[dict[str, Any]]:
    return [
        make_source(action_items[i]["action_item_id"], utterances[i % len(utterances)]["utterance_id"])
        for i in range(rows)
    ]


def row_tuple(row: dict[str, Any], columns: list[str]) -> tuple[Any, ...]:
    return tuple(row[column] for column in columns)


def time_call(fn: Callable[[], Any]) -> float:
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start


def run_read_benchmarks(
    fetch_one: Callable[[str, list[Any]], dict[str, Any] | None],
    fetch_all: Callable[[str, list[Any]], list[dict[str, Any]]],
    placeholder: str,
    meeting_id: str,
    action_items: list[dict[str, Any]],
) -> dict[str, float]:
    ids = [item["action_item_id"] for item in action_items[:200]]
    pk_sql = f"SELECT * FROM action_items WHERE action_item_id = {placeholder}"
    agg_sql = (
        "SELECT assignee_normalized, COUNT(*) AS cnt, "
        "AVG(final_confidence) AS avg_conf, SUM(review_required) AS risky "
        f"FROM action_items WHERE meeting_id = {placeholder} "
        "GROUP BY assignee_normalized ORDER BY cnt DESC"
    )
    join_sql = (
        "SELECT a.action_item_id, a.assignee_normalized, a.description, "
        "s.evidence_text, u.text AS utterance_text "
        "FROM action_items a "
        "LEFT JOIN action_item_sources s ON a.action_item_id = s.action_item_id "
        "LEFT JOIN utterances u ON s.utterance_id = u.utterance_id "
        f"WHERE a.meeting_id = {placeholder} "
        "ORDER BY a.final_confidence ASC "
        "LIMIT 50"
    )
    scan_sql = "SELECT * FROM action_items ORDER BY final_confidence ASC, created_at DESC"

    return {
        "pk_lookup": time_call(lambda: [fetch_one(pk_sql, [aid]) for aid in ids]),
        "agg_query": time_call(lambda: [fetch_all(agg_sql, [meeting_id]) for _ in range(20)]),
        "join_query": time_call(lambda: [fetch_all(join_sql, [meeting_id]) for _ in range(20)]),
        "full_scan": time_call(lambda: [fetch_all(scan_sql, []) for _ in range(20)]),
    }


METRICS = [
    ("schema_init", "Schema init"),
    ("single_upsert", "Single upsert"),
    ("bulk_load", "Bulk load"),
    ("bulk_load_10k", "Bulk load 10k"),
    ("pk_lookup", "PK lookup x200"),
    ("agg_query", "Agg query x20"),
    ("join_query", "JOIN query x20"),
    ("full_scan", "Full scan x20"),
]


def print_backend_results(name: str, results: dict[str, float], rows: int) -> None:
    print(f"\n{'=' * 60}")
    print(f"  Backend: {name}")
    print(f"{'=' * 60}")
    for key, label in METRICS:
        value = results.get(key)
        if value is None:
            continue
        suffix = ""
        if key in {"single_upsert", "bulk_load"}:
            suffix = f"  ({value / rows * 1000:.3f} ms/row)"
        elif key == "bulk_load_10k":
            suffix = f"  ({value / 10000 * 1000:.3f} ms/row)"
        print(f"  {label:<18} {value * 1000:10.1f} ms{suffix}")
