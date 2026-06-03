from __future__ import annotations

import json
import re
from collections import Counter, defaultdict

from db.sqlite_client import SQLiteClient
from models import stable_hash
from preprocessing.glossary import GLOSSARY


STOPWORDS = {
    "한다",
    "진행한다",
    "확인한다",
    "정리한다",
    "관련",
    "회의",
    "작업",
    "후속",
}


def regenerate_issue_keywords(client: SQLiteClient, meeting_id: str) -> list[dict[str, object]]:
    action_items = client.fetch_all(
        """
        SELECT description, category, campaign_context, risk_flags_json
        FROM action_items
        WHERE meeting_id = ?
        """,
        (meeting_id,),
    )
    rows = build_issue_keyword_rows(meeting_id, action_items)

    client.execute("DELETE FROM issue_keywords WHERE meeting_id = ?", (meeting_id,))
    client.upsert_many(
        "issue_keywords",
        rows,
        conflict_columns=["meeting_id", "keyword", "keyword_type"],
        update_columns=["score", "frequency", "source_action_count"],
    )
    return rows


def build_issue_keyword_rows(
    meeting_id: str,
    action_items: list[dict[str, object]],
) -> list[dict[str, object]]:
    domain_counter: Counter[str] = Counter()
    bigram_counter: Counter[str] = Counter()
    risk_counter: Counter[str] = Counter()
    keyword_sources: dict[tuple[str, str], set[int]] = defaultdict(set)

    for index, item in enumerate(action_items):
        text_parts = [
            str(item.get("description") or ""),
            str(item.get("category") or ""),
            str(item.get("campaign_context") or ""),
        ]
        text = " ".join(text_parts)

        for keyword in _domain_keywords(text):
            domain_counter[keyword] += 1
            keyword_sources[("domain", keyword)].add(index)

        for keyword in _bigrams(text):
            bigram_counter[keyword] += 1
            keyword_sources[("bigram", keyword)].add(index)

        for keyword in _risk_flags(item.get("risk_flags_json")):
            risk_counter[keyword] += 1
            keyword_sources[("risk_flag", keyword)].add(index)

    rows = []
    rows.extend(_rows_from_counter(meeting_id, "domain", domain_counter, keyword_sources))
    rows.extend(_rows_from_counter(meeting_id, "bigram", bigram_counter, keyword_sources))
    rows.extend(_rows_from_counter(meeting_id, "risk_flag", risk_counter, keyword_sources))
    rows.sort(key=lambda row: (-float(row["score"]), str(row["keyword"])))
    return rows


def _domain_keywords(text: str) -> list[str]:
    normalized = text.lower()
    hits = []
    for term, (canonical, _) in GLOSSARY.items():
        if term in normalized:
            hits.append(canonical)
    return hits


def _bigrams(text: str) -> list[str]:
    tokens = [
        token
        for token in re.findall(r"[0-9a-zA-Z가-힣]+", text.lower())
        if len(token) > 1 and token not in STOPWORDS
    ]
    return [f"{left} {right}" for left, right in zip(tokens, tokens[1:])]


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


def _rows_from_counter(
    meeting_id: str,
    keyword_type: str,
    counter: Counter[str],
    keyword_sources: dict[tuple[str, str], set[int]],
) -> list[dict[str, object]]:
    rows = []
    for keyword, frequency in counter.items():
        source_count = len(keyword_sources[(keyword_type, keyword)])
        score = round(frequency * (1.0 + source_count * 0.2), 3)
        rows.append(
            {
                "issue_keyword_id": stable_hash(meeting_id, keyword_type, keyword),
                "meeting_id": meeting_id,
                "keyword": keyword,
                "keyword_type": keyword_type,
                "score": score,
                "frequency": frequency,
                "source_action_count": source_count,
            }
        )
    return rows
