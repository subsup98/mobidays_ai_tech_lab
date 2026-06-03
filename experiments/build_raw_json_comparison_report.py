from __future__ import annotations

import csv
import json
import sys
from difflib import SequenceMatcher
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


REFERENCE_PATH = Path("data/raw/ko_meeting_3speakers.json")
OUTPUT_DIR = Path("data/interim/raw_json_comparison")

MODELS = [
    {
        "name": "base + pyannote 3.1",
        "transcript": Path("data/interim/model_comparison_auto/transcript_base_speakers-auto.json"),
        "speaker_summary": Path("data/interim/speaker_mapping/transcript_base_speakers-auto_speaker_summary.json"),
        "elapsed_sec": 196.45,
    },
    {
        "name": "small + pyannote 3.1",
        "transcript": Path("data/interim/model_comparison_auto/transcript_small_speakers-auto.json"),
        "speaker_summary": Path("data/interim/speaker_mapping/transcript_small_speakers-auto_speaker_summary.json"),
        "elapsed_sec": 231.37,
    },
    {
        "name": "medium + pyannote 3.1",
        "transcript": Path("data/interim/model_comparison_auto/transcript_medium_speakers-auto.json"),
        "speaker_summary": Path("data/interim/speaker_mapping/transcript_medium_speakers-auto_speaker_summary.json"),
        "elapsed_sec": 506.62,
    },
    {
        "name": "large-v3 + pyannote 3.1",
        "transcript": Path("data/interim/model_comparison_large_auto/transcript_large-v3_speakers-auto.json"),
        "speaker_summary": Path("data/interim/speaker_mapping/transcript_large-v3_speakers-auto_speaker_summary.json"),
        "elapsed_sec": 822.91,
    },
    {
        "name": "large-v3 + NeMo Sortformer",
        "transcript": Path("data/interim/nemo_sortformer/transcript_large-v3_sortformer.json"),
        "speaker_summary": Path("data/interim/speaker_mapping/transcript_large-v3_sortformer_speaker_summary.json"),
        "elapsed_sec": 860.81,
        "elapsed_note": "estimated: large-v3 STT 822.91 sec + Sortformer diarization 37.90 sec",
    },
    {
        "name": "large-v3 + pyannote community-1",
        "transcript": Path("data/interim/pyannote4_community/transcript_large-v3_community1.json"),
        "speaker_summary": Path("data/interim/speaker_mapping/transcript_large-v3_community1_speaker_summary.json"),
        "elapsed_sec": 953.31,
        "elapsed_note": "estimated: large-v3 STT 822.91 sec + community-1 diarization 130.40 sec",
    },
]


def normalize(text: str) -> str:
    return "".join(ch.lower() for ch in text if ch.isalnum())


def similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize(left), normalize(right)).ratio()


def load_reference() -> list[dict[str, object]]:
    data = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    return [
        {
            "line_no": int(segment.get("line_no") or segment.get("id") or index),
            "speaker": str(segment.get("speaker") or "unknown"),
            "role": str(segment.get("role") or ""),
            "text": str(segment.get("text") or ""),
        }
        for index, segment in enumerate(data["segments"], start=1)
    ]


def load_transcript(path: Path) -> list[dict[str, object]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        {
            "sequence_no": int(utterance.get("sequence_no") or index),
            "speaker": str(utterance.get("speaker_raw") or "unknown"),
            "text": str(utterance.get("text") or ""),
        }
        for index, utterance in enumerate(data["utterances"], start=1)
    ]


def best_match(utterance: dict[str, object], reference: list[dict[str, object]]) -> dict[str, object]:
    return max(reference, key=lambda row: similarity(str(utterance["text"]), str(row["text"])))


