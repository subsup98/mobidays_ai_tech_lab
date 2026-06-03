"""캠페인/광고주별 반복 이슈 키워드를 BoW(Bag-of-Words) 방식으로 추출합니다.

과제 요건(위젯 3: "캠페인/광고주별 반복 이슈 키워드 (BoW 또는 임베딩
클러스터링)")에 맞춰, 액션 아이템을 (campaign, advertiser)로 그룹핑한 뒤
그룹별로 토큰을 Bag-of-Words로 집계해 반복 빈도가 높은 이슈 키워드를
추출한다.

키워드 유형은 세 가지다.
- domain : 도메인 용어집(GLOSSARY)에 등재된 핵심 용어 (BoW 어휘 사전 역할)
- risk_flag : 액션 아이템의 위험 플래그
- bigram : 인접 2단어 BoW 중 노이즈를 걸러낸 것

품질 정리 포인트:
- bigram은 불용어/길이 필터를 거치고, 그룹 내에서 2회 이상 반복된 것만
  채택해 우연한 1회성 조합을 제거한다.
- 이미 domain 용어로 잡힌 토큰이 포함된 bigram은 중복이므로 제외한다.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict

from db.sqlite_client import SQLiteClient
from models import stable_hash
from preprocessing.glossary import GLOSSARY


# 캠페인/광고주 미지정 시 사용하는 기본값 (NULL 대신 사용해 그룹 키를 안정화)
UNCLASSIFIED = "(미분류)"

# bigram 후보에서 제외할 불용어 — 동사/조사/일반 명사 등 의미 없는 토큰
STOPWORDS = {
    "한다",
    "진행한다",
    "확인한다",
    "정리한다",
    "관련",
    "회의",
    "작업",
    "후속",
    "내용",
    "부분",
    "정도",
    "예정",
    "필요",
    "진행",
    "확인",
    "정리",
    "공유",
    "검토",
    "이번",
    "다음",
    "오늘",
    "내일",
    "그리고",
    "또는",
    "위해",
    "대한",
    "통해",
    "있는",
    "없는",
    "해야",
}

# bigram을 채택하기 위한 그룹 내 최소 반복 횟수 (1회성 조합 제거)
MIN_BIGRAM_FREQUENCY = 2


def regenerate_issue_keywords(client: SQLiteClient, meeting_id: str) -> list[dict[str, object]]:
    action_items = client.fetch_all(
        """
        SELECT description, category, campaign_context, advertiser_context, risk_flags_json
        FROM action_items
        WHERE meeting_id = ?
        """,
        (meeting_id,),
    )
    rows = build_issue_keyword_rows(meeting_id, action_items)

    client.execute("DELETE FROM issue_keywords WHERE meeting_id = ?", (meeting_id,))
    # 매번 DELETE 후 INSERT이므로 충돌은 없지만, ON CONFLICT arbiter로는 항상
    # 유효한 PRIMARY KEY(issue_keyword_id)를 사용한다. issue_keyword_id는
    # (meeting_id, type, keyword, campaign, advertiser) 해시라 그룹별로 유일하다.
    client.upsert_many(
        "issue_keywords",
        rows,
        conflict_columns=["issue_keyword_id"],
        update_columns=["score", "frequency", "source_action_count"],
    )
    return rows


def build_issue_keyword_rows(
    meeting_id: str,
    action_items: list[dict[str, object]],
) -> list[dict[str, object]]:
    """캠페인/광고주별로 그룹핑한 뒤 그룹마다 BoW로 키워드를 집계합니다."""
    rows: list[dict[str, object]] = []
    for (campaign, advertiser), items in _group_by_context(action_items).items():
        rows.extend(_keywords_for_group(meeting_id, campaign, advertiser, items))
    rows.sort(key=lambda row: (-float(row["score"]), str(row["keyword"])))
    return rows


def _group_by_context(
    action_items: list[dict[str, object]],
) -> dict[tuple[str, str], list[dict[str, object]]]:
    groups: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for item in action_items:
        campaign = str(item.get("campaign_context") or UNCLASSIFIED)
        advertiser = str(item.get("advertiser_context") or UNCLASSIFIED)
        groups[(campaign, advertiser)].append(item)
    return groups


def _keywords_for_group(
    meeting_id: str,
    campaign: str,
    advertiser: str,
    items: list[dict[str, object]],
) -> list[dict[str, object]]:
    domain_counter: Counter[str] = Counter()
    bigram_counter: Counter[str] = Counter()
    risk_counter: Counter[str] = Counter()
    sources: dict[tuple[str, str], set[int]] = defaultdict(set)
    domain_terms: set[str] = set()

    # 1차 패스: domain / risk_flag BoW 집계 + domain 토큰 수집
    for index, item in enumerate(items):
        text = _item_text(item)

        item_domain = _domain_keywords(text)
        domain_terms.update(token for kw in item_domain for token in kw.lower().split())
        for keyword in item_domain:
            domain_counter[keyword] += 1
            sources[("domain", keyword)].add(index)

        for keyword in _risk_flags(item.get("risk_flags_json")):
            risk_counter[keyword] += 1
            sources[("risk_flag", keyword)].add(index)

    # 2차 패스: bigram BoW는 domain 토큰을 알아야 중복을 거를 수 있으므로 분리
    for index, item in enumerate(items):
        for keyword in _bigrams(_item_text(item), domain_terms):
            bigram_counter[keyword] += 1
            sources[("bigram", keyword)].add(index)

    # 우연한 1회성 bigram 제거
    bigram_counter = Counter(
        {kw: freq for kw, freq in bigram_counter.items() if freq >= MIN_BIGRAM_FREQUENCY}
    )

    rows: list[dict[str, object]] = []
    for ktype, counter in (
        ("domain", domain_counter),
        ("bigram", bigram_counter),
        ("risk_flag", risk_counter),
    ):
        for keyword, frequency in counter.items():
            source_count = len(sources[(ktype, keyword)])
            score = round(frequency * (1.0 + source_count * 0.2), 3)
            rows.append(
                {
                    "issue_keyword_id": stable_hash(
                        meeting_id, ktype, keyword, campaign, advertiser
                    ),
                    "meeting_id": meeting_id,
                    "keyword": keyword,
                    "keyword_type": ktype,
                    "score": score,
                    "frequency": frequency,
                    "source_action_count": source_count,
                    "campaign_context": campaign,
                    "advertiser_context": advertiser,
                }
            )
    return rows


def _item_text(item: dict[str, object]) -> str:
    return " ".join(
        str(item.get(field) or "")
        for field in ("description", "category", "campaign_context")
    )


def _domain_keywords(text: str) -> list[str]:
    normalized = text.lower()
    hits = []
    for term, (canonical, _) in GLOSSARY.items():
        if term in normalized:
            hits.append(canonical)
    # 동일 표준어 중복 제거
    return list(dict.fromkeys(hits))


def _bigrams(text: str, domain_terms: set[str]) -> list[str]:
    tokens = [
        token
        for token in re.findall(r"[0-9a-zA-Z가-힣]+", text.lower())
        if len(token) > 1 and token not in STOPWORDS
    ]
    bigrams = []
    for left, right in zip(tokens, tokens[1:]):
        # 이미 domain 용어로 잡힌 토큰이 들어간 조합은 중복이므로 제외
        if left in domain_terms or right in domain_terms:
            continue
        bigrams.append(f"{left} {right}")
    return bigrams


def _risk_flags(raw_value: object) -> list[str]:
    if not raw_value:
        return []
    if isinstance(raw_value, list):
        return [str(value) for value in raw_value]
    try:
        parsed = json.loads(str(raw_value))
    except json.JSONDecodeError:
        return [str(raw_value)]
    if isinstance(parsed, list):
        return [str(value) for value in parsed]
    return []
