from __future__ import annotations

from dataclasses import dataclass

from ingestion.transcript_builder import load_transcript_json
from models import Transcript
from preprocessing.glossary import GLOSSARY


@dataclass(frozen=True)
class TranscriptComparison:
    generated_utterances: int
    reference_utterances: int
    generated_speakers: int
    reference_speakers: int
    keyword_recall: float
    missing_keywords: list[str]


def compare_transcripts(
    generated: Transcript,
    reference: Transcript,
) -> TranscriptComparison:
    generated_keywords = _keywords_in_transcript(generated)
    reference_keywords = _keywords_in_transcript(reference)
    missing_keywords = sorted(reference_keywords - generated_keywords)
    keyword_recall = (
        round((len(reference_keywords) - len(missing_keywords)) / len(reference_keywords), 3)
        if reference_keywords
        else 1.0
    )

    return TranscriptComparison(
        generated_utterances=len(generated.utterances),
        reference_utterances=len(reference.utterances),
        generated_speakers=len({u.speaker_raw for u in generated.utterances}),
        reference_speakers=len({u.speaker_raw for u in reference.utterances}),
        keyword_recall=keyword_recall,
        missing_keywords=missing_keywords,
    )


def compare_transcript_files(
    generated_path: str,
    reference_path: str,
) -> TranscriptComparison:
    return compare_transcripts(
        generated=load_transcript_json(generated_path),
        reference=load_transcript_json(reference_path),
    )


def _keywords_in_transcript(transcript: Transcript) -> set[str]:
    text = "\n".join(utterance.text.lower() for utterance in transcript.utterances)
    keywords = set()
    for term, (canonical, _) in GLOSSARY.items():
        if term in text:
            keywords.add(canonical)
    return keywords
