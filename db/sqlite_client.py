from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "app_quality.db"
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class SQLiteClient:
    """Small SQLite wrapper for schema setup and idempotent writes."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def init_schema(self) -> None:
        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
        with self.transaction() as connection:
            connection.executescript(schema_sql)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def fetch_all(
        self,
        query: str,
        params: Sequence[Any] | Mapping[str, Any] = (),
    ) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def fetch_one(
        self,
        query: str,
        params: Sequence[Any] | Mapping[str, Any] = (),
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(query, params).fetchone()
        return dict(row) if row else None

    def execute(
        self,
        query: str,
        params: Sequence[Any] | Mapping[str, Any] = (),
    ) -> None:
        with self.transaction() as connection:
            connection.execute(query, params)

    def upsert(
        self,
        table: str,
        values: Mapping[str, Any],
        conflict_columns: Sequence[str],
        update_columns: Sequence[str] | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        if not values:
            raise ValueError("upsert values cannot be empty")
        if not conflict_columns:
            raise ValueError("conflict_columns cannot be empty")

        columns = list(values.keys())
        update_columns = list(update_columns or columns)
        update_columns = [col for col in update_columns if col not in conflict_columns]

        placeholders = ", ".join(f":{column}" for column in columns)
        column_sql = ", ".join(columns)
        conflict_sql = ", ".join(conflict_columns)

        if update_columns:
            update_sql = ", ".join(
                f"{column} = excluded.{column}" for column in update_columns
            )
            sql = (
                f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders}) "
                f"ON CONFLICT ({conflict_sql}) DO UPDATE SET {update_sql}"
            )
        else:
            sql = (
                f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders}) "
                f"ON CONFLICT ({conflict_sql}) DO NOTHING"
            )

        if connection is not None:
            connection.execute(sql, dict(values))
            return

        with self.transaction() as owned_connection:
            owned_connection.execute(sql, dict(values))

    def upsert_many(
        self,
        table: str,
        rows: Iterable[Mapping[str, Any]],
        conflict_columns: Sequence[str],
        update_columns: Sequence[str] | None = None,
    ) -> None:
        with self.transaction() as connection:
            for row in rows:
                self.upsert(
                    table=table,
                    values=row,
                    conflict_columns=conflict_columns,
                    update_columns=update_columns,
                    connection=connection,
                )

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
        prepared = dict(values)
        if isinstance(prepared.get("risk_flags_json"), (list, tuple)):
            prepared["risk_flags_json"] = json.dumps(
                prepared["risk_flags_json"], ensure_ascii=False
            )

        self.upsert(
            "action_items",
            prepared,
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

    def insert_action_item_event(
        self,
        event_id: str,
        action_item_id: str,
        new_status: str,
        old_status: str | None = None,
        changed_by: str = "dashboard",
        note: str | None = None,
    ) -> None:
        self.upsert(
            "action_item_events",
            {
                "event_id": event_id,
                "action_item_id": action_item_id,
                "old_status": old_status,
                "new_status": new_status,
                "changed_by": changed_by,
                "note": note,
            },
            conflict_columns=["event_id"],
            update_columns=[],
        )

    def update_action_status(
        self,
        action_item_id: str,
        new_status: str,
        event_id: str,
        changed_by: str = "dashboard",
        note: str | None = None,
    ) -> None:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT status FROM action_items WHERE action_item_id = ?",
                (action_item_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown action_item_id: {action_item_id}")

            old_status = row["status"]
            connection.execute(
                """
                UPDATE action_items
                SET status = ?, updated_at = datetime('now')
                WHERE action_item_id = ?
                """,
                (new_status, action_item_id),
            )
            connection.execute(
                """
                INSERT INTO action_item_events (
                    event_id,
                    action_item_id,
                    old_status,
                    new_status,
                    changed_by,
                    note
                )
                VALUES (?, ?, ?, ?, ?, ?)
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

    def list_tables(self) -> list[str]:
        rows = self.fetch_all(
            "SELECT name FROM sqlite_master WHERE type = ? ORDER BY name",
            ("table",),
        )
        return [row["name"] for row in rows]
