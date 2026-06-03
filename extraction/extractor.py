from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Protocol

from google import genai

from extraction.prompt import PROMPT_VERSION, build_chunk_prompt
from extraction.validator import (
    ExtractionPayload,
    ExtractionValidationError,
    parse_extraction_payload,
    validate_against_chunk,
)
from models import (
    ActionItem,
    Chunk,
    ExtractedActionItem,
    ExtractionMode,
    ExtractionProvider,
    ExtractionRun,
    Priority,
    materialize_action_item,
    stable_hash,
)


DEFAULT_GEMINI_MODEL = "gemini-2.5-flash-lite"


class Extractor(Protocol):
    def extract(self, chunk: Chunk, meeting_id: str) -> "ExtractionResult":
        ...


@dataclass(frozen=True)
class ExtractionResult:
    run: ExtractionRun
    action_items: list[ActionItem]


class MockExtractor:
    provider = ExtractionProvider.MOCK

    def __init__(self, model_name: str = "mock-rule-v1") -> None:
        self.model_name = model_name

    def extract(self, chunk: Chunk, meeting_id: str) -> ExtractionResult:
        run_id = stable_hash("extract", meeting_id, chunk.chunk_id, self.model_name)
        extracted = _mock_extract(chunk)
        action_items = [
            materialize_action_item(
                meeting_id=meeting_id,
                chunk_id=chunk.chunk_id,
                sequence_no=index,
                extracted=item,
                extraction_run_id=run_id,
            )
            for index, item in enumerate(extracted.action_items, start=1)
        ]
        return ExtractionResult(
            run=ExtractionRun(
                extraction_run_id=run_id,
                meeting_id=meeting_id,
                provider=ExtractionProvider.MOCK,
                model_name=self.model_name,
                prompt_version=PROMPT_VERSION,
                mode=ExtractionMode.MOCK,
                raw_request_json=json.dumps(
                    {"chunk_id": chunk.chunk_id},
                    ensure_ascii=False,
                ),
                raw_response_json=extracted.model_dump_json(),
                parsed_ok=True,
                retry_count=0,
            ),
            action_items=action_items,
        )


class GeminiExtractor:
    provider = ExtractionProvider.GEMINI

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
        max_retries: int = 3,
    ) -> None:
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model_name = model_name or os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        self.max_retries = max_retries
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is required for GeminiExtractor")
        self.client = genai.Client(api_key=self.api_key)

    def extract(self, chunk: Chunk, meeting_id: str) -> ExtractionResult:
        prompt = build_chunk_prompt(chunk)
        run_id = stable_hash("extract", meeting_id, chunk.chunk_id, self.model_name)
        last_error: str | None = None
        raw_response_text = ""

        for attempt in range(self.max_retries):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                )
                raw_response_text = getattr(response, "text", "") or ""

                # 1단계: JSON 파싱 + Pydantic 스키마 검증
                payload = parse_extraction_payload(raw_response_text)

                # 2단계: 소스 utterance ID 정제
                payload = _sanitize_payload_sources(payload, chunk)

                # 3단계: 청크 대비 의미론적 검증 (환각·중복·카테고리)
                payload, warnings = validate_against_chunk(payload, chunk)

                # 검증 후 아이템이 모두 제거됐으면 재시도
                if not payload.action_items and attempt < self.max_retries - 1:
                    last_error = (
                        f"검증 후 유효한 액션아이템 없음 "
                        f"(경고: {'; '.join(warnings) if warnings else '없음'})"
                    )
                    continue

                action_items = [
                    materialize_action_item(
                        meeting_id=meeting_id,
                        chunk_id=chunk.chunk_id,
                        sequence_no=index,
                        extracted=item,
                        extraction_run_id=run_id,
                    )
                    for index, item in enumerate(payload.action_items, start=1)
                ]
                validation_note = (
                    f"검증 경고 {len(warnings)}건: {'; '.join(warnings)}"
                    if warnings else None
                )
                return ExtractionResult(
                    run=ExtractionRun(
                        extraction_run_id=run_id,
                        meeting_id=meeting_id,
                        provider=ExtractionProvider.GEMINI,
                        model_name=self.model_name,
                        prompt_version=PROMPT_VERSION,
                        mode=ExtractionMode.REAL,
                        raw_request_json=json.dumps({"prompt": prompt}, ensure_ascii=False),
                        raw_response_json=raw_response_text,
                        parsed_ok=True,
                        retry_count=attempt,
                        error_message=validation_note,
                    ),
                    action_items=action_items,
                )

            except ExtractionValidationError as exc:
                # 스키마 위반: 재시도
                last_error = f"스키마 위반 (시도 {attempt + 1}/{self.max_retries}): {exc}"
            except Exception as exc:
                # API 오류 등 기타 예외: 재시도
                last_error = f"추출 오류 (시도 {attempt + 1}/{self.max_retries}): {exc}"

        return ExtractionResult(
            run=ExtractionRun(
                extraction_run_id=run_id,
                meeting_id=meeting_id,
                provider=ExtractionProvider.GEMINI,
                model_name=self.model_name,
                prompt_version=PROMPT_VERSION,
                mode=ExtractionMode.REAL,
                raw_request_json=json.dumps({"prompt": prompt}, ensure_ascii=False),
                raw_response_json=raw_response_text or None,
                parsed_ok=False,
                retry_count=self.max_retries,
                error_message=last_error,
            ),
            action_items=[],
        )


