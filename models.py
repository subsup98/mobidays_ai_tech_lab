from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class SourceType(str, Enum):
    AUDIO = "audio"
    TRANSCRIPT = "transcript"
    MOCK = "mock"


class UtteranceSource(str, Enum):
    STT = "stt"
    PROVIDED_TRANSCRIPT = "provided_transcript"
    MOCK = "mock"


class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ActionStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"


class ExtractionProvider(str, Enum):
    GEMINI = "gemini"
    MOCK = "mock"


class ExtractionMode(str, Enum):
    REAL = "real"
    MOCK = "mock"
    FALLBACK = "fallback"


class Meeting(BaseModel):
    meeting_id: str
    title: str
    meeting_date: date | None = None
    audio_path: str | None = None
    audio_hash: str | None = None
    transcript_path: str | None = None
    source_type: SourceType = SourceType.AUDIO


class Participant(BaseModel):
    participant_id: str
    meeting_id: str
    speaker_raw: str
    speaker_normalized: str | None = None
    role: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class Utterance(BaseModel):
    utterance_id: str
    meeting_id: str
    speaker_raw: str
    text: str
    sequence_no: int = Field(ge=1)
    participant_id: str | None = None
    speaker_normalized: str | None = None
    start_sec: float | None = Field(default=None, ge=0.0)
    end_sec: float | None = Field(default=None, ge=0.0)
    source: UtteranceSource = UtteranceSource.STT

    @model_validator(mode="after")
    def validate_time_range(self) -> "Utterance":
        if (
            self.start_sec is not None
            and self.end_sec is not None
            and self.end_sec < self.start_sec
        ):
            raise ValueError("end_sec must be greater than or equal to start_sec")
        return self


class Transcript(BaseModel):
    meeting: Meeting
    participants: list[Participant] = Field(default_factory=list)
    utterances: list[Utterance]

    @field_validator("utterances")
    @classmethod
    def utterances_must_be_sorted(cls, value: list[Utterance]) -> list[Utterance]:
        sequence_numbers = [utterance.sequence_no for utterance in value]
        if sequence_numbers != sorted(sequence_numbers):
            raise ValueError("utterances must be sorted by sequence_no")
        if len(sequence_numbers) != len(set(sequence_numbers)):
            raise ValueError("utterance sequence_no values must be unique")
        return value


