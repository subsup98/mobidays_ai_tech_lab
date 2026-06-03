"""FAISS-backed similar-action search with local sentence-transformers embeddings."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import faiss
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VECTOR_DB_DIR = PROJECT_ROOT / "data" / "vector" / "faiss_action_items"
DEFAULT_EMBEDDING_MODEL = "text-embedding-004"
LOCAL_EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_DIM = 384  # paraphrase-multilingual-MiniLM-L12-v2 output dim
CONFIDENCE_THRESHOLD = 0.7

_local_model_cache: Any = None


def build_embedding_text(action_item: dict[str, Any]) -> str:
    parts = [action_item.get("description", "")]
    if action_item.get("category"):
        parts.append(action_item["category"])
    if action_item.get("campaign_context"):
        parts.append(action_item["campaign_context"])
    return " [SEP] ".join(str(p) for p in parts if p)


def vector_db_path() -> Path:
    return Path(os.getenv("VECTOR_DB_PATH", str(DEFAULT_VECTOR_DB_DIR)))


def _get_local_model() -> Any:
    global _local_model_cache
    if _local_model_cache is None:
        from sentence_transformers import SentenceTransformer
        model_name = os.getenv("LOCAL_EMBEDDING_MODEL", LOCAL_EMBEDDING_MODEL)
        _local_model_cache = SentenceTransformer(model_name)
    return _local_model_cache


def _local_embed(text: str) -> list[float]:
    model = _get_local_model()
    vector = model.encode(text, normalize_embeddings=True)
    return vector.tolist()


def _gemini_embed(text: str, gemini_client: object, model: str) -> list[float]:
    result = gemini_client.models.embed_content(model=model, contents=text)
    return list(result.embeddings[0].values)


def _use_gemini() -> bool:
    return bool(os.getenv("GEMINI_API_KEY")) and os.getenv("LLM_PROVIDER", "mock").lower() == "gemini"


def _embed(text: str) -> tuple[list[float], str]:
    if _use_gemini():
        from google import genai
        model = os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
        try:
            client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
            return _gemini_embed(text, client, model), model
        except Exception:
            pass

    return _local_embed(text), os.getenv("LOCAL_EMBEDDING_MODEL", LOCAL_EMBEDDING_MODEL)


def _write_faiss_index(index: Any, dest: Path) -> None:
    # faiss.write_index uses C fopen which fails on non-ASCII Windows paths.
    # serialize_index returns a numpy uint8 array; write it with Python open().
    data: np.ndarray = faiss.serialize_index(index)
    with open(dest, "wb") as f:
        f.write(data.tobytes())


def _read_faiss_index(src: Path) -> Any:
    with open(src, "rb") as f:
        buf = np.frombuffer(f.read(), dtype=np.uint8)
    return faiss.deserialize_index(buf)


class FaissVectorDB:
    """Persistent FAISS index plus id metadata.

    FAISS stores vectors only. PostgreSQL remains the source of truth for
    action-item metadata and evidence.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else vector_db_path()
        self.index_path = self.path / "index.faiss"
        self.meta_path = self.path / "ids.json"

    def count(self, meeting_id: str | None = None) -> int:
        metadata = self._load_metadata()
        if meeting_id:
            metadata = [row for row in metadata if row.get("meeting_id") == meeting_id]
        return len(metadata)

    def rebuild(self, records: list[dict[str, Any]]) -> int:
        self.path.mkdir(parents=True, exist_ok=True)
        metadata = []
        vectors = []
        for record in records:
            vectors.append(record["vector"])
            metadata.append(
                {
                    "action_item_id": record["action_item_id"],
                    "meeting_id": record["meeting_id"],
                    "model_name": record["model_name"],
                    "text_input": record["text_input"],
                }
            )

        if vectors:
            matrix = np.asarray(vectors, dtype="float32")
            index = faiss.IndexFlatIP(matrix.shape[1])
            index.add(matrix)
        else:
            index = faiss.IndexFlatIP(EMBEDDING_DIM)

        _write_faiss_index(index, self.index_path)
        self.meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return len(records)

    def search_ids(
        self,
        query_vector: list[float],
        top_k: int = 5,
        meeting_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self.index_path.exists() or not self.meta_path.exists():
            return []

        index = _read_faiss_index(self.index_path)
        metadata = self._load_metadata()
        if not metadata:
            return []

        # For meeting filters, search wider then filter ids. This keeps FAISS
        # vector-only while PostgreSQL owns metadata.
        search_k = min(index.ntotal, max(top_k * 10, top_k))
        query = np.asarray([query_vector], dtype="float32")
        scores, indices = index.search(query, search_k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if int(idx) < 0:
                continue
            row = metadata[int(idx)]
            if meeting_id and row.get("meeting_id") != meeting_id:
                continue
            results.append(
                {
                    "action_item_id": row["action_item_id"],
                    "meeting_id": row["meeting_id"],
                    "similarity": round(float(score), 4),
                }
            )
            if len(results) >= top_k:
                break
        return results

    def _load_metadata(self) -> list[dict[str, Any]]:
        if not self.meta_path.exists():
            return []
        return json.loads(self.meta_path.read_text(encoding="utf-8"))


def generate_and_store_embeddings(client: Any, meeting_id: str) -> int:
    del meeting_id
    action_items = client.fetch_all(
        """
        SELECT
            action_item_id,
            meeting_id,
            description,
            category,
            campaign_context,
            final_confidence
        FROM action_items
        WHERE final_confidence >= ?
        ORDER BY meeting_id, action_item_id
        """,
        (CONFIDENCE_THRESHOLD,),
    )

    records = []
    for item in action_items:
        text = build_embedding_text(item)
        vector, model_name = _embed(text)
        records.append(
            {
                "action_item_id": item["action_item_id"],
                "meeting_id": item["meeting_id"],
                "model_name": model_name,
                "text_input": text,
                "vector": vector,
            }
        )

    return FaissVectorDB().rebuild(records)


def vector_record_count(meeting_id: str | None = None) -> int:
    return FaissVectorDB().count(meeting_id)


def search_similar(
    client: Any,
    query_text: str,
    top_k: int = 5,
    meeting_id: str | None = None,
) -> list[dict[str, Any]]:
    query_vector, _ = _embed(query_text)
    id_hits = FaissVectorDB().search_ids(query_vector, top_k=top_k, meeting_id=meeting_id)
    if not id_hits:
        return []

    score_by_id = {row["action_item_id"]: row["similarity"] for row in id_hits}
    ordered_ids = [row["action_item_id"] for row in id_hits]
    is_postgres = client.__class__.__name__ == "PostgreSQLClient"
    placeholders = ", ".join("?" for _ in ordered_ids)
    evidence_expr = (
        "STRING_AGG('[' || u.sequence_no || '] ' || u.speaker_raw || ': ' || u.text, CHR(10) ORDER BY u.sequence_no)"
        if is_postgres
        else "GROUP_CONCAT('[' || u.sequence_no || '] ' || u.speaker_raw || ': ' || u.text, CHAR(10))"
    )
    group_by = (
        "a.action_item_id, a.meeting_id, a.description, a.assignee_normalized, "
        "a.category, a.priority, a.status, a.final_confidence, m.title"
        if is_postgres
        else "a.action_item_id, m.title"
    )
    rows = client.fetch_all(
        f"""
        SELECT
            a.action_item_id,
            a.meeting_id,
            a.description,
            a.assignee_normalized,
            a.category,
            a.priority,
            a.status,
            a.final_confidence,
            m.title AS meeting_title,
            {evidence_expr} AS evidence_summary
        FROM action_items a
        JOIN meetings m ON m.meeting_id = a.meeting_id
        LEFT JOIN action_item_sources s ON s.action_item_id = a.action_item_id
        LEFT JOIN utterances u ON u.utterance_id = s.utterance_id
        WHERE a.action_item_id IN ({placeholders})
        GROUP BY {group_by}
        """,
        tuple(ordered_ids),
    )

    row_by_id = {row["action_item_id"]: row for row in rows}
    results = []
    for action_item_id in ordered_ids:
        row = row_by_id.get(action_item_id)
        if not row:
            continue
        row["similarity"] = score_by_id[action_item_id]
        results.append(row)
    return results