class FallbackExtractor:
    def __init__(self, primary: Extractor, fallback: Extractor) -> None:
        self.primary = primary
        self.fallback = fallback

    def extract(self, chunk: Chunk, meeting_id: str) -> ExtractionResult:
        result = self.primary.extract(chunk, meeting_id)
        if result.run.parsed_ok:
            return result

        fallback_result = self.fallback.extract(chunk, meeting_id)
        fallback_run = fallback_result.run.model_copy(
            update={
                "mode": ExtractionMode.FALLBACK,
                "error_message": result.run.error_message,
            }
        )
        return ExtractionResult(run=fallback_run, action_items=fallback_result.action_items)


def build_extractor() -> Extractor:
    provider = os.getenv("LLM_PROVIDER", "mock").lower()
    fallback_enabled = os.getenv("LLM_FALLBACK", "mock").lower() == "mock"

    if provider == "gemini":
        try:
            primary: Extractor = GeminiExtractor()
        except Exception:
            if fallback_enabled:
                return MockExtractor()
            raise
        if fallback_enabled:
            return FallbackExtractor(primary=primary, fallback=MockExtractor())
        return primary

    return MockExtractor()


def _mock_extract(chunk: Chunk) -> ExtractionPayload:
    text = chunk.chunk_text
    action_text, source_ids = _select_action_evidence(text, chunk.utterance_ids)
    decision_text = action_text or text
    assignee, assignee_normalized = _guess_assignee(decision_text)
    category = _guess_category(decision_text, chunk.topic_hint)

    if not action_text and not _looks_actionable(text):
        return ExtractionPayload(action_items=[])

    description = _build_mock_description(decision_text, category)
    priority = _guess_priority(decision_text)
    risk_flags = []
    if assignee_normalized == "unassigned":
        risk_flags.append("assignee_missing")
    risk_flags.append("due_date_missing")

    return ExtractionPayload(
        action_items=[
            ExtractedActionItem(
                assignee=assignee,
                assignee_normalized=assignee_normalized,
                description=description,
                category=category,
                due_date=None,
                priority=priority,
                llm_confidence=0.72,
                source_utterance_ids=source_ids,
                campaign_context=_guess_campaign(text),
                advertiser_context=None,
                risk_flags=risk_flags,
            )
        ]
    )


def _sanitize_payload_sources(
    payload: ExtractionPayload,
    chunk: Chunk,
) -> ExtractionPayload:
    valid_sources = set(chunk.utterance_ids)
    sanitized_items = []

    for item in payload.action_items:
        source_ids = [
            source_id
            for source_id in item.source_utterance_ids
            if source_id in valid_sources
        ]
        risk_flags = list(item.risk_flags)

        if not source_ids:
            source_ids = chunk.utterance_ids[:1]
            risk_flags.append("source_corrected")

        sanitized_items.append(
            item.model_copy(
                update={
                    "source_utterance_ids": source_ids,
                    "risk_flags": list(dict.fromkeys(risk_flags)),
                }
            )
        )

    return ExtractionPayload(action_items=sanitized_items)


def _looks_actionable(text: str) -> bool:
    markers = [
        "할게요",
        "해둘게요",
        "다시 드릴게요",
        "챙길게요",
        "푸시할게요",
        "하겠습니다",
        "정리해",
        "정리할게요",
        "올려놓을게요",
        "작성",
        "공유할게요",
        "진행할게요",
        "세팅해야",
        "다시 세팅",
        "오늘 안에",
        "내일 오전",
        "컨펌",
    ]
    non_action_markers = [
        "정리할 게",
        "얘기 잠깐",
        "어떻게 생각",
        "봐요?",
        "나요?",
        "맞죠?",
        "말씀드릴게요",
        "받고 가야",
    ]
    if any(marker in text for marker in non_action_markers):
        return False
    commitment_markers = ["제가", "저는", "내가", "담당자한테", "담당자에게", "다시 드릴게요"]
    if not any(marker in text for marker in commitment_markers):
        return False
    return any(marker in text for marker in markers)


def _select_action_evidence(text: str, fallback_ids: list[str]) -> tuple[str | None, list[str]]:
    for line in text.splitlines():
        if _looks_actionable(line):
            source_id = None
            if line.startswith("[") and "]" in line:
                source_id = line[1:line.index("]")]
            evidence_text = line.split(": ", 1)[-1].strip()
            return evidence_text, [source_id] if source_id else fallback_ids[:1]
    return None, fallback_ids[:1]


