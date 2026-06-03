from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from models import Chunk, ExtractedActionItem


class ExtractionPayload(BaseModel):
    action_items: list[ExtractedActionItem] = Field(default_factory=list)


class ExtractionValidationError(ValueError):
    pass


# ──────────────────────────────────────────
# 1. JSON 파싱 + Pydantic 스키마 검증
# ──────────────────────────────────────────

def parse_extraction_payload(raw_text: str) -> ExtractionPayload:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        data = _extract_first_json_object(raw_text)

    try:
        return ExtractionPayload.model_validate(data)
    except ValidationError as exc:
        raise ExtractionValidationError(str(exc)) from exc


def _extract_first_json_object(raw_text: str) -> Any:
    match = re.search(r"\{.*\}", raw_text, flags=re.DOTALL)
    if not match:
        raise ExtractionValidationError("LLM 응답에서 JSON 객체를 찾을 수 없습니다")
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ExtractionValidationError(str(exc)) from exc


# ──────────────────────────────────────────
# 2. 청크 대비 의미론적 검증 (환각·스키마 위반 탐지)
# ──────────────────────────────────────────

_VALID_CATEGORIES = {
    "performance", "creative", "budget", "schedule",
    "reporting", "experiment", "issue", "general",
}
_VALID_PRIORITIES = {"low", "medium", "high"}

# 설명 길이 기준
_MIN_DESC_LEN = 8
_MAX_DESC_LEN = 150

# 환각 판정에서 제외할 범용 토큰 (의미 구분력 없음)
_STOPWORDS = {
    "한다", "진행", "확인", "정리", "작업", "내용", "이슈", "관련",
    "합니다", "했습니다", "해야", "하고", "하면", "하는", "하여",
}


def validate_against_chunk(
    payload: ExtractionPayload,
    chunk: Chunk,
) -> tuple[ExtractionPayload, list[str]]:
    """Pydantic 통과 후 청크 내용 대비 2차 검증.

    탐지 항목:
    - 설명 너무 짧음 / 너무 김 (description_too_short / description_too_long)
    - 잘못된 카테고리 → general로 자동 정규화
    - 환각 의심: 설명 핵심 토큰이 청크 본문에 없음 (hallucination_risk)
    - 청크에 없는 화자가 담당자로 지정됨 (assignee_missing 추가)
    - 동일 청크 내 중복 설명 제거

    반환: (검증된 페이로드, 경고 메시지 목록)
    """
    chunk_tokens = _tokenize(chunk.chunk_text)
    chunk_speakers = _extract_speakers(chunk.chunk_text)
    warnings: list[str] = []
    seen_descriptions: set[str] = set()
    validated: list[ExtractedActionItem] = []

    for item in payload.action_items:
        flags = list(item.risk_flags)
        updated: dict[str, Any] = {}

        # ── 설명 길이 검사 ──────────────────────────
        desc = item.description.strip()
        if len(desc) < _MIN_DESC_LEN:
            warnings.append(f"설명이 너무 짧아 제외: '{desc}'")
            continue
        if len(desc) > _MAX_DESC_LEN:
            flags = _add_flag(flags, "description_too_long")
            updated["description"] = desc[:_MAX_DESC_LEN].rstrip() + "…"
            warnings.append(f"설명이 {len(desc)}자로 길어 잘림: '{desc[:40]}…'")

        # ── 카테고리 정규화 ─────────────────────────
        if item.category not in _VALID_CATEGORIES:
            warnings.append(f"알 수 없는 카테고리 '{item.category}' → 'general'로 정규화")
            updated["category"] = "general"

        # ── 환각 탐지 ───────────────────────────────
        if _is_hallucination(item.description, chunk_tokens):
            flags = _add_flag(flags, "hallucination_risk")
            warnings.append(f"환각 의심 (청크 토큰 미겹침): '{desc[:50]}'")

        # ── 담당자 화자 검증 ────────────────────────
        normalized = item.assignee_normalized
        if (
            normalized not in ("unassigned", "speaker_from_context")
            and chunk_speakers
            and normalized not in chunk_speakers
        ):
            flags = _add_flag(flags, "assignee_missing")
            warnings.append(f"화자 목록에 없는 담당자: '{normalized}'")

        # ── 중복 제거 ───────────────────────────────
        dedup_key = _normalize_text(desc)
        if dedup_key in seen_descriptions:
            warnings.append(f"중복 설명 제외: '{desc}'")
            continue
        seen_descriptions.add(dedup_key)

        updated["risk_flags"] = list(dict.fromkeys(flags))
        validated.append(item.model_copy(update=updated) if updated else item)

    return ExtractionPayload(action_items=validated), warnings


# ──────────────────────────────────────────
# 내부 헬퍼
# ──────────────────────────────────────────

def _tokenize(text: str) -> set[str]:
    tokens = re.findall(r"[0-9a-zA-Z가-힣]{2,}", text.lower())
    return {t for t in tokens if t not in _STOPWORDS}


def _is_hallucination(description: str, chunk_tokens: set[str]) -> bool:
    """설명의 의미 토큰 중 하나라도 청크에 있으면 환각 아님."""
    desc_tokens = _tokenize(description)
    if not desc_tokens:
        return False
    return not (desc_tokens & chunk_tokens)


def _extract_speakers(chunk_text: str) -> set[str]:
    """청크 텍스트에서 '[uN] speaker_label: ...' 형식의 화자 레이블 추출."""
    return set(re.findall(r"^\[[^\]]+\]\s+(\S+?):", chunk_text, flags=re.MULTILINE))


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _add_flag(flags: list[str], flag: str) -> list[str]:
    if flag not in flags:
        flags.append(flag)
    return flags
