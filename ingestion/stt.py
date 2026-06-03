from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

from faster_whisper import WhisperModel


@dataclass(frozen=True)
class STTSegment:
    start_sec: float
    end_sec: float
    text: str
    sequence_no: int


@dataclass(frozen=True)
class STTResult:
    segments: list[STTSegment]
    language: str | None
    language_probability: float | None
    model_name: str


class FasterWhisperSTT:
    def __init__(
        self,
        model_name: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
    ) -> None:
        self.model_name = model_name or os.getenv("STT_MODEL") or "small"
        self.device = device or os.getenv("STT_DEVICE") or "cpu"
        self.compute_type = compute_type or os.getenv("STT_COMPUTE_TYPE") or "int8"
        self._model: WhisperModel | None = None

    @property
    def model(self) -> WhisperModel:
        if self._model is None:
            self._model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
            )
        return self._model

    def transcribe(
        self,
        audio_path: str | Path,
        language: str = "ko",
        vad_filter: bool = True,
        beam_size: int = 5,
    ) -> STTResult:
        segments_iter, info = self.model.transcribe(
            str(audio_path),
            language=language,
            vad_filter=vad_filter,
            beam_size=beam_size,
        )
        segments = list(_to_segments(segments_iter))
        return STTResult(
            segments=segments,
            language=getattr(info, "language", None),
            language_probability=getattr(info, "language_probability", None),
            model_name=self.model_name,
        )


def _to_segments(raw_segments: Iterable[object]) -> Iterable[STTSegment]:
    for index, segment in enumerate(raw_segments, start=1):
        text = getattr(segment, "text", "").strip()
        if not text:
            continue
        yield STTSegment(
            start_sec=round(float(getattr(segment, "start", 0.0)), 3),
            end_sec=round(float(getattr(segment, "end", 0.0)), 3),
            text=text,
            sequence_no=index,
        )
