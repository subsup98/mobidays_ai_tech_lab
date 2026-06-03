from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GlossaryHit:
    term: str
    canonical: str
    category: str


GLOSSARY: dict[str, tuple[str, str]] = {
    "roas": ("ROAS", "performance"),
    "cpm": ("CPM", "performance"),
    "ctr": ("CTR", "performance"),
    "cpc": ("CPC", "performance"),
    "cta": ("CTA", "creative"),
    "a/b": ("A/B", "experiment"),
    "ab": ("A/B", "experiment"),
    "소재": ("creative", "creative"),
    "배너": ("creative", "creative"),
    "카피": ("copy", "creative"),
    "문구": ("copy", "creative"),
    "랜딩": ("landing", "conversion"),
    "전환": ("conversion", "conversion"),
    "예산": ("budget", "budget"),
    "비용": ("cost", "budget"),
    "리포트": ("report", "reporting"),
    "보고서": ("report", "reporting"),
}


def find_glossary_hits(text: str) -> list[GlossaryHit]:
    normalized = text.lower()
    hits: list[GlossaryHit] = []
    for term, (canonical, category) in GLOSSARY.items():
        if term in normalized:
            hits.append(GlossaryHit(term=term, canonical=canonical, category=category))
    return hits


def infer_topic_hint(text: str) -> str:
    hits = find_glossary_hits(text)
    if hits:
        category_counts: dict[str, int] = {}
        for hit in hits:
            category_counts[hit.category] = category_counts.get(hit.category, 0) + 1
        return sorted(category_counts.items(), key=lambda item: (-item[1], item[0]))[0][0]

    fallback_rules = {
        "schedule": ["일정", "기한", "언제", "내일", "이번 주", "다음 주"],
        "owner": ["담당", "제가", "맡", "챙길게", "부탁"],
        "issue": ["문제", "이슈", "누락", "리스크", "어렵"],
    }
    for topic, keywords in fallback_rules.items():
        if any(keyword in text for keyword in keywords):
            return topic
    return "general"
