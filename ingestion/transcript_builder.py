from __future__ import annotations

import json
from pathlib import Path

from ingestion.audio_loader import AudioMetadata
from ingestion.diarization import DiarizationResult, SpeakerSegment
from ingestion.stt import STTResult, STTSegment
from models import (
    Meeting,
    Participant,
    SourceType,
    Transcript,
    Utterance,
    UtteranceSource,
    stable_hash,
)


def build_transcript(
    audio_metadata: AudioMetadata,
    stt_result: STTResult,
    diarization_result: DiarizationResult | None = None,
) -> Transcript:
    meeting = audio_metadata.to_meeting()
    speaker_segments = diarization_result.segments if diarization_result else []
    utterances = [
        _build_utterance(
            meeting=meeting,
            stt_segment=segment,
            speaker_raw=_speaker_for_segment(segment, speaker_segments),
        )
        for segment in stt_result.segments
    ]
    participants = _build_participants(meeting.meeting_id, utterances)
    participant_by_speaker = {
        participant.speaker_raw: participant.participant_id
        for participant in participants
    }

    enriched_utterances = [
        utterance.model_copy(
            update={"participant_id": participant_by_speaker.get(utterance.speaker_raw)}
        )
        for utterance in utterances
    ]
    return Transcript(
        meeting=meeting,
        participants=participants,
        utterances=enriched_utterances,
    )


def save_transcript_json(transcript: Transcript, output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        transcript.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return path


def load_transcript_json(path: str | Path) -> Transcript:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if "meeting" not in data and "segments" in data:
        return _load_provided_transcript(data, path)
    return Transcript.model_validate(data)


def _build_utterance(
    meeting: Meeting,
    stt_segment: STTSegment,
    speaker_raw: str,
) -> Utterance:
    return Utterance(
        utterance_id=stable_hash(
            meeting.meeting_id,
            stt_segment.sequence_no,
            stt_segment.start_sec,
            stt_segment.end_sec,
        ),
        meeting_id=meeting.meeting_id,
        speaker_raw=speaker_raw,
        speaker_normalized=speaker_raw.lower(),
        text=stt_segment.text,
        start_sec=stt_segment.start_sec,
        end_sec=stt_segment.end_sec,
        sequence_no=stt_segment.sequence_no,
    )


def _build_participants(
    meeting_id: str,
    utterances: list[Utterance],
) -> list[Participant]:
    speakers = sorted({utterance.speaker_raw for utterance in utterances})
    return [
        Participant(
            participant_id=stable_hash(meeting_id, speaker),
            meeting_id=meeting_id,
            speaker_raw=speaker,
            speaker_normalized=speaker.lower(),
            role=None,
            confidence=0.0,
        )
        for speaker in speakers
    ]


def _speaker_for_segment(
    stt_segment: STTSegment,
    speaker_segments: list[SpeakerSegment],
) -> str:
    if not speaker_segments:
        return "SPEAKER_00"

    best_speaker = "SPEAKER_00"
    best_overlap = 0.0

    for speaker_segment in speaker_segments:
        overlap = _overlap_seconds(
            stt_segment.start_sec,
            stt_segment.end_sec,
            speaker_segment.start_sec,
            speaker_segment.end_sec,
        )
        if overlap > best_overlap:
            best_overlap = overlap
            best_speaker = speaker_segment.speaker

    return best_speaker


def _overlap_seconds(
    start_a: float,
    end_a: float,
    start_b: float,
    end_b: float,
) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def _load_provided_transcript(data: dict[str, object], path: Path) -> Transcript:
    meeting_id = stable_hash("provided", path.stem)
    meeting = Meeting(
        meeting_id=meeting_id,
        title=path.stem,
        transcript_path=str(path),
        source_type=SourceType.TRANSCRIPT,
    )

    speakers = data.get("speakers") or []
    participants = []
    participant_by_name = {}
    for speaker in speakers:
        if not isinstance(speaker, dict):
            continue
        name = str(speaker.get("name") or "unknown")
        role = speaker.get("role")
        participant = Participant(
            participant_id=stable_hash(meeting_id, name),
            meeting_id=meeting_id,
            speaker_raw=name,
            speaker_normalized=_normalize_role_or_name(role, name),
            role=str(role) if role else None,
            confidence=1.0,
        )
        participants.append(participant)
        participant_by_name[name] = participant

    utterances = []
    for index, segment in enumerate(data.get("segments") or [], start=1):
        if not isinstance(segment, dict):
            continue
        speaker_raw = str(segment.get("speaker") or "unknown")
        participant = participant_by_name.get(speaker_raw)
        role = segment.get("role")
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        sequence_no = int(segment.get("line_no") or segment.get("id") or index)
        utterances.append(
            Utterance(
                utterance_id=stable_hash(meeting_id, sequence_no, speaker_raw, text),
                meeting_id=meeting_id,
                participant_id=participant.participant_id if participant else None,
                speaker_raw=speaker_raw,
                speaker_normalized=_normalize_role_or_name(role, speaker_raw),
                text=text,
                start_sec=None,
                end_sec=None,
                sequence_no=sequence_no,
                source=UtteranceSource.PROVIDED_TRANSCRIPT,
            )
        )

    if not participants:
        speaker_names = sorted({utterance.speaker_raw for utterance in utterances})
        participants = [
            Participant(
                participant_id=stable_hash(meeting_id, name),
                meeting_id=meeting_id,
                speaker_raw=name,
                speaker_normalized=name,
                role=None,
                confidence=0.5,
            )
            for name in speaker_names
        ]

    return Transcript(meeting=meeting, participants=participants, utterances=utterances)


def _normalize_role_or_name(role: object, name: str) -> str:
    role_text = str(role or "")
    if "팀장" in role_text:
        return "team_lead"
    if "퍼포먼스" in role_text or "마케터" in role_text:
        return "performance_marketer"
    if "디자이너" in role_text or "콘텐츠" in role_text:
        return "content_designer"
    return name.lower()
