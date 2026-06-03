from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from analytics.embeddings import generate_and_store_embeddings
from extraction.summarizer import summarize_and_store
from db.pg_client import DEFAULT_DSN, PostgreSQLClient
from db.sqlite_client import DEFAULT_DB_PATH, SQLiteClient
from extraction.extractor import build_extractor
from ingestion.audio_loader import load_audio_metadata
from ingestion.diarization import PyannoteDiarizer, fallback_single_speaker
from ingestion.stt import FasterWhisperSTT
from ingestion.transcript_builder import (
    build_transcript,
    load_transcript_json,
    save_transcript_json,
)
from models import Chunk, Transcript, stable_hash, utc_now_iso
from preprocessing.chunker import build_chunks, chunk_utterance_rows
from preprocessing.normalizer import normalize_speakers, remove_consecutive_duplicates


DEFAULT_TRANSCRIPT_OUTPUT = Path("data/processed/transcript.json")

load_dotenv()


def run_pipeline(
    input_path: str | Path,
    db_path: str | Path = DEFAULT_DB_PATH,
    input_mode: str = "transcript",
    transcript_output: str | Path = DEFAULT_TRANSCRIPT_OUTPUT,
    db_backend: str = "sqlite",
    pg_dsn: str | None = None,
    num_speakers: int | None = None,
    title: str | None = None,
) -> None:
    client = build_db_client(db_backend=db_backend, db_path=db_path, pg_dsn=pg_dsn)
    client.init_schema()

    if input_mode == "audio":
        transcript = _build_transcript_from_audio(
            input_path, transcript_output, client,
            num_speakers=num_speakers, title=title,
        )
    elif input_mode == "transcript":
        transcript = load_transcript_json(input_path)
    else:
        raise ValueError(f"Unsupported input_mode: {input_mode}")

    transcript = remove_consecutive_duplicates(normalize_speakers(transcript))
    chunks = build_chunks(transcript)

    _store_transcript(client, transcript)
    _store_chunks(client, chunks)
    _extract_and_store(client, transcript.meeting.meeting_id, chunks)
    summarize_and_store(client, transcript.meeting.meeting_id, transcript)
    generate_and_store_embeddings(client, transcript.meeting.meeting_id)


def build_db_client(
    db_backend: str = "sqlite",
    db_path: str | Path = DEFAULT_DB_PATH,
    pg_dsn: str | None = None,
) -> object:
    if db_backend == "postgres":
        return PostgreSQLClient(dsn=pg_dsn or os.getenv("DATABASE_URL") or DEFAULT_DSN)
    return SQLiteClient(db_path)


def _build_transcript_from_audio(
    input_path: str | Path,
    transcript_output: str | Path,
    client: SQLiteClient,
    num_speakers: int | None = None,
    title: str | None = None,
) -> Transcript:
    audio_metadata = load_audio_metadata(input_path, title=title)
    stt = FasterWhisperSTT()
    stt_result = stt.transcribe(audio_metadata.audio_path)

    if stt_result.segments:
        diarization_end = max(segment.end_sec for segment in stt_result.segments)
    else:
        diarization_end = audio_metadata.duration_sec or 0.0

    try:
        diarization_result = PyannoteDiarizer(num_speakers=num_speakers).diarize(audio_metadata.audio_path)
        diarization_status = "completed"
        diarization_error = None
    except Exception as exc:
        diarization_result = fallback_single_speaker(0.0, diarization_end)
        diarization_status = "fallback"
        diarization_error = str(exc)

    transcript = build_transcript(audio_metadata, stt_result, diarization_result)
    saved_path = save_transcript_json(transcript, transcript_output)
    transcript.meeting.transcript_path = str(saved_path)

    client.upsert_meeting(
        {
            "meeting_id": transcript.meeting.meeting_id,
            "title": transcript.meeting.title,
            "meeting_date": None,
            "audio_path": transcript.meeting.audio_path,
            "audio_hash": transcript.meeting.audio_hash,
            "transcript_path": transcript.meeting.transcript_path,
            "source_type": transcript.meeting.source_type.value,
            "updated_at": utc_now_iso(),
        }
    )

    client.upsert(
        "stt_runs",
        {
            "stt_run_id": stable_hash("stt", transcript.meeting.meeting_id),
            "meeting_id": transcript.meeting.meeting_id,
            "audio_path": str(audio_metadata.audio_path),
            "audio_hash": audio_metadata.audio_hash,
            "stt_model": stt_result.model_name,
            "diarization_model": diarization_result.model_name,
            "language": stt_result.language or "ko",
            "duration_sec": audio_metadata.duration_sec,
            "segment_count": len(stt_result.segments),
            "speaker_count": diarization_result.speaker_count,
            "status": diarization_status,
            "error_message": diarization_error,
        },
        conflict_columns=["stt_run_id"],
        update_columns=[
            "audio_path",
            "audio_hash",
            "stt_model",
            "diarization_model",
            "language",
            "duration_sec",
            "segment_count",
            "speaker_count",
            "status",
            "error_message",
        ],
    )
    return transcript


def _store_transcript(client: SQLiteClient, transcript: Transcript) -> None:
    meeting = transcript.meeting
    client.upsert_meeting(
        {
            "meeting_id": meeting.meeting_id,
            "title": meeting.title,
            "meeting_date": meeting.meeting_date.isoformat()
            if meeting.meeting_date
            else None,
            "audio_path": meeting.audio_path,
            "audio_hash": meeting.audio_hash,
            "transcript_path": meeting.transcript_path,
            "source_type": meeting.source_type.value,
            "updated_at": utc_now_iso(),
        }
    )

    for participant in transcript.participants:
        client.upsert_participant(participant.model_dump())

    for utterance in transcript.utterances:
        row = utterance.model_dump()
        row["source"] = utterance.source.value
        client.upsert_utterance(row)


