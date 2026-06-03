from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from ingestion.audio_loader import load_audio_metadata
from ingestion.diarization import DiarizationResult, SpeakerSegment
from ingestion.stt import STTResult, STTSegment
from ingestion.transcript_builder import build_transcript, load_transcript_json, save_transcript_json


def load_community_segments(path: Path) -> DiarizationResult:
    rows = json.loads(path.read_text(encoding="utf-8"))
    segments = [
        SpeakerSegment(
            start_sec=float(row["start_sec"]),
            end_sec=float(row["end_sec"]),
            speaker=f"COMMUNITY1_{str(row['speaker']).rsplit('_', 1)[-1].zfill(2)}",
        )
        for row in rows
    ]
    segments.sort(key=lambda segment: (segment.start_sec, segment.end_sec))
    return DiarizationResult(
        segments=segments,
        model_name="pyannote/speaker-diarization-community-1",
    )


def load_stt_from_transcript(path: Path) -> STTResult:
    transcript = load_transcript_json(path)
    return STTResult(
        segments=[
            STTSegment(
                start_sec=float(utterance.start_sec or 0.0),
                end_sec=float(utterance.end_sec or 0.0),
                text=utterance.text,
                sequence_no=utterance.sequence_no,
            )
            for utterance in transcript.utterances
        ],
        language="ko",
        language_probability=None,
        model_name="large-v3",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build transcript from large-v3 STT and pyannote community-1 diarization.")
    parser.add_argument("--audio", default="data/raw/ko_meeting_3speakers_4min_faster.mp3")
    parser.add_argument("--stt-transcript", default="data/interim/model_comparison_large_auto/transcript_large-v3_speakers-auto.json")
    parser.add_argument("--community-segments", default="data/interim/pyannote4_community/community1_segments.json")
    parser.add_argument("--output", default="data/interim/pyannote4_community/transcript_large-v3_community1.json")
    args = parser.parse_args()

    audio_metadata = load_audio_metadata(args.audio)
    stt_result = load_stt_from_transcript(Path(args.stt_transcript))
    diarization_result = load_community_segments(Path(args.community_segments))
    transcript = build_transcript(audio_metadata, stt_result, diarization_result)
    save_transcript_json(transcript, args.output)
    print(args.output)


if __name__ == "__main__":
    main()
