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


def load_sortformer_segments(path: Path) -> DiarizationResult:
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_segments = data[0] if data and isinstance(data[0], list) else data
    segments = []
    for item in raw_segments:
        start, end, speaker = str(item).split()
        speaker_index = speaker.rsplit("_", 1)[-1]
        segments.append(
            SpeakerSegment(
                start_sec=round(float(start), 3),
                end_sec=round(float(end), 3),
                speaker=f"SORTFORMER_{int(speaker_index):02d}",
            )
        )
    segments.sort(key=lambda segment: (segment.start_sec, segment.end_sec))
    return DiarizationResult(
        segments=segments,
        model_name="nvidia/diar_sortformer_4spk-v1",
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
    parser = argparse.ArgumentParser(description="Build transcript from large-v3 STT and NeMo Sortformer diarization.")
    parser.add_argument("--audio", default="data/raw/ko_meeting_3speakers_4min_faster.mp3")
    parser.add_argument("--stt-transcript", default="data/interim/model_comparison_large_auto/transcript_large-v3_speakers-auto.json")
    parser.add_argument("--sortformer-segments", default="data/interim/nemo_sortformer/sortformer_raw_segments.json")
    parser.add_argument("--output", default="data/interim/nemo_sortformer/transcript_large-v3_sortformer.json")
    args = parser.parse_args()

    audio_metadata = load_audio_metadata(args.audio)
    stt_result = load_stt_from_transcript(Path(args.stt_transcript))
    diarization_result = load_sortformer_segments(Path(args.sortformer_segments))
    transcript = build_transcript(audio_metadata, stt_result, diarization_result)
    save_transcript_json(transcript, args.output)
    print(args.output)


if __name__ == "__main__":
    main()
