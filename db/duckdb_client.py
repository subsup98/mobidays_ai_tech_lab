from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import duckdb

from db.base_client import BaseDBClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "app_duckdb.db"

# Adapted from schema.sql:
#   - removed PRAGMA (DuckDB doesn't use it)
#   - replaced datetime('now') with CURRENT_TIMESTAMP
#   - kept CHECK constraints and ON CONFLICT syntax (DuckDB 1.x compatible)
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meetings (
    meeting_id VARCHAR PRIMARY KEY,
    title VARCHAR NOT NULL,
    meeting_date VARCHAR,
    audio_path VARCHAR,
    audio_hash VARCHAR,
    transcript_path VARCHAR,
    source_type VARCHAR NOT NULL DEFAULT 'audio'
        CHECK (source_type IN ('audio', 'transcript', 'mock')),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS stt_runs (
    stt_run_id VARCHAR PRIMARY KEY,
    meeting_id VARCHAR NOT NULL,
    audio_path VARCHAR NOT NULL,
    audio_hash VARCHAR,
    stt_model VARCHAR NOT NULL,
    diarization_model VARCHAR,
    language VARCHAR DEFAULT 'ko',
    duration_sec DOUBLE,
    segment_count INTEGER NOT NULL DEFAULT 0,
    speaker_count INTEGER NOT NULL DEFAULT 0,
    status VARCHAR NOT NULL DEFAULT 'completed'
        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'fallback')),
    error_message VARCHAR,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS participants (
    participant_id VARCHAR PRIMARY KEY,
    meeting_id VARCHAR NOT NULL,
    speaker_raw VARCHAR NOT NULL,
    speaker_normalized VARCHAR,
    role VARCHAR,
    confidence DOUBLE NOT NULL DEFAULT 0.0
        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (meeting_id, speaker_raw)
);

CREATE TABLE IF NOT EXISTS utterances (
    utterance_id VARCHAR PRIMARY KEY,
    meeting_id VARCHAR NOT NULL,
    participant_id VARCHAR,
    speaker_raw VARCHAR NOT NULL,
    speaker_normalized VARCHAR,
    text VARCHAR NOT NULL,
    start_sec DOUBLE,
    end_sec DOUBLE,
    sequence_no INTEGER NOT NULL,
    source VARCHAR NOT NULL DEFAULT 'stt'
        CHECK (source IN ('stt', 'provided_transcript', 'mock')),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (meeting_id, sequence_no)
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id VARCHAR PRIMARY KEY,
    meeting_id VARCHAR NOT NULL,
    chunk_text VARCHAR NOT NULL,
    topic_hint VARCHAR,
    start_sequence_no INTEGER,
    end_sequence_no INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chunk_utterances (
    chunk_id VARCHAR NOT NULL,
    utterance_id VARCHAR NOT NULL,
    sequence_no INTEGER NOT NULL,
    PRIMARY KEY (chunk_id, utterance_id)
);

CREATE TABLE IF NOT EXISTS extraction_runs (
    extraction_run_id VARCHAR PRIMARY KEY,
    meeting_id VARCHAR NOT NULL,
    provider VARCHAR NOT NULL DEFAULT 'mock'
        CHECK (provider IN ('gemini', 'mock')),
    model_name VARCHAR NOT NULL,
    prompt_version VARCHAR NOT NULL DEFAULT 'v1',
    mode VARCHAR NOT NULL DEFAULT 'mock'
        CHECK (mode IN ('real', 'mock', 'fallback')),
    raw_request_json VARCHAR,
    raw_response_json VARCHAR,
    parsed_ok INTEGER NOT NULL DEFAULT 0
        CHECK (parsed_ok IN (0, 1)),
    retry_count INTEGER NOT NULL DEFAULT 0,
    error_message VARCHAR,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS action_items (
    action_item_id VARCHAR PRIMARY KEY,
    dedup_key VARCHAR NOT NULL,
    meeting_id VARCHAR NOT NULL,
    chunk_id VARCHAR,
    extraction_run_id VARCHAR,
    sequence_no INTEGER NOT NULL,
    assignee VARCHAR NOT NULL DEFAULT 'unassigned',
    assignee_normalized VARCHAR NOT NULL DEFAULT 'unassigned',
    description VARCHAR NOT NULL,
    normalized_task_signature VARCHAR NOT NULL,
    category VARCHAR NOT NULL DEFAULT 'uncategorized',
    due_date VARCHAR,
    priority VARCHAR NOT NULL DEFAULT 'medium'
        CHECK (priority IN ('low', 'medium', 'high')),
    status VARCHAR NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'in_progress', 'done', 'blocked')),
    llm_confidence DOUBLE NOT NULL DEFAULT 0.0
        CHECK (llm_confidence >= 0.0 AND llm_confidence <= 1.0),
    validation_score DOUBLE NOT NULL DEFAULT 0.0
        CHECK (validation_score >= 0.0 AND validation_score <= 1.0),
    final_confidence DOUBLE NOT NULL DEFAULT 0.0
        CHECK (final_confidence >= 0.0 AND final_confidence <= 1.0),
    review_required INTEGER NOT NULL DEFAULT 0
        CHECK (review_required IN (0, 1)),
    risk_flags_json VARCHAR NOT NULL DEFAULT '[]',
    campaign_context VARCHAR,
    advertiser_context VARCHAR,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (meeting_id, dedup_key)
);