def summarize_mapping(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    parts = []
    for generated_speaker, info in data["generated_speakers"].items():
        counts = info["matched_reference_counts"]
        count_text = ", ".join(f"{speaker} {count}" for speaker, count in counts.items())
        parts.append(f"{generated_speaker}: {count_text}")
    return " / ".join(parts)


def build_rows() -> tuple[list[dict[str, object]], dict[str, list[dict[str, object]]]]:
    reference = load_reference()
    summary_rows = []
    sample_rows_by_model = {}

    for model in MODELS:
        generated = load_transcript(model["transcript"])
        sample_rows = []
        scores = []
        for utterance in generated:
            match = best_match(utterance, reference)
            score = round(similarity(str(utterance["text"]), str(match["text"])), 3)
            scores.append(score)
            if len(sample_rows) < 12:
                sample_rows.append(
                    {
                        "model": model["name"],
                        "generated_seq": utterance["sequence_no"],
                        "generated_speaker": utterance["speaker"],
                        "reference_line": match["line_no"],
                        "reference_speaker": match["speaker"],
                        "similarity": score,
                        "generated_text": utterance["text"],
                        "reference_text": match["text"],
                    }
                )

        speakers = sorted({row["speaker"] for row in generated})
        summary_rows.append(
            {
                "model": model["name"],
                "elapsed_sec": model["elapsed_sec"] if model["elapsed_sec"] is not None else "",
                "elapsed_note": model.get("elapsed_note", ""),
                "generated_utterances": len(generated),
                "reference_utterances": len(reference),
                "utterance_delta": len(generated) - len(reference),
                "generated_speakers": len(speakers),
                "reference_speakers": 3,
                "avg_text_similarity": round(sum(scores) / len(scores), 3),
                "speaker_mapping": summarize_mapping(model["speaker_summary"]),
                "transcript_path": str(model["transcript"]),
            }
        )
        sample_rows_by_model[str(model["name"])] = sample_rows

    return summary_rows, sample_rows_by_model


def write_markdown(summary_rows: list[dict[str, object]], sample_rows_by_model: dict[str, list[dict[str, object]]]) -> None:
    lines = [
        "# Raw JSON Comparison by Model",
        "",
        "## Reference",
        "",
        "- Raw JSON: `data/raw/ko_meeting_3speakers.json`",
        "- Reference utterances: 37",
        "- Reference speakers: 3 (`지훈`, `수아`, `채린`)",
        "",
        "## Summary",
        "",
        "| Model | Elapsed | Utterances | Delta | Speakers | Avg text similarity | Speaker mapping |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in summary_rows:
        elapsed = f"{row['elapsed_sec']} sec" if row["elapsed_sec"] != "" else "diarization only"
        lines.append(
            f"| {row['model']} | {elapsed} | {row['generated_utterances']} / {row['reference_utterances']} "
            f"| {row['utterance_delta']:+} | {row['generated_speakers']} / {row['reference_speakers']} "
            f"| {row['avg_text_similarity']} | {row['speaker_mapping']} |"
        )

    lines.extend(
        [
            "",
            "## First 12 Utterance Matches",
            "",
            "Each generated utterance is matched to the most similar raw JSON utterance by normalized text similarity.",
        ]
    )

    for model_name, rows in sample_rows_by_model.items():
        lines.extend(
            [
                "",
                f"### {model_name}",
                "",
                "| Gen # | Gen speaker | Ref # | Ref speaker | Sim | Generated text | Raw JSON text |",
                "|---:|---|---:|---|---:|---|---|",
            ]
        )
        for row in rows:
            generated_text = str(row["generated_text"]).replace("|", "/")
            reference_text = str(row["reference_text"]).replace("|", "/")
            lines.append(
                f"| {row['generated_seq']} | {row['generated_speaker']} | {row['reference_line']} "
                f"| {row['reference_speaker']} | {row['similarity']} | {generated_text} | {reference_text} |"
            )

    (OUTPUT_DIR / "raw_json_model_comparison.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary_rows, sample_rows_by_model = build_rows()

    with (OUTPUT_DIR / "summary.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(summary_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_rows)

    all_sample_rows = [
        row for rows in sample_rows_by_model.values() for row in rows
    ]
    with (OUTPUT_DIR / "utterance_match_samples.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(all_sample_rows[0].keys()))
        writer.writeheader()
        writer.writerows(all_sample_rows)

    write_markdown(summary_rows, sample_rows_by_model)
    print(OUTPUT_DIR / "raw_json_model_comparison.md")


if __name__ == "__main__":
    main()