def _guess_assignee(text: str) -> tuple[str, str]:
    if "team_lead" in text or "팀장" in text:
        return "팀장", "team_lead"
    if "performance_marketer" in text or "마케터" in text:
        return "퍼포먼스 마케터", "performance_marketer"
    if "content_designer" in text or "디자이너" in text:
        return "콘텐츠 디자이너", "content_designer"
    if "제가" in text:
        return "발화자", "speaker_from_context"
    return "unassigned", "unassigned"


def _guess_category(text: str, topic_hint: str | None) -> str:
    if topic_hint and topic_hint != "general":
        return topic_hint
    if any(term in text.lower() for term in ["roas", "cpm", "ctr", "성과"]):
        return "performance"
    if any(term in text for term in ["소재", "카피", "문구", "CTA"]):
        return "creative"
    if any(term in text for term in ["예산", "비용"]):
        return "budget"
    if any(term in text for term in ["리포트", "보고서"]):
        return "reporting"
    return "general"


def _guess_priority(text: str) -> Priority:
    urgent_markers = [
        "오늘",
        "내일",
        "오전까지",
        "이번 주",
        "바로",
        "급",
        "막히",
        "안 오면",
        "컨펌",
        "보장",
    ]
    low_markers = ["나중", "추후", "시간 되면", "여유"]
    if any(marker in text for marker in urgent_markers):
        return Priority.HIGH
    if any(marker in text for marker in low_markers):
        return Priority.LOW
    return Priority.MEDIUM


def _build_mock_description(text: str, category: str) -> str:
    cleaned = _clean_action_text(text)
    if "인사이트" in cleaned and "비주얼" in cleaned and "톤" in cleaned:
        return "지난 캠페인 인사이트를 기준으로 비주얼 톤을 결정한다"
    if "다음 달 캠페인" in cleaned and "정리" in cleaned:
        return "다음 달 캠페인 전 정리 항목을 확인한다"
    if "비주얼 카드" in cleaned or "빈 슬롯" in cleaned:
        return "비주얼 카드 순서와 빈 슬롯 카피를 정리한다"
    if "픽셀 정리" in cleaned and "챙길" in cleaned:
        return "픽셀 정리 이후 관련 후속 이슈를 함께 챙긴다"
    if "픽셀" in cleaned and ("다시" in cleaned or "보장" in cleaned):
        return "픽셀 보장 내용을 확인해 다시 공유한다"
    if "오늘 안에" in cleaned and "내일 오전" in cleaned and ("보정" in cleaned or "공유" in cleaned):
        return "전환 수치를 오늘 안에 보정하고 내일 오전 공유한다"
    if "담당자" in cleaned and "푸시" in cleaned:
        return "담당자에게 자료 전달을 재촉하고 미수신 시 임시 컷으로 진행한다"
    if "헤드라인" in cleaned or "기존 AB" in cleaned or "카피" in cleaned and "세팅" in cleaned:
        return "변경된 카피로 A/B 테스트를 다시 세팅한다"
    if "컨펌" in cleaned and "슬랙" in cleaned:
        return "컨펌 담당자를 슬랙으로 정리한다"
    if "같이 챙길" in cleaned:
        return "관련 이슈를 함께 챙기고 후속 진행 조건을 확인한다"
    if "제안서" in cleaned and "마무리" in cleaned:
        return "픽셀 정리 후 제안서 마무리 작업을 진행한다"
    if "ROAS" in text.upper() or "roas" in text.lower():
        return "ROAS 리포트를 정리한다"
    if "CPM" in text.upper() or "cpm" in text.lower():
        return "CPM 이슈 원인을 확인한다"
    if "CTA" in text.upper() or "cta" in text.lower():
        return "CTA 문구 테스트를 진행한다"
    if category == "creative":
        return _sentence_to_task(cleaned) if cleaned else "광고 소재 작업 내용을 확인한다"
    if category == "budget":
        return "예산 관련 내용을 확인한다"
    if cleaned:
        return _sentence_to_task(cleaned)
    return "후속 작업을 확인한다"


def _clean_action_text(text: str) -> str:
    text = text.split(": ", 1)[-1].strip()
    fillers = ["어,", "음,", "아,", "일단 ", "그럼 ", "근데 "]
    for filler in fillers:
        text = text.replace(filler, "")
    return " ".join(text.split())


def _sentence_to_task(text: str) -> str:
    text = text.strip()
    replacements = [
        ("해둘게요", "한다"),
        ("드릴게요", "공유한다"),
        ("할게요", "한다"),
        ("챙길게요", "챙긴다"),
        ("정리하고요", "정리한다"),
        ("정리할게요", "정리한다"),
        ("가야 하니까", "진행한다"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    if len(text) > 80:
        text = text[:77].rstrip() + "..."
    return text


def _guess_campaign(text: str) -> str | None:
    # This intentionally stays simple; preprocessing/LLM may improve it later.
    for marker in ["노바드림", "캠페인"]:
        if marker in text:
            return "노바드림" if marker == "노바드림" else None
    return None