CREATE TABLE IF NOT EXISTS action_item_sources (
    action_item_id VARCHAR NOT NULL,
    utterance_id VARCHAR NOT NULL,
    evidence_text VARCHAR,
    relevance_score DOUBLE NOT NULL DEFAULT 1.0
        CHECK (relevance_score >= 0.0 AND relevance_score <= 1.0),
    PRIMARY KEY (action_item_id, utterance_id)
);

CREATE TABLE IF NOT EXISTS issue_keywords (
    issue_keyword_id VARCHAR PRIMARY KEY,
    meeting_id VARCHAR NOT NULL,
    keyword VARCHAR NOT NULL,
    keyword_type VARCHAR NOT NULL DEFAULT 'bigram'
        CHECK (keyword_type IN ('domain', 'bigram', 'risk_flag')),
    score DOUBLE NOT NULL DEFAULT 0.0,
    frequency INTEGER NOT NULL DEFAULT 0,
    source_action_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (meeting_id, keyword, keyword_type)
);

CREATE TABLE IF NOT EXISTS action_item_events (
    event_id VARCHAR PRIMARY KEY,
    action_item_id VARCHAR NOT NULL,
    old_status VARCHAR,
    new_status VARCHAR NOT NULL
        CHECK (new_status IN ('open', 'in_progress', 'done', 'blocked')),
    changed_by VARCHAR NOT NULL DEFAULT 'dashboard',
    note VARCHAR,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS slack_payloads (
    payload_id VARCHAR PRIMARY KEY,
    action_item_id VARCHAR NOT NULL,
    payload_json VARCHAR NOT NULL,
    sent_mock INTEGER NOT NULL DEFAULT 0
        CHECK (sent_mock IN (0, 1)),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_stt_runs_meeting_id ON stt_runs(meeting_id);
CREATE INDEX IF NOT EXISTS idx_participants_meeting_id ON participants(meeting_id);
CREATE INDEX IF NOT EXISTS idx_utterances_meeting_sequence ON utterances(meeting_id, sequence_no);
CREATE INDEX IF NOT EXISTS idx_chunks_meeting_id ON chunks(meeting_id);
CREATE INDEX IF NOT EXISTS idx_extraction_runs_meeting_id ON extraction_runs(meeting_id);
CREATE INDEX IF NOT EXISTS idx_action_items_meeting_status ON action_items(meeting_id, status);
CREATE INDEX IF NOT EXISTS idx_action_items_assignee_status ON action_items(assignee_normalized, status);
CREATE INDEX IF NOT EXISTS idx_action_items_review_required ON action_items(review_required, final_confidence);
CREATE INDEX IF NOT EXISTS idx_action_item_sources_utterance ON action_item_sources(utterance_id);
CREATE INDEX IF NOT EXISTS idx_issue_keywords_meeting_score ON issue_keywords(meeting_id, score DESC);
CREATE INDEX IF NOT EXISTS idx_action_item_events_action ON action_item_events(action_item_id, created_at);
CREATE INDEX IF NOT EXISTS idx_slack_payloads_action ON slack_payloads(action_item_id);
"""


class DuckDBClient(BaseDBClient):
    """DuckDB backend. Uses a persistent file connection (or :memory: for tests)."""

    placeholder = "?"

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = ":memory:" if str(db_path) == ":memory:" else str(Path(db_path))
        self._conn: duckdb.DuckDBPyConnection | None = None

    def _get_conn(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            if self.db_path != ":memory:":
                Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = duckdb.connect(self.db_path)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def init_schema(self) -> None:
        conn = self._get_conn()
        for statement in _SCHEMA_SQL.split(";"):
            stmt = statement.strip()
            if stmt:
                conn.execute(stmt)

    @contextmanager
    def transaction(self) -> Iterator[duckdb.DuckDBPyConnection]:
        conn = self._get_conn()
        conn.begin()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def fetch_all(self, query: str, params: Any = ()) -> list[dict[str, Any]]:
        conn = self._get_conn()
        result = conn.execute(query, list(params) if params else [])
        columns = [desc[0] for desc in result.description]
        return [dict(zip(columns, row)) for row in result.fetchall()]

    def fetch_one(self, query: str, params: Any = ()) -> dict[str, Any] | None:
        conn = self._get_conn()
        result = conn.execute(query, list(params) if params else [])
        columns = [desc[0] for desc in result.description]
        row = result.fetchone()
        return dict(zip(columns, row)) if row else None

    def execute(self, query: str, params: Any = ()) -> None:
        with self.transaction() as conn:
            conn.execute(query, list(params) if params else [])

    def upsert(
        self,
        table: str,
        values: Mapping[str, Any],
        conflict_columns: Sequence[str],
        update_columns: Sequence[str] | None = None,
        connection: Any = None,
    ) -> None:
        if not values:
            raise ValueError("upsert values cannot be empty")
        columns = list(values.keys())
        update_cols = [c for c in (update_columns or columns) if c not in conflict_columns]
        placeholders = ", ".join("?" for _ in columns)
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

        params_list = list(values.values())
        if connection is not None:
            connection.execute(sql, params_list)
        else:
            with self.transaction() as conn:
                conn.execute(sql, params_list)
