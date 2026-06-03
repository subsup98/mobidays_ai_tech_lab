"""회의 발화록을 읽고 안건·결정사항·요약을 생성합니다."""
from __future__ import annotations

import json
import os

from models import Transcript, stable_hash


SUMMARY_PROMPT = """\
당신은 퍼포먼스 마케팅 에이전시 회의 전문 AI 비서입니다.

아래 발화록을 읽고 회의록을 작성하세요.
- agenda: 이번 회의에서 논의된 주요 안건 목록 (3~6개, 간결한 명사형)
- decisions: 회의에서 확정된 결정 사항 목록 (구체적일수록 좋음)
- summary: 회의 전체 흐름을 3~5문장으로 요약

반드시 아래 JSON 형식만 반환하고, 마크다운 코드블록은 포함하지 마세요.
{
  "agenda": ["안건1", "안건2"],
  "decisions": ["결정1", "결정2"],
  "summary": "요약 문장"
}

--- 발화록 ---
"""


def build_transcript_text(transcript: Transcript) -> str:
    lines = []
    for u in sorted(transcript.utterances, key=lambda x: x.sequence_no):
        speaker = u.speaker_normalized or u.speaker_raw
        lines.append(f"[{u.sequence_no}] {speaker}: {u.text}")
    return "\n".join(lines)


class GeminiSummarizer:
    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
    ) -> None:
        from google import genai

        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model_name = model_name or os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is required for GeminiSummarizer")
        self.client = genai.Client(api_key=self.api_key)

    def summarize(self, transcript: Transcript) -> dict:
        transcript_text = build_transcript_text(transcript)
        prompt = SUMMARY_PROMPT + transcript_text
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=prompt,
        )
        raw = getattr(response, "text", "") or ""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # 마크다운 코드블록 제거 후 재시도
            cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            data = json.loads(cleaned)

        return {
            "agenda": data.get("agenda") or [],
            "decisions": data.get("decisions") or [],
            "summary": data.get("summary") or "",
            "provider": "gemini",
            "model_name": self.model_name,
        }


class MockSummarizer:
    def summarize(self, transcript: Transcript) -> dict:
        speakers = sorted({u.speaker_normalized or u.speaker_raw for u in transcript.utterances})
        n_utterances = len(transcript.utterances)
        return {
            "agenda": [
                "캠페인 성과 및 수치 확인",
                "채널별 예산 배분 방향 결정",
                "광고 소재 및 카피 톤 조정",
                "후속 액션아이템 담당자 지정",
            ],
            "decisions": [
                f"참여자 {len(speakers)}명이 총 {n_utterances}개 발화로 회의를 진행함",
                "주요 후속 작업은 액션 운영 탭에서 확인 가능",
            ],
            "summary": (
                f"이 회의는 {', '.join(speakers)} 등 {len(speakers)}명이 참여하여 "
                f"총 {n_utterances}개 발화로 진행됐습니다. "
                "캠페인 관련 안건을 논의하고 담당자별 후속 액션을 정리했습니다. "
                "자세한 내용은 STT 검토 탭의 발화 원문을 참조하세요."
            ),
            "provider": "mock",
            "model_name": "mock-summary-v1",
        }


def build_summarizer():
    provider = os.getenv("LLM_PROVIDER", "mock").lower()
    if provider == "gemini":
        try:
            return GeminiSummarizer()
        except Exception:
            pass
    return MockSummarizer()


def summarize_and_store(client, meeting_id: str, transcript: Transcript) -> None:
    summarizer = build_summarizer()
    try:
        result = summarizer.summarize(transcript)
    except Exception as exc:
        result = MockSummarizer().summarize(transcript)
        result["summary"] = f"[자동 요약 실패: {exc}] " + result["summary"]

    summary_id = stable_hash("summary", meeting_id)
    client.upsert(
        "meeting_summaries",
        {
            "summary_id": summary_id,
            "meeting_id": meeting_id,
            "agenda_json": json.dumps(result["agenda"], ensure_ascii=False),
            "decisions_json": json.dumps(result["decisions"], ensure_ascii=False),
            "summary_text": result["summary"],
            "provider": result["provider"],
            "model_name": result["model_name"],
        },
        conflict_columns=["meeting_id"],
        update_columns=["agenda_json", "decisions_json", "summary_text", "provider", "model_name"],
    )
