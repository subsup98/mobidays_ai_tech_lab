from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


DEFAULT_REFERENCE = Path("data/raw/ko_meeting_3speakers.json")


@dataclass(frozen=True)
class MatchedUtterance:
    generated_sequence_no: int
    generated_speaker: str
    generated_text: str
    reference_line_no: int
    reference_speaker: str
    reference_role: str
    reference_text: str
    similarity: float


def normalize_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^0-9a-z가-힣]+", "", text)
    return text


def similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize_text(left), normalize_text(right)).ratio()


def load_reference(path: Path) -> list[dict[str, object]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        {
            "line_no": int(segment.get("line_no") or segment.get("id") or index),
            "speaker": str(segment.get("speaker") or "unknown"),
            "role": str(segment.get("role") or ""),
            "text": str(segment.get("text") or ""),
        }
        for index, segment in enumerate(data.get("segments", []), start=1)
        if str(segment.get("text") or "").strip()
    ]


def load_generated(path: Path) -> list[dict[str, object]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        {
            "sequence_no": int(utterance.get("sequence_no") or index),
            "speaker": str(utterance.get("speaker_raw") or "unknown"),
            "text": str(utterance.get("text") or ""),
        }
        for index, utterance in enumerate(data.get("utterances", []), start=1)
        if str(utterance.get("text") or "").strip()
    ]


def match_utterances(
    generated: list[dict[str, object]],
    reference: list[dict[str, object]],
) -> list[MatchedUtterance]:
    matches = []
    for utterance in generated:
        best = max(
            reference,
            key=lambda ref: similarity(str(utterance["text"]), str(ref["text"])),
        )
        matches.append(
            MatchedUtterance(
                generated_sequence_no=int(utterance["sequence_no"]),
                generated_speaker=str(utterance["speaker"]),
                generated_text=str(utterance["text"]),
                reference_line_no=int(best["line_no"]),
                reference_speaker=str(best["speaker"]),
                reference_role=str(best["role"]),
                reference_text=str(best["text"]),
                similarity=round(similarity(str(utterance["text"]), str(best["text"])), 3),
            )
        )
    return matches


def summarize(matches: list[MatchedUtterance]) -> dict[str, object]:
    speaker_to_reference = defaultdict(Counter)
    speaker_similarity = defaultdict(list)
    reference_to_generated = defaultdict(Counter)

    for match in matches:
        speaker_to_reference[match.generated_speaker][match.reference_speaker] += 1
        speaker_similarity[match.generated_speaker].append(match.similarity)
        reference_to_generated[match.reference_speaker][match.generated_speaker] += 1

    generated_speakers = {}
    for speaker, counts in speaker_to_reference.items():
        total = sum(counts.values())
        top_speaker, top_count = counts.most_common(1)[0]
        generated_speakers[speaker] = {
            "matched_reference_counts": dict(counts),
            "dominant_reference_speaker": top_speaker,
            "dominant_share": round(top_count / total, 3) if total else 0.0,
            "avg_similarity": round(sum(speaker_similarity[speaker]) / total, 3)
            if total
            else 0.0,
            "matched_utterance_count": total,
        }

    reference_speakers = {
        speaker: dict(counts) for speaker, counts in reference_to_generated.items()
    }

    return {
        "generated_speakers": generated_speakers,
        "reference_speakers": reference_speakers,
    }


def write_outputs(
    matches: list[MatchedUtterance],
    summary: dict[str, object],
    output_dir: Path,
    stem: str,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = [match.__dict__ for match in matches]

    with (output_dir / f"{stem}_utterance_matches.csv").open(
        "w",
        encoding="utf-8",
        newline="",
    ) as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    (output_dir / f"{stem}_speaker_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Map generated speaker labels to reference speakers.")
    parser.add_argument("--generated", required=True)
    parser.add_argument("--reference", default=str(DEFAULT_REFERENCE))
    parser.add_argument("--output-dir", default="data/interim/speaker_mapping")
    args = parser.parse_args()

    generated_path = Path(args.generated)
    reference = load_reference(Path(args.reference))
    generated = load_generated(generated_path)
    matches = match_utterances(generated, reference)
    summary = summarize(matches)
    write_outputs(matches, summary, Path(args.output_dir), generated_path.stem)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