class Chunk(BaseModel):
    chunk_id: str
    meeting_id: str
    chunk_text: str
    utterance_ids: list[str]
    topic_hint: str | None = None
    start_sequence_no: int | None = None
    end_sequence_no: int | None = None

    @field_validator("utterance_ids")
    @classmethod
    def utterance_ids_required(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("chunk must include at least one utterance_id")
        return value


class ExtractedActionItem(BaseModel):
    assignee: str = "unassigned"
    assignee_normalized: str = "unassigned"
    description: str
    category: str = "uncategorized"
    due_date: date | None = None
    priority: Priority = Priority.MEDIUM
    llm_confidence: float = Field(ge=0.0, le=1.0)
    source_utterance_ids: list[str]
    campaign_context: str | None = None
    advertiser_context: str | None = None
    risk_flags: list[str] = Field(default_factory=list)

    @field_validator("source_utterance_ids")
    @classmethod
    def source_required(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("source_utterance_ids is required")
        return value


class ActionItem(BaseModel):
    action_item_id: str
    dedup_key: str
    meeting_id: str
    sequence_no: int = Field(ge=1)
    assignee: str
    assignee_normalized: str
    description: str
    normalized_task_signature: str
    category: str
    priority: Priority = Priority.MEDIUM
    status: ActionStatus = ActionStatus.OPEN
    llm_confidence: float = Field(ge=0.0, le=1.0)
    validation_score: float = Field(ge=0.0, le=1.0)
    final_confidence: float = Field(ge=0.0, le=1.0)
    review_required: bool
    source_utterance_ids: list[str]
    chunk_id: str | None = None
    extraction_run_id: str | None = None
    due_date: date | None = None
    risk_flags: list[str] = Field(default_factory=list)
    campaign_context: str | None = None
    advertiser_context: str | None = None

    def to_db_row(self) -> dict[str, Any]:
        return {
            "action_item_id": self.action_item_id,
            "dedup_key": self.dedup_key,
            "meeting_id": self.meeting_id,
            "chunk_id": self.chunk_id,
            "extraction_run_id": self.extraction_run_id,
            "sequence_no": self.sequence_no,
            "assignee": self.assignee,
            "assignee_normalized": self.assignee_normalized,
            "description": self.description,
            "normalized_task_signature": self.normalized_task_signature,
            "category": self.category,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "priority": self.priority.value,
            "status": self.status.value,
            "llm_confidence": self.llm_confidence,
            "validation_score": self.validation_score,
            "final_confidence": self.final_confidence,
            "review_required": int(self.review_required),
            "risk_flags_json": json.dumps(self.risk_flags, ensure_ascii=False),
            "campaign_context": self.campaign_context,
            "advertiser_context": self.advertiser_context,
        }


class ExtractionRun(BaseModel):
    extraction_run_id: str
    meeting_id: str
    provider: ExtractionProvider = ExtractionProvider.MOCK
    model_name: str
    prompt_version: str = "v1"
    mode: ExtractionMode = ExtractionMode.MOCK
    raw_request_json: str | None = None
    raw_response_json: str | None = None
    parsed_ok: bool = False
    retry_count: int = Field(default=0, ge=0)
    error_message: str | None = None


DOMAIN_TERMS = {
    "roas": ["roas", "알오에이에스"],
    "cpm": ["cpm"],
    "cta": ["cta"],
    "ab_test": ["a/b", "ab", "에이비", "테스트"],
    "report": ["리포트", "보고서", "정리", "공유"],
    "issue": ["이슈", "문제", "이상", "원인"],
    "creative": ["소재", "배너", "카피", "문구"],
    "budget": ["예산", "비용"],
}

ACTION_VERBS = {
    "create": ["작성", "정리", "공유", "만들", "제작"],
    "check": ["확인", "체크", "검토", "조사", "분석"],
    "update": ["수정", "반영", "업데이트", "변경"],
    "run": ["진행", "테스트", "실행"],
}


def stable_hash(*parts: Any, length: int = 16) -> str:
    normalized = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:length]


def normalize_text(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"\s+", " ", value)
    return value


def normalized_task_signature(description: str) -> str:
    text = normalize_text(description)
    tokens: list[str] = []

    for canonical, variants in DOMAIN_TERMS.items():
        if any(variant in text for variant in variants):
            tokens.append(canonical)

    for canonical, variants in ACTION_VERBS.items():
        if any(variant in text for variant in variants):
            tokens.append(canonical)
            break

    if not tokens:
        fallback = re.sub(r"[^0-9a-z가-힣]+", " ", text)
        tokens = fallback.split()[:4] or ["unknown"]

    return ":".join(dict.fromkeys(tokens))


def build_action_item_id(meeting_id: str, dedup_key: str) -> str:
    return stable_hash(meeting_id, dedup_key)


def build_dedup_key(
    meeting_id: str,
    assignee_normalized: str,
    category: str,
    due_date: date | str | None,
    task_signature: str,
) -> str:
    if isinstance(due_date, date):
        due_date_value = due_date.isoformat()
    else:
        due_date_value = due_date or "none"
    return stable_hash(
        meeting_id,
        assignee_normalized or "unassigned",
        category or "uncategorized",
        due_date_value,
        task_signature,
    )


def compute_validation_score(item: ExtractedActionItem) -> tuple[float, list[str]]:
    risk_flags = list(item.risk_flags)
    score = 1.0

    if item.assignee_normalized == "unassigned":
        risk_flags.append("assignee_missing")
        score -= 0.2
    if not item.due_date:
        risk_flags.append("due_date_missing")
        score -= 0.1
    if not item.source_utterance_ids:
        risk_flags.append("source_missing")
        score -= 0.4
    if item.category == "uncategorized":
        risk_flags.append("category_unclear")
        score -= 0.1
    if len(item.description.strip()) < 5:
        risk_flags.append("description_too_short")
        score -= 0.2

    unique_flags = list(dict.fromkeys(risk_flags))
    return max(0.0, round(score, 3)), unique_flags


def materialize_action_item(
    meeting_id: str,
    chunk_id: str | None,
    sequence_no: int,
    extracted: ExtractedActionItem,
    extraction_run_id: str | None = None,
    confidence_threshold: float = 0.7,
    meeting_date: date | None = None,
) -> ActionItem:
    # LLM이 due_date를 비웠어도 설명에 상대 시간 표현이 있으면 회의일 기준으로
    # 절대 날짜를 채운다(README "상대 기한 해석" 가정). due_date가 채워지면
    # compute_validation_score가 due_date_missing 플래그를 붙이지 않는다.
    if extracted.due_date is None and meeting_date is not None:
        from extraction.due_date_parser import parse_due_date

        parsed = parse_due_date(extracted.description, meeting_date)
        if parsed is not None:
            extracted = extracted.model_copy(update={"due_date": parsed})

    task_signature = normalized_task_signature(extracted.description)
    validation_score, risk_flags = compute_validation_score(extracted)
    final_confidence = min(extracted.llm_confidence, validation_score)
    review_required = final_confidence < confidence_threshold or bool(risk_flags)

    dedup_key = build_dedup_key(
        meeting_id=meeting_id,
        assignee_normalized=extracted.assignee_normalized,
        category=extracted.category,
        due_date=extracted.due_date,
        task_signature=task_signature,
    )

    return ActionItem(
        # dedup_key 기반으로 PK를 생성해 PK와 UNIQUE(meeting_id, dedup_key)가
        # 항상 일치하게 한다. chunk_id+sequence_no 기반은 같은 배치에서 PK는
        # 같은데 dedup_key는 다른 조합이 생겨 action_items_pkey 충돌을 유발했다.
        action_item_id=build_action_item_id(meeting_id, dedup_key),
        dedup_key=dedup_key,
        meeting_id=meeting_id,
        chunk_id=chunk_id,
        extraction_run_id=extraction_run_id,
        sequence_no=sequence_no,
        assignee=extracted.assignee,
        assignee_normalized=extracted.assignee_normalized,
        description=extracted.description,
        normalized_task_signature=task_signature,
        category=extracted.category,
        due_date=extracted.due_date,
        priority=extracted.priority,
        llm_confidence=extracted.llm_confidence,
        validation_score=validation_score,
        final_confidence=final_confidence,
        review_required=review_required,
        source_utterance_ids=extracted.source_utterance_ids,
        risk_flags=risk_flags,
        campaign_context=extracted.campaign_context,
        advertiser_context=extracted.advertiser_context,
    )


def utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat()
