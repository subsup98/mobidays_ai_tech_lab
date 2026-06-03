"""액션 아이템 설명에서 상대 시간 표현을 절대 날짜로 환산합니다.

회의록에는 "내일 오전", "이번 주 수요일", "오늘 안에"처럼 상대 표현이
대부분이라, 회의 날짜(meeting_date)를 기준일("오늘")로 삼아 절대 날짜를
계산합니다. README 가정 사항의 "상대 기한 해석" 항목과 일치합니다.

기준일(meeting_date)을 모르면 절대 날짜를 만들 수 없으므로 None을 반환합니다.
"""
from __future__ import annotations

import re
from datetime import date, timedelta


# 요일 이름 → weekday() 인덱스 (월=0 ... 일=6)
_WEEKDAYS = {
    "월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6,
}

# "N일 뒤/후" 류
_REL_DAYS_PATTERN = re.compile(r"(\d+)\s*일\s*(뒤|후|내)")


def parse_due_date(description: str, meeting_date: date | None) -> date | None:
    """description에서 상대 시간 표현을 찾아 meeting_date 기준 절대 날짜로 환산.

    매칭되는 표현이 없거나 기준일이 없으면 None.
    """
    if meeting_date is None or not description:
        return None

    text = description

    # 1) 직접 지정: 오늘 / 내일 / 모레 / 글피
    if "모레" in text:
        return meeting_date + timedelta(days=2)
    if "글피" in text:
        return meeting_date + timedelta(days=3)
    if "내일" in text:
        return meeting_date + timedelta(days=1)
    if "오늘" in text or "금일" in text:
        return meeting_date

    # 2) "N일 뒤/후/내"
    rel = _REL_DAYS_PATTERN.search(text)
    if rel:
        return meeting_date + timedelta(days=int(rel.group(1)))

    # 3) 요일 표현: "(이번 주|다음 주)? X요일"
    weekday = _parse_weekday(text, meeting_date)
    if weekday is not None:
        return weekday

    # 4) 주 단위: "이번 주" = 그 주 금요일, "다음 주" = 다음 주 금요일
    if "다음 주" in text or "담주" in text:
        return _end_of_week(meeting_date + timedelta(days=7))
    if "이번 주" in text or "금주" in text:
        return _end_of_week(meeting_date)

    return None


def _parse_weekday(text: str, base: date) -> date | None:
    """'수요일', '이번 주 금요일', '다음 주 월요일' 등을 절대 날짜로."""
    match = re.search(r"(다음\s*주|이번\s*주|담주|금주)?\s*([월화수목금토일])\s*요일", text)
    if not match:
        return None

    target = _WEEKDAYS[match.group(2)]
    prefix = (match.group(1) or "").replace(" ", "")

    # 기준 주의 해당 요일
    days_ahead = (target - base.weekday()) % 7
    candidate = base + timedelta(days=days_ahead)

    if prefix in ("다음주", "담주"):
        candidate += timedelta(days=7)
    elif prefix in ("이번주", "금주"):
        pass  # 이번 주 해당 요일 (이미 지난 요일이면 같은 주로 본다: days_ahead=0 처리)
    else:
        # 접두사 없는 단순 요일: 회의일 이후 가장 가까운 해당 요일
        if days_ahead == 0:
            candidate = base  # 회의 당일을 의미한다고 본다

    return candidate


def _end_of_week(d: date) -> date:
    """해당 주의 금요일(업무 마감 기준)을 반환."""
    return d + timedelta(days=(4 - d.weekday()))
