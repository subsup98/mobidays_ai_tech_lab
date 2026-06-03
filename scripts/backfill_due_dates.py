"""기존 action_items의 due_date를 회의 날짜 기준으로 백필합니다.

LLM/시드 단계에서 due_date를 비워둔 채 'due_date_missing' 플래그가 붙은
액션 아이템 중, 설명에 상대 시간 표현("내일 오전", "수요일" 등)이 있는
것을 회의 날짜 기준 절대 날짜로 채우고 'due_date_missing' 플래그를 제거한다.

README "상대 기한 해석" 가정과 동일한 규칙(extraction.due_date_parser)을 쓴다.
회의 날짜가 없으면 "회의일=오늘"로 간주한다.

사용법:
    python -m scripts.backfill_due_dates                 # 기본 DB(.env)
    python -m scripts.backfill_due_dates --dry-run       # 변경 없이 미리보기
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime

from dotenv import load_dotenv

from extraction.due_date_parser import parse_due_date


def _build_client():
    load_dotenv()
    backend = os.getenv("DB_BACKEND", "postgres").lower()
    if backend == "postgres":
        from db.pg_client import DEFAULT_DSN, PostgreSQLClient

        return PostgreSQLClient(os.getenv("DATABASE_URL") or DEFAULT_DSN)
    from db.sqlite_client import SQLiteClient

    return SQLiteClient(os.getenv("DATABASE_PATH", "data/app_quality.db"))


def _parse_meeting_date(raw: object) -> date | None:
    if not raw:
        return None
    if isinstance(raw, date):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    try:
        return datetime.fromisoformat(str(raw)[:10]).date()
    except ValueError:
        return None


def backfill(dry_run: bool = False) -> dict[str, int]:
    client = _build_client()
    # init_schema는 호출하지 않는다: 컬럼은 이미 존재하며, 실행 중인 대시보드
    # 커넥션과 ALTER TABLE 락이 경합해 무한 대기에 빠질 수 있다.
    # 모든 액션 아이템을 스캔한다. due_date가 비면 설명에서 회의일 기준으로
    # 파싱해 채우고, 채워진 경우 due_date_missing을 제거한다. 그리고 위험
    # 신호와 최종 신뢰도를 기준으로 review_required를 항상 재계산해, 과거에
    # due_date_missing 때문에 검토 필요로 표시됐던 항목을 바로잡는다.
    rows = client.fetch_all(
        """
        SELECT a.action_item_id, a.description, a.risk_flags_json,
               a.validation_score, a.llm_confidence, a.due_date,
               a.review_required, m.meeting_date
        FROM action_items a
        JOIN meetings m ON m.meeting_id = a.meeting_id
        """
    )

    scanned = len(rows)
    updated = 0
    for row in rows:
        # 설명에서 회의일 기준으로 기한을 새로 파싱할 수 있는 행만 대상.
        # 기존에 due_date가 이미 있거나 시간 표현이 없으면 건드리지 않는다.
        if row["due_date"] is not None:
            continue
        base = _parse_meeting_date(row["meeting_date"]) or date.today()
        due = parse_due_date(str(row["description"] or ""), base)
        if due is None:
            continue
        due_iso = due.isoformat()

        # 기한을 채웠으니 due_date_missing은 모순 → 제거
        flags = _load_flags(row["risk_flags_json"])
        had_due_missing = "due_date_missing" in flags
        flags = [f for f in flags if f != "due_date_missing"]

        # due_date_missing 감점(-0.1) 해제 후 점수 재계산(models.py와 동일).
        validation_score = float(row["validation_score"] or 0.0)
        if had_due_missing:
            validation_score = round(min(1.0, validation_score + 0.1), 3)
        llm_confidence = float(row["llm_confidence"] or 0.0)
        final_confidence = round(min(llm_confidence, validation_score), 3)
        # 검토 필요는 (최종 신뢰도 < 0.7) 또는 (남은 위험 신호 존재)일 때만.
        # 기한이 유일한 검토 사유였다면 이 행은 0으로 내려간다.
        review_required = 1 if (final_confidence < 0.7 or flags) else 0

        print(
            f"  {row['action_item_id'][:12]} → due={due_iso} "
            f"review_required={review_required} | {str(row['description'])[:34]}"
        )
        updated += 1

        if not dry_run:
            client.execute(
                """
                UPDATE action_items
                SET due_date = ?, risk_flags_json = ?, validation_score = ?,
                    final_confidence = ?, review_required = ?
                WHERE action_item_id = ?
                """,
                (
                    due_iso,
                    json.dumps(flags, ensure_ascii=False),
                    validation_score,
                    final_confidence,
                    review_required,
                    row["action_item_id"],
                ),
            )

    print(
        f"\n{'[DRY-RUN] ' if dry_run else ''}스캔 {scanned}건 중 {updated}건 "
        f"갱신(due_date/검토 필요){' 예정' if dry_run else ' 완료'}."
    )
    return {"scanned": scanned, "updated": updated}


def _load_flags(raw: object) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return []
    return [str(x) for x in parsed] if isinstance(parsed, list) else []


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="action_items due_date 백필")
    parser.add_argument("--dry-run", action="store_true", help="변경 없이 미리보기")
    args = parser.parse_args()
    backfill(dry_run=args.dry_run)
