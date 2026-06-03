from __future__ import annotations

import argparse
import csv
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
import sys

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from ingestion.audio_loader import load_audio_metadata
from ingestion.diarization import PyannoteDiarizer, fallback_single_speaker
from ingestion.stt import FasterWhisperSTT
from ingestion.transcript_builder import build_transcript, load_transcript_json, save_transcript_json
from ingestion.transcript_compare import compare_transcripts


DEFAULT_AUDIO = Path("data/raw/ko_meeting_3speakers_4min_faster.mp3")
DEFAULT_REFERENCE = Path("data/raw/ko_meeting_3speakers.json")
DEFAULT_OUTPUT_DIR = Path("data/interim/model_comparison")


@dataclass(frozen=True)
class ExperimentResult:
    stt_model: str
    diarization_model: str
    num_speakers_hint: int | None
    status: str
    elapsed_sec: float
    generated_utterances: int
    reference_utterances: int
    generated_speakers: int
    reference_speakers: int
    keyword_recall: float
    missing_keywords: str
    transcript_path: str
    error_message: str | None = None


def run_experiments(
    audio_path: Path,
    reference_path: Path,
    output_dir: Path,
    stt_models: list[str],
    speaker_hints: list[int | None],
    diarization_model: str | None,
) -> list[ExperimentResult]:
    output_dir.mkdir(parents=True, exist_ok=True)
    reference = load_transcript_json(reference_path)
    audio_metadata = load_audio_metadata(audio_path)
    results: list[ExperimentResult] = []

    for stt_model in stt_models:
        start = time.perf_counter()
        stt = FasterWhisperSTT(model_name=stt_model)
        stt_result = stt.transcribe(audio_metadata.audio_path)
        stt_elapsed = time.perf_counter() - start

        diarization_end = (
            max(segment.end_sec for segment in stt_result.segments)
            if stt_result.segments
            else audio_metadata.duration_sec or 0.0
        )

        for speaker_hint in speaker_hints:
            run_start = time.perf_counter()
            status = "completed"
            error_message = None

            try:
                diarization_result = PyannoteDiarizer(
                    model_name=diarization_model,
                    num_speakers=speaker_hint,
                ).diarize(audio_metadata.audio_path)
            except Exception as exc:
                diarization_result = fallback_single_speaker(0.0, diarization_end)
                status = "fallback"
                error_message = str(exc)

            transcript = build_transcript(audio_metadata, stt_result, diarization_result)
            suffix = f"{stt_model}_speakers-{speaker_hint or 'auto'}"
            transcript_path = output_dir / f"transcript_{suffix}.json"
            save_transcript_json(transcript, transcript_path)

            comparison = compare_transcripts(transcript, reference)
            elapsed = round(stt_elapsed + (time.perf_counter() - run_start), 3)
            results.append(
                ExperimentResult(
                    stt_model=stt_model,
                    diarization_model=diarization_result.model_name,
                    num_speakers_hint=speaker_hint,
                    status=status,
                    elapsed_sec=elapsed,
                    generated_utterances=comparison.generated_utterances,
                    reference_utterances=comparison.reference_utterances,
                    generated_speakers=comparison.generated_speakers,
                    reference_speakers=comparison.reference_speakers,
                    keyword_recall=comparison.keyword_recall,
                    missing_keywords=", ".join(comparison.missing_keywords),
                    transcript_path=str(transcript_path),
                    error_message=error_message,
                )
            )

    return results


def write_results(results: list[ExperimentResult], output_dir: Path) -> None:
    rows = [asdict(result) for result in results]
    csv_path = output_dir / "summary.csv"
    json_path = output_dir / "summary.json"

    with csv_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    json_path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def parse_speaker_hints(value: str) -> list[int | None]:
    hints: list[int | None] = []
    for item in value.split(","):
        item = item.strip().lower()
        if not item or item == "auto":
            hints.append(None)
        else:
            hints.append(int(item))
    return hints


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Compare STT and diarization model settings.")
    parser.add_argument("--audio", default=str(DEFAULT_AUDIO))
    parser.add_argument("--reference", default=str(DEFAULT_REFERENCE))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--stt-models", default="small")
    parser.add_argument("--speaker-hints", default="auto,3")
    parser.add_argument("--diarization-model", default=None)
    args = parser.parse_args()

    results = run_experiments(
        audio_path=Path(args.audio),
        reference_path=Path(args.reference),
        output_dir=Path(args.output_dir),
        stt_models=[model.strip() for model in args.stt_models.split(",") if model.strip()],
        speaker_hints=parse_speaker_hints(args.speaker_hints),
        diarization_model=args.diarization_model,
    )
    write_results(results, Path(args.output_dir))

    print(json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
