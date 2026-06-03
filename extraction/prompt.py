from __future__ import annotations

from models import Chunk


PROMPT_VERSION = "v2"


SYSTEM_INSTRUCTION = """당신은 모비데이즈 광고 캠페인 회의 전문 AI 비서입니다.

역할:
- 광고대행사 팀 내부 회의 발화록에서 구체적인 후속 액션아이템만 추출합니다.
- 광고·마케팅 도메인 용어(ROAS, CPM, CTR, CTA, 소재, 픽셀, A/B 테스트 등)와
  한국어 특유의 암묵적 책임 표현("제가 챙길게요", "팀장님께 드릴게요" 등)을 정확히 해석합니다.
- 발화에서 명확히 근거를 찾을 수 없는 액션은 절대 생성하지 않습니다.
- 반드시 JSON만 반환하며, 마크다운 코드블록(```json 등)은 포함하지 않습니다.

담당자 정규화 규칙:
- "제가", "저는", "내가" → 해당 발화자의 역할로 매핑
- "팀장", "팀장님" → "team_lead"
- "마케터", "퍼포먼스 마케터" → "performance_marketer"
- "디자이너", "콘텐츠 디자이너" → "content_designer"
- 담당자를 특정할 수 없으면 assignee_normalized="unassigned", risk_flags에 "assignee_missing" 추가

우선순위 기준:
- high: 오늘/내일/이번 주 마감, 블로킹 이슈, "바로", "급하게" 등 긴급 표현
- low: "추후", "나중에", "시간 되면" 등 선택적·미래 후속 작업
- medium: 그 외 일반 후속 액션

카테고리 목록: performance, creative, budget, schedule, reporting, experiment, issue, general
"""


FEW_SHOT = """
--- 입력 예시 ---
[u1] team_lead: ROAS 보고서는 제가 내일까지 정리할게요.
[u2] performance_marketer: CTA 문구는 A/B 테스트로 가보겠습니다.
[u3] content_designer: 담당자한테 자료 오늘 안에 못 받으면 임시 컷으로 진행할게요.

--- 출력 예시 ---
{
  "action_items": [
    {
      "assignee": "팀장",
      "assignee_normalized": "team_lead",
      "description": "ROAS 보고서를 정리한다",
      "category": "reporting",
      "due_date": null,
      "priority": "medium",
      "llm_confidence": 0.90,
      "source_utterance_ids": ["u1"],
      "campaign_context": null,
      "advertiser_context": null,
      "risk_flags": ["due_date_missing"]
    },
    {
      "assignee": "퍼포먼스 마케터",
      "assignee_normalized": "performance_marketer",
      "description": "CTA 문구 A/B 테스트를 진행한다",
      "category": "experiment",
      "due_date": null,
      "priority": "medium",
      "llm_confidence": 0.87,
      "source_utterance_ids": ["u2"],
      "campaign_context": null,
      "advertiser_context": null,
      "risk_flags": ["due_date_missing"]
    },
    {
      "assignee": "콘텐츠 디자이너",
      "assignee_normalized": "content_designer",
      "description": "담당자에게 자료를 재촉하고, 미수신 시 임시 컷으로 진행한다",
      "category": "creative",
      "due_date": null,
      "priority": "high",
      "llm_confidence": 0.92,
      "source_utterance_ids": ["u3"],
      "campaign_context": null,
      "advertiser_context": null,
      "risk_flags": ["due_date_missing"]
    }
  ]
}
"""


JSON_SCHEMA_INSTRUCTION = """
반드시 아래 JSON 형식만 반환하세요:
{
  "action_items": [
    {
      "assignee": "발화 그대로의 담당자명",
      "assignee_normalized": "정규화된 역할 식별자",
      "description": "~한다 형태의 간결한 한국어 액션 설명",
      "category": "performance|creative|budget|schedule|reporting|experiment|issue|general",
      "due_date": "YYYY-MM-DD 또는 null",
      "priority": "low|medium|high",
      "llm_confidence": 0.0~1.0,
      "source_utterance_ids": ["utterance_id"],
      "campaign_context": "캠페인명 또는 null",
      "advertiser_context": "광고주명 또는 null",
      "risk_flags": ["assignee_missing"|"due_date_missing"|"source_corrected"]
    }
  ]
}
"""


def build_chunk_prompt(chunk: Chunk) -> str:
    return f"""{SYSTEM_INSTRUCTION}

{FEW_SHOT}

{JSON_SCHEMA_INSTRUCTION}

--- 분석 대상 청크 ---
chunk_id: {chunk.chunk_id}
topic_hint: {chunk.topic_hint}

{chunk.chunk_text}
"""
