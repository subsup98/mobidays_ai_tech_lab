"""
기존 STT transcript JSON을 재사용하여 diarization만 num_speakers=3 힌트로 재실행합니다.
STT를 다시 돌리지 않으므로 시간이 크게 절약됩니다.
"""
from __future__ import annotations

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
from ingestion.stt import STTResult, STTSegment
from ingestion.transcript_builder import (
    build_transcript,
    load_transcript_json,
    save_transcript_json,
)
from ingestion.transcript_compare import compare_transcripts


DEFAULT_AUDIO = Path("data/raw/ko_meeting_3speakers_4min_faster.mp3")
DEFAULT_REFERENCE = Path("data/raw/ko_meeting_3speakers.json")
DEFAULT_OUTPUT_DIR = Path("data/interim/model_comparison_3speakers")

# 기존 auto-speaker 실험의 transcript JSON 파일 목록
EXISTING_TRANSCRIPTS = {
    "base": Path("data/interim/model_comparison_auto/transcript_base_speakers-auto.json"),
    "small": Path("data/interim/model_comparison_auto/transcript_small_speakers-auto.json"),
    "medium": Path("data/interim/model_comparison_auto/transcript_medium_speakers-auto.json"),
    "large-v3": Path("data/interim/model_comparison_large_auto/transcript_large-v3_speakers-auto.json"),
}


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


def reconstruct_stt_result(transcript_path: Path, model_name: str) -> STTResult:
    """기존 transcript JSON의 utterances에서 STTResult를 재구성합니다."""
    transcript = load_transcript_json(transcript_path)
    segments = [
        STTSegment(
            start_sec=u.start_sec or 0.0,
            end_sec=u.end_sec or 0.0,
            text=u.text,
            sequence_no=u.sequence_no,
        )
        for u in transcript.utterances
        if u.text.strip()
    ]
    return STTResult(
        segments=segments,
        language="ko",
        language_probability=None,
        model_name=model_name,
    )


def run_experiments(
    audio_path: Path,
    reference_path: Path,
    output_dir: Path,
    stt_models: list[str],
    num_speakers: int,
    diarization_model: str | None,
) -> list[ExperimentResult]:
    output_dir.mkdir(parents=True, exist_ok=True)
    reference = load_transcript_json(reference_path)
    audio_metadata = load_audio_metadata(audio_path)
    results: list[ExperimentResult] = []

    for stt_model in stt_models:
        existing_path = EXISTING_TRANSCRIPTS.get(stt_model)
        if not existing_path or not existing_path.exists():
            print(f"[skip] {stt_model}: 기존 transcript 없음 ({existing_path})")
            continue

        print(f"\n[{stt_model}] 기존 STT transcript 로드 중: {existing_path}")
        stt_result = reconstruct_stt_result(existing_path, stt_model)
        print(f"[{stt_model}] STT segments: {len(stt_result.segments)}개")

        diarization_end = (
            max(s.end_sec for s in stt_result.segments)
            if stt_result.segments
            else audio_metadata.duration_sec or 0.0
        )

        start = time.perf_counter()
        status = "completed"
        error_message = None

        print(f"[{stt_model}] diarization 실행 중 (num_speakers={num_speakers}) ...")
        try:
            diarization_result = PyannoteDiarizer(
                model_name=diarization_model,
                num_speakers=num_speakers,
            ).diarize(audio_metadata.audio_path)
            print(f"[{stt_model}] 감지된 화자 수: {diarization_result.speaker_count}")
        except Exception as exc:
            diarization_result = fallback_single_speaker(0.0, diarization_end)
            status = "fallback"
            error_message = str(exc)
            print(f"[{stt_model}] diarization 실패, fallback 사용: {exc}")

        transcript = build_transcript(audio_metadata, stt_result, diarization_result)
        suffix = f"{stt_model}_speakers-{num_speakers}"
        transcript_path = output_dir / f"transcript_{suffix}.json"
        save_transcript_json(transcript, transcript_path)

        comparison = compare_transcripts(transcript, reference)
        elapsed = round(time.perf_counter() - start, 3)

        result = ExperimentResult(
            stt_model=stt_model,
            diarization_model=diarization_result.model_name,
            num_speakers_hint=num_speakers,
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
        results.append(result)

        print(
            f"[{stt_model}] 완료: {elapsed:.1f}초, "
            f"발화 {comparison.generated_utterances}개, "
            f"화자 {comparison.generated_speakers}명, "
            f"키워드 재현율 {comparison.keyword_recall:.3f}"
        )

    return results


def write_results(results: list[ExperimentResult], output_dir: Path) -> None:
    if not results:
        return
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
    print(f"\n결과 저장: {csv_path}, {json_path}")


def main() -> None:
    load_dotenv()

    audio_path = DEFAULT_AUDIO
    reference_path = DEFAULT_REFERENCE
    output_dir = DEFAULT_OUTPUT_DIR
    stt_models = ["base", "small", "medium", "large-v3"]
    num_speakers = 3
    diarization_model = None  # 기본값: pyannote/speaker-diarization-3.1

    print("=" * 60)
    print(f"STT 모델: {stt_models}")
    print(f"화자 수 힌트: {num_speakers}")
    print(f"출력 폴더: {output_dir}")
    print("=" * 60)

    results = run_experiments(
        audio_path=audio_path,
        reference_path=reference_path,
        output_dir=output_dir,
        stt_models=stt_models,
        num_speakers=num_speakers,
        diarization_model=diarization_model,
    )
    write_results(results, output_dir)

    print("\n\n=== 최종 결과 요약 ===")
    print(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
