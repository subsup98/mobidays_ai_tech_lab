"""PostgreSQL에 더미 회의 3건 + 액션아이템 삽입 스크립트."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv()

import os
from db.pg_client import DEFAULT_DSN, PostgreSQLClient
from models import stable_hash

dsn = os.getenv("DATABASE_URL") or os.getenv("PGDSN") or DEFAULT_DSN
print(f"접속 DB: {dsn}")
c = PostgreSQLClient(dsn=dsn)
c.init_schema()

# ──────────────────────────────────────────────────────────
# 더미 회의 3건
# ──────────────────────────────────────────────────────────
MEETINGS = [
    {
        "meeting_id": "meet_20260520_nova",
        "title": "데모 노바드림 5월 4주차 캠페인 리뷰",
        "meeting_date": "2026-05-20",
        "source_type": "mock",
    },
    {
        "meeting_id": "meet_20260527_nova",
        "title": "데모 노바드림 6월 소재 기획 회의",
        "meeting_date": "2026-05-27",
        "source_type": "mock",
    },
    {
        "meeting_id": "meet_20260603_hana",
        "title": "데모 하나투어 여름 시즌 성과 점검",
        "meeting_date": "2026-06-03",
        "source_type": "mock",
    },
]

# ──────────────────────────────────────────────────────────
# 더미 액션아이템 (회의별)
# ──────────────────────────────────────────────────────────
ACTIONS = {
    "meet_20260520_nova": [
        ("team_lead",           "team_lead",           "ROAS 보고서를 정리해 공유한다",                  "reporting",   "medium", "done",        0.88, 0.85, "노바드림", None),
        ("performance_marketer","performance_marketer","CTA 문구 A/B 테스트를 세팅한다",                  "experiment",  "high",   "done",        0.91, 0.80, "노바드림", None),
        ("content_designer",    "content_designer",    "픽셀 보장 내용을 다시 공유한다",                  "issue",       "high",   "in_progress", 0.85, 0.70, "노바드림", None),
        ("team_lead",           "team_lead",           "다음 캠페인 예산 시뮬레이션을 준비한다",           "budget",      "medium", "open",        0.80, 0.75, "노바드림", None),
        ("performance_marketer","performance_marketer","CPM 이슈 원인을 분석한다",                        "performance", "high",   "done",        0.90, 0.82, "노바드림", None),
    ],
    "meet_20260527_nova": [
        ("content_designer",    "content_designer",    "비주얼 카드 순서와 빈 슬롯 카피를 정리한다",       "creative",    "high",   "in_progress", 0.93, 0.88, "노바드림", None),
        ("team_lead",           "team_lead",           "지난 캠페인 인사이트로 비주얼 톤을 결정한다",       "creative",    "medium", "open",        0.86, 0.78, "노바드림", None),
        ("performance_marketer","performance_marketer","담당자에게 자료를 재촉하고 임시 컷으로 진행한다",   "issue",       "high",   "open",        0.92, 0.83, "노바드림", None),
        ("content_designer",    "content_designer",    "변경된 카피로 A/B 테스트를 다시 세팅한다",         "experiment",  "medium", "open",        0.84, 0.76, "노바드림", None),
        ("team_lead",           "team_lead",           "전환 수치를 보정하고 내일 오전 공유한다",           "reporting",   "high",   "blocked",     0.89, 0.80, "노바드림", None),
        ("performance_marketer","performance_marketer","헤드라인 소재 교체 후 CTR 재측정한다",              "performance", "medium", "open",        0.82, 0.72, "노바드림", None),
    ],
    "meet_20260603_hana": [
        ("team_lead",           "team_lead",           "여름 시즌 랜딩페이지 최종 검수한다",               "creative",    "high",   "open",        0.90, 0.85, None, "하나투어"),
        ("performance_marketer","performance_marketer","키워드 입찰가 조정 결과를 리포트한다",              "reporting",   "medium", "open",        0.87, 0.79, None, "하나투어"),
        ("content_designer",    "content_designer",    "배너 사이즈별 소재를 제작한다",                    "creative",    "medium", "in_progress", 0.85, 0.80, None, "하나투어"),
        ("team_lead",           "team_lead",           "6월 예산 집행 계획을 확정한다",                    "budget",      "high",   "open",        0.91, 0.87, None, "하나투어"),
    ],
}

KEYWORDS = {
    "meet_20260520_nova": [
        ("ROAS", "domain", 3.2, 4, 3),
        ("CPM", "domain", 2.8, 3, 2),
        ("A/B 테스트", "bigram", 2.5, 2, 2),
        ("픽셀 보장", "bigram", 2.1, 2, 1),
        ("due_date_missing", "risk_flag", 1.8, 5, 5),
    ],
    "meet_20260527_nova": [
        ("소재", "domain", 4.0, 5, 4),
        ("비주얼 카드", "bigram", 3.5, 3, 3),
        ("CTA 문구", "bigram", 3.0, 3, 3),
        ("A/B 테스트", "bigram", 2.8, 3, 3),
        ("assignee_missing", "risk_flag", 1.2, 1, 1),
    ],
    "meet_20260603_hana": [
        ("랜딩페이지", "domain", 3.0, 3, 2),
        ("키워드 입찰", "bigram", 2.5, 2, 2),
        ("예산 집행", "bigram", 2.2, 2, 2),
        ("배너 소재", "bigram", 2.0, 2, 1),
        ("due_date_missing", "risk_flag", 2.4, 4, 4),
    ],
}

# ──────────────────────────────────────────────────────────
# 삽입
# ──────────────────────────────────────────────────────────
for m in MEETINGS:
    c.upsert("meetings", {**m, "updated_at": "2026-06-03 00:00:00"},
             conflict_columns=["meeting_id"],
             update_columns=["title", "meeting_date", "source_type", "updated_at"])
    print(f"  회의 upsert: {m['title']}")

for mid, items in ACTIONS.items():
    for i, (assignee, assignee_n, desc, cat, pri, status, llm_c, val_c, campaign, advertiser) in enumerate(items, start=1):
        final_c = round(min(llm_c, val_c), 3)
        risk = ["due_date_missing"]
        if assignee_n == "unassigned":
            risk.append("assignee_missing")
        item_id = stable_hash("action", mid, str(i))
        dedup   = stable_hash("dedup",  mid, assignee_n, cat, desc[:30])
        row = {
            "action_item_id":           item_id,
            "dedup_key":                dedup,
            "meeting_id":               mid,
            "chunk_id":                 None,
            "extraction_run_id":        None,
            "sequence_no":              i,
            "assignee":                 assignee,
            "assignee_normalized":      assignee_n,
            "description":              desc,
            "normalized_task_signature": desc[:50].lower(),
            "category":                 cat,
            "due_date":                 None,
            "priority":                 pri,
            "status":                   status,
            "llm_confidence":           llm_c,
            "validation_score":         val_c,
            "final_confidence":         final_c,
            "review_required":          1 if final_c < 0.7 else 0,
            "risk_flags_json":          str(risk).replace("'", '"'),
            "campaign_context":         campaign,
            "advertiser_context":       advertiser,
            "updated_at":               "2026-06-03 00:00:00",
        }
        c.upsert_action_item(row)
    print(f"  액션아이템 upsert: {mid} ({len(items)}건)")

for mid, kws in KEYWORDS.items():
    for keyword, ktype, score, freq, src_cnt in kws:
        kid = stable_hash("kw", mid, ktype, keyword)
        c.upsert(
            "issue_keywords",
            {
                "issue_keyword_id":    kid,
                "meeting_id":          mid,
                "keyword":             keyword,
                "keyword_type":        ktype,
                "score":               score,
                "frequency":           freq,
                "source_action_count": src_cnt,
            },
            conflict_columns=["meeting_id", "keyword", "keyword_type"],
            update_columns=["score", "frequency", "source_action_count"],
        )
    print(f"  키워드 upsert: {mid} ({len(kws)}건)")

c.close()
print("\n완료: 더미 회의 3건 삽입됨")
