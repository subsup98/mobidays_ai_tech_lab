from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Mapping, Sequence

try:
    import psycopg2
    import psycopg2.extras
    _PSYCOPG2_AVAILABLE = True
except ImportError:
    _PSYCOPG2_AVAILABLE = False

from db.base_client import BaseDBClient

# Adapted from schema.sql:
#   - replaced datetime('now') with CURRENT_TIMESTAMP
#   - replaced TEXT with VARCHAR (both work in PG, VARCHAR is conventional)
#   - INTEGER CHECK (x IN (0,1)) kept as-is (BOOLEAN is idiomatic in PG but this keeps schema parity)
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
    duration_sec DOUBLE PRECISION,
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
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0
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
    start_sec DOUBLE PRECISION,
    end_sec DOUBLE PRECISION,
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
    llm_confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0
        CHECK (llm_confidence >= 0.0 AND llm_confidence <= 1.0),
    validation_score DOUBLE PRECISION NOT NULL DEFAULT 0.0
        CHECK (validation_score >= 0.0 AND validation_score <= 1.0),
    final_confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0
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
    relevance_score DOUBLE PRECISION NOT NULL DEFAULT 1.0
        CHECK (relevance_score >= 0.0 AND relevance_score <= 1.0),
    PRIMARY KEY (action_item_id, utterance_id)
);

