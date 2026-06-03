from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import torchaudio

from models import Meeting, SourceType, stable_hash


SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac"}


@dataclass(frozen=True)
class AudioMetadata:
    audio_path: Path
    audio_hash: str
    meeting_id: str
    title: str
    duration_sec: float | None

    def to_meeting(self) -> Meeting:
        return Meeting(
            meeting_id=self.meeting_id,
            title=self.title,
            audio_path=str(self.audio_path),
            audio_hash=self.audio_hash,
            source_type=SourceType.AUDIO,
        )


def resolve_audio_path(input_path: str | Path) -> Path:
    path = Path(input_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {path}")
    if not path.is_file():
        raise ValueError(f"Audio path is not a file: {path}")
    if path.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
        raise ValueError(
            f"Unsupported audio extension '{path.suffix}'. "
            f"Supported: {sorted(SUPPORTED_AUDIO_EXTENSIONS)}"
        )
    return path


def compute_file_hash(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    hasher = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(chunk_size), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def get_audio_duration_sec(path: str | Path) -> float | None:
    try:
        metadata = torchaudio.info(str(path))
    except Exception:
        return None

    if metadata.sample_rate <= 0:
        return None
    return round(metadata.num_frames / metadata.sample_rate, 3)


def load_audio_metadata(input_path: str | Path, title: str | None = None) -> AudioMetadata:
    path = resolve_audio_path(input_path)
    audio_hash = compute_file_hash(path)
    meeting_id = stable_hash("meeting", audio_hash)
    return AudioMetadata(
        audio_path=path,
        audio_hash=audio_hash,
        meeting_id=meeting_id,
        title=title or path.stem,
        duration_sec=get_audio_duration_sec(path),
    )