def _store_chunks(client: SQLiteClient, chunks: list[object]) -> None:
    for chunk in chunks:
        client.upsert_chunk(
            {
                "chunk_id": chunk.chunk_id,
                "meeting_id": chunk.meeting_id,
                "chunk_text": chunk.chunk_text,
                "topic_hint": chunk.topic_hint,
                "start_sequence_no": chunk.start_sequence_no,
                "end_sequence_no": chunk.end_sequence_no,
            }
        )

    client.upsert_many(
        "chunk_utterances",
        chunk_utterance_rows(chunks),
        conflict_columns=["chunk_id", "utterance_id"],
        update_columns=["sequence_no"],
    )


def _extract_and_store(client: SQLiteClient, meeting_id: str, chunks: list[object]) -> None:
    extractor = build_extractor()
    extraction_chunks = chunks
    if os.getenv("LLM_PROVIDER", "mock").lower() == "gemini":
        extraction_chunks = [_combine_chunks_for_extraction(meeting_id, chunks)]
        _store_chunks(client, extraction_chunks)

    for chunk in extraction_chunks:
        result = extractor.extract(chunk, meeting_id)
        run = result.run
        client.upsert(
            "extraction_runs",
            {
                "extraction_run_id": run.extraction_run_id,
                "meeting_id": run.meeting_id,
                "provider": run.provider.value,
                "model_name": run.model_name,
                "prompt_version": run.prompt_version,
                "mode": run.mode.value,
                "raw_request_json": run.raw_request_json,
                "raw_response_json": run.raw_response_json,
                "parsed_ok": int(run.parsed_ok),
                "retry_count": run.retry_count,
                "error_message": run.error_message,
            },
            conflict_columns=["extraction_run_id"],
            update_columns=[
                "provider",
                "model_name",
                "prompt_version",
                "mode",
                "raw_request_json",
                "raw_response_json",
                "parsed_ok",
                "retry_count",
                "error_message",
            ],
        )

        for action_item in result.action_items:
            client.upsert_action_item(action_item.to_db_row())
            persisted = client.fetch_one(
                """
                SELECT action_item_id
                FROM action_items
                WHERE meeting_id = ? AND dedup_key = ?
                """,
                (action_item.meeting_id, action_item.dedup_key),
            )
            persisted_action_item_id = (
                persisted["action_item_id"] if persisted else action_item.action_item_id
            )
            source_rows = [
                {
                    "action_item_id": persisted_action_item_id,
                    "utterance_id": utterance_id,
                    "evidence_text": None,
                    "relevance_score": 1.0,
                }
                for utterance_id in action_item.source_utterance_ids
            ]
            client.upsert_many(
                "action_item_sources",
                source_rows,
                conflict_columns=["action_item_id", "utterance_id"],
                update_columns=["evidence_text", "relevance_score"],
            )


def _combine_chunks_for_extraction(meeting_id: str, chunks: list[object]) -> Chunk:
    utterance_ids = []
    for chunk in chunks:
        utterance_ids.extend(chunk.utterance_ids)

    start_sequence_no = min(
        chunk.start_sequence_no
        for chunk in chunks
        if chunk.start_sequence_no is not None
    )
    end_sequence_no = max(
        chunk.end_sequence_no for chunk in chunks if chunk.end_sequence_no is not None
    )
    return Chunk(
        chunk_id=stable_hash(meeting_id, "meeting_level_extraction"),
        meeting_id=meeting_id,
        chunk_text="\n\n".join(chunk.chunk_text for chunk in chunks),
        utterance_ids=utterance_ids,
        topic_hint="meeting",
        start_sequence_no=start_sequence_no,
        end_sequence_no=end_sequence_no,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run meeting action item pipeline.")
    parser.add_argument("--input", required=True, help="Path to mp3/audio or transcript JSON")
    parser.add_argument(
        "--input-mode",
        choices=["audio", "transcript"],
        default="transcript",
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path")
    parser.add_argument(
        "--db-backend",
        choices=["sqlite", "postgres"],
        default=os.getenv("DB_BACKEND", "sqlite"),
        help="Operational DB backend",
    )
    parser.add_argument(
        "--pg-dsn",
        default=os.getenv("DATABASE_URL") or os.getenv("PGDSN"),
        help="PostgreSQL DSN when --db-backend=postgres",
    )
    parser.add_argument(
        "--transcript-output",
        default=str(DEFAULT_TRANSCRIPT_OUTPUT),
        help="Path for generated transcript JSON when input-mode=audio",
    )
    parser.add_argument(
        "--num-speakers",
        type=int,
        default=None,
        help="Number of speakers in the meeting (hint for diarization). Omit to use automatic detection.",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="Meeting title. Defaults to the audio filename stem.",
    )
    args = parser.parse_args()

    run_pipeline(
        input_path=args.input,
        db_path=args.db,
        input_mode=args.input_mode,
        transcript_output=args.transcript_output,
        db_backend=args.db_backend,
        pg_dsn=args.pg_dsn,
        num_speakers=args.num_speakers,
        title=args.title,
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "db": args.db,
                "input": args.input,
                "input_mode": args.input_mode,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