CREATE TABLE IF NOT EXISTS issue_keywords (
    issue_keyword_id VARCHAR PRIMARY KEY,
    meeting_id VARCHAR NOT NULL,
    keyword VARCHAR NOT NULL,
    keyword_type VARCHAR NOT NULL DEFAULT 'bigram'
        CHECK (keyword_type IN ('domain', 'bigram', 'risk_flag')),
    score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
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

DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5432/mobidays_bench"


class PostgreSQLClient(BaseDBClient):
    """PostgreSQL backend using psycopg2. Requires a running PostgreSQL server.

    Set dsn to a libpq connection string or use environment variable PGDSN.
    Example: postgresql://user:password@localhost:5432/dbname
    """

    placeholder = "%s"

    def __init__(self, dsn: str = DEFAULT_DSN, connect_timeout: int = 3) -> None:
        if not _PSYCOPG2_AVAILABLE:
            raise ImportError("psycopg2 is not installed. Run: pip install psycopg2-binary")
        self.dsn = dsn
        self.connect_timeout = connect_timeout
        self._conn: Any = None

    def _get_conn(self) -> Any:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(
                self.dsn,
                connect_timeout=self.connect_timeout,
            )
            self._conn.autocommit = False
            return self._conn
        # 이전 트랜잭션이 실패 상태면 롤백해서 커넥션을 재사용 가능 상태로 복구
        try:
            tx_status = self._conn.get_transaction_status()
            if tx_status == psycopg2.extensions.TRANSACTION_STATUS_INERROR:
                self._conn.rollback()
        except Exception:
            self.close()
            return self._get_conn()
        return self._conn

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()
        self._conn = None

    def init_schema(self) -> None:
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                for statement in _SCHEMA_SQL.split(";"):
                    stmt = statement.strip()
                    if stmt:
                        cur.execute(stmt)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        conn = self._get_conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def fetch_all(self, query: str, params: Any = ()) -> list[dict[str, Any]]:
        conn = self._get_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(self._adapt_query(query), list(params) if params else None)
                return [dict(row) for row in cur.fetchall()]
        except Exception:
            try:
                conn.rollback()
            except Exception:
                self.close()
            raise

    def fetch_one(self, query: str, params: Any = ()) -> dict[str, Any] | None:
        conn = self._get_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(self._adapt_query(query), list(params) if params else None)
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception:
            try:
                conn.rollback()
            except Exception:
                self.close()
            raise

    def execute(self, query: str, params: Any = ()) -> None:
        with self.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(self._adapt_query(query), list(params) if params else None)

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
        placeholders = ", ".join("%s" for _ in columns)
        col_sql = ", ".join(columns)
        conflict_sql = ", ".join(conflict_columns)

        if update_cols:
            update_sql = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
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
        conn = connection if connection is not None else self._get_conn()
        with conn.cursor() as cur:
            cur.execute(sql, params_list)
        if connection is None:
            conn.commit()

    def upsert_many(
        self,
        table: str,
        rows: Sequence[Mapping[str, Any]],
        conflict_columns: Sequence[str],
        update_columns: Sequence[str] | None = None,
    ) -> None:
        if not rows:
            return
        super().upsert_many(table, rows, conflict_columns, update_columns)

    def upsert_meeting(self, values: Mapping[str, Any]) -> None:
        self.upsert(
            "meetings",
            values,
            conflict_columns=["meeting_id"],
            update_columns=[
                "title",
                "meeting_date",
                "audio_path",
                "audio_hash",
                "transcript_path",
                "source_type",
                "updated_at",
            ],
        )

    def upsert_participant(self, values: Mapping[str, Any]) -> None:
        self.upsert(
            "participants",
            values,
            conflict_columns=["meeting_id", "speaker_raw"],
            update_columns=["speaker_normalized", "role", "confidence"],
        )

    def upsert_utterance(self, values: Mapping[str, Any]) -> None:
        self.upsert(
            "utterances",
            values,
            conflict_columns=["meeting_id", "sequence_no"],
            update_columns=[
                "participant_id",
                "speaker_raw",
                "speaker_normalized",
                "text",
                "start_sec",
                "end_sec",
                "source",
            ],
        )

    def upsert_chunk(self, values: Mapping[str, Any]) -> None:
        self.upsert(
            "chunks",
            values,
            conflict_columns=["chunk_id"],
            update_columns=[
                "meeting_id",
                "chunk_text",
                "topic_hint",
                "start_sequence_no",
                "end_sequence_no",
            ],
        )

    def upsert_action_item(self, values: Mapping[str, Any]) -> None:
        self.upsert(
            "action_items",
            values,
            conflict_columns=["meeting_id", "dedup_key"],
            update_columns=[
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
                "llm_confidence",
                "validation_score",
                "final_confidence",
                "review_required",
                "risk_flags_json",
                "campaign_context",
                "advertiser_context",
                "updated_at",
            ],
        )

    def update_action_status(
        self,
        action_item_id: str,
        new_status: str,
        event_id: str,
        changed_by: str = "dashboard",
        note: str | None = None,
    ) -> None:
        with self.transaction() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT status FROM action_items WHERE action_item_id = %s",
                    (action_item_id,),
                )
                row = cur.fetchone()
                if row is None:
                    raise ValueError(f"Unknown action_item_id: {action_item_id}")

                old_status = row["status"]
                cur.execute(
                    """
                    UPDATE action_items
                    SET status = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE action_item_id = %s
                    """,
                    (new_status, action_item_id),
                )
                cur.execute(
                    """
                    INSERT INTO action_item_events (
                        event_id,
                        action_item_id,
                        old_status,
                        new_status,
                        changed_by,
                        note
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (event_id) DO NOTHING
                    """,
                    (event_id, action_item_id, old_status, new_status, changed_by, note),
                )

    def list_action_items(self, meeting_id: str | None = None) -> list[dict[str, Any]]:
        if meeting_id:
            return self.fetch_all(
                """
                SELECT a.*, c.start_sequence_no
                FROM action_items a
                LEFT JOIN chunks c ON c.chunk_id = a.chunk_id
                WHERE a.meeting_id = ?
                ORDER BY COALESCE(c.start_sequence_no, a.sequence_no), a.sequence_no, a.action_item_id
                """,
                (meeting_id,),
            )
        return self.fetch_all(
            """
            SELECT a.*, c.start_sequence_no
            FROM action_items a
            LEFT JOIN chunks c ON c.chunk_id = a.chunk_id
            ORDER BY a.created_at DESC
            """
        )

    @staticmethod
    def _adapt_query(query: str) -> str:
        return query.replace("?", "%s")
