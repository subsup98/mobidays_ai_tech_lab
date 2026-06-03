from __future__ import annotations

from collections import defaultdict

from models import Chunk, Transcript, stable_hash
from preprocessing.glossary import infer_topic_hint


def build_chunks(transcript: Transcript, max_utterances: int = 4) -> list[Chunk]:
    chunks: list[Chunk] = []
    current_utterances = []
    current_topic = None

    for utterance in transcript.utterances:
        topic = infer_topic_hint(utterance.text)
        should_flush = (
            current_utterances
            and (topic != current_topic or len(current_utterances) >= max_utterances)
        )
        if should_flush:
            chunks.append(_make_chunk(transcript.meeting.meeting_id, current_utterances))
            current_utterances = []

        current_utterances.append(utterance)
        current_topic = topic

    if current_utterances:
        chunks.append(_make_chunk(transcript.meeting.meeting_id, current_utterances))

    return chunks


def chunk_utterance_rows(chunks: list[Chunk]) -> list[dict[str, object]]:
    rows = []
    for chunk in chunks:
        for index, utterance_id in enumerate(chunk.utterance_ids, start=1):
            rows.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "utterance_id": utterance_id,
                    "sequence_no": index,
                }
            )
    return rows


def topic_counts(chunks: list[Chunk]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for chunk in chunks:
        counts[chunk.topic_hint or "general"] += 1
    return dict(counts)


def _make_chunk(meeting_id: str, utterances: list[object]) -> Chunk:
    start_sequence_no = utterances[0].sequence_no
    end_sequence_no = utterances[-1].sequence_no
    chunk_text = "\n".join(
        f"[{utterance.utterance_id}] "
        f"{utterance.speaker_normalized or utterance.speaker_raw}: {utterance.text}"
        for utterance in utterances
    )
    topic_hint = infer_topic_hint(chunk_text)
    return Chunk(
        chunk_id=stable_hash(meeting_id, start_sequence_no, end_sequence_no, topic_hint),
        meeting_id=meeting_id,
        chunk_text=chunk_text,
        utterance_ids=[utterance.utterance_id for utterance in utterances],
        topic_hint=topic_hint,
        start_sequence_no=start_sequence_no,
        end_sequence_no=end_sequence_no,
    )
