from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from pyannote.audio import Pipeline


DEFAULT_DIARIZATION_MODEL = "pyannote/speaker-diarization-3.1"


@dataclass(frozen=True)
class SpeakerSegment:
    start_sec: float
    end_sec: float
    speaker: str


@dataclass(frozen=True)
class DiarizationResult:
    segments: list[SpeakerSegment]
    model_name: str

    @property
    def speaker_count(self) -> int:
        return len({segment.speaker for segment in self.segments})


class PyannoteDiarizer:
    def __init__(
        self,
        model_name: str | None = None,
        auth_token: str | None = None,
        num_speakers: int | None = None,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
    ) -> None:
        self.model_name = (
            model_name
            or os.getenv("DIARIZATION_MODEL")
            or DEFAULT_DIARIZATION_MODEL
        )
        self.auth_token = auth_token or os.getenv("HUGGINGFACE_TOKEN")
        env_num_speakers = os.getenv("DIARIZATION_NUM_SPEAKERS")
        env_min_speakers = os.getenv("DIARIZATION_MIN_SPEAKERS")
        env_max_speakers = os.getenv("DIARIZATION_MAX_SPEAKERS")
        self.num_speakers = num_speakers or (
            int(env_num_speakers) if env_num_speakers else None
        )
        self.min_speakers = min_speakers or (
            int(env_min_speakers) if env_min_speakers else None
        )
        self.max_speakers = max_speakers or (
            int(env_max_speakers) if env_max_speakers else None
        )
        self._pipeline: Pipeline | None = None

    @property
    def pipeline(self) -> Pipeline:
        if not self.auth_token:
            raise RuntimeError(
                "HUGGINGFACE_TOKEN is required for pyannote diarization. "
                "Set it in the environment or use fallback speaker assignment."
            )
        if self._pipeline is None:
            self._pipeline = Pipeline.from_pretrained(
                self.model_name,
                use_auth_token=self.auth_token,
            )
            if self._pipeline is None:
                raise RuntimeError(
                    f"Could not load diarization model '{self.model_name}'. "
                    "Accept the model user conditions on Hugging Face and verify "
                    "HUGGINGFACE_TOKEN permissions."
                )
        return self._pipeline

    def diarize(self, audio_path: str | Path) -> DiarizationResult:
        kwargs = {}
        if self.num_speakers:
            kwargs["num_speakers"] = self.num_speakers
        else:
            if self.min_speakers:
                kwargs["min_speakers"] = self.min_speakers
            if self.max_speakers:
                kwargs["max_speakers"] = self.max_speakers
        diarization = self.pipeline(str(audio_path), **kwargs)
        segments = [
            SpeakerSegment(
                start_sec=round(float(turn.start), 3),
                end_sec=round(float(turn.end), 3),
                speaker=str(speaker),
            )
            for turn, _, speaker in diarization.itertracks(yield_label=True)
        ]
        segments.sort(key=lambda segment: (segment.start_sec, segment.end_sec))
        return DiarizationResult(segments=segments, model_name=self.model_name)


def fallback_single_speaker(start_sec: float, end_sec: float) -> DiarizationResult:
    return DiarizationResult(
        segments=[
            SpeakerSegment(
                start_sec=round(start_sec, 3),
                end_sec=round(end_sec, 3),
                speaker="SPEAKER_00",
            )
        ],
        model_name="fallback_single_speaker",
    )
