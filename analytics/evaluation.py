"""Evaluation metrics: gold label P/R/F1 and always-on proxy metrics."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from db.sqlite_client import SQLiteClient
from models import normalized_task_signature


class GoldActionItem(BaseModel):
    description: str
    assignee_normalized: str = "unassigned"
    category: str = "uncategorized"
    due_date: str | None = None
    priority: str = "medium"


class GoldSample(BaseModel):
    meeting_id: str
    gold_action_items: list[GoldActionItem] = Field(default_factory=list)


class CategoryMetrics(BaseModel):
    precision: float
    recall: float
    f1: float
    predicted_count: int
    gold_count: int


class EvaluationReport(BaseModel):
    meeting_id: str
    has_gold: bool
    # Gold-label metrics — populated only when has_gold is True
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None
    assignee_accuracy: float | None = None
    category_accuracy: float | None = None
    per_category: dict[str, CategoryMetrics] | None = None
    matched_pairs: list[dict] | None = None  # for dashboard display
    # Proxy metrics — always populated
    proxy: dict[str, float] = Field(default_factory=dict)


@dataclass
class _MatchedPair:
    pred_idx: int
    gold_idx: int
    similarity: float
    assignee_match: bool
    category_match: bool


def load_gold_sample(path: str | Path) -> GoldSample:
    return GoldSample.model_validate(
        json.loads(Path(path).read_text(encoding="utf-8"))
    )


def _sig_jaccard(sig_a: str, sig_b: str) -> float:
    tokens_a = set(sig_a.split(":")) - {""}
    tokens_b = set(sig_b.split(":")) - {""}
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def _match(
    predicted: list[dict],
    gold_items: list[GoldActionItem],
    threshold: float = 0.5,
) -> list[_MatchedPair]:
    """Greedy best-match: each gold item matched at most once."""
    gold_sigs = [normalized_task_signature(g.description) for g in gold_items]
    used_gold = [False] * len(gold_items)
    pairs: list[_MatchedPair] = []

    for pred_idx, pred in enumerate(predicted):
        pred_sig = pred.get("normalized_task_signature") or normalized_task_signature(
            pred.get("description", "")
        )
        best_sim, best_idx = 0.0, -1
        for gold_idx, gsig in enumerate(gold_sigs):
            if used_gold[gold_idx]:
                continue
            sim = _sig_jaccard(pred_sig, gsig)
            if sim > best_sim:
                best_sim, best_idx = sim, gold_idx

        if best_idx >= 0 and best_sim >= threshold:
            used_gold[best_idx] = True
            g = gold_items[best_idx]
            pairs.append(
                _MatchedPair(
                    pred_idx=pred_idx,
                    gold_idx=best_idx,
                    similarity=round(best_sim, 3),
                    assignee_match=pred.get("assignee_normalized") == g.assignee_normalized,
                    category_match=pred.get("category") == g.category,
                )
            )

    return pairs


def _prf(tp: int, total_predicted: int, total_gold: int) -> tuple[float, float, float]:
    precision = tp / total_predicted if total_predicted else 0.0
    recall = tp / total_gold if total_gold else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall > 0
        else 0.0
    )
    return round(precision, 3), round(recall, 3), round(f1, 3)


def _per_category(
    predicted: list[dict],
    gold_items: list[GoldActionItem],
    pairs: list[_MatchedPair],
) -> dict[str, CategoryMetrics]:
    categories = {p.get("category", "uncategorized") for p in predicted} | {
        g.category for g in gold_items
    }
    result: dict[str, CategoryMetrics] = {}

    for cat in sorted(categories):
        cat_pred_indices = {
            i for i, p in enumerate(predicted) if p.get("category") == cat
        }
        cat_gold_count = sum(1 for g in gold_items if g.category == cat)
        tp = sum(
            1
            for pair in pairs
            if pair.pred_idx in cat_pred_indices
            # the gold item must also be in this category
            and gold_items[pair.gold_idx].category == cat
        )
        prec, rec, f1 = _prf(tp, len(cat_pred_indices), cat_gold_count)
        result[cat] = CategoryMetrics(
            precision=prec,
            recall=rec,
            f1=f1,
            predicted_count=len(cat_pred_indices),
            gold_count=cat_gold_count,
        )

    return result


def proxy_metrics(client: SQLiteClient, meeting_id: str | None = None) -> dict[str, float]:
    where = "WHERE meeting_id = ?" if meeting_id else ""
    params = (meeting_id,) if meeting_id else ()

    row = client.fetch_one(
        f"""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN assignee_normalized != 'unassigned' THEN 1 ELSE 0 END) AS with_assignee,
            SUM(CASE WHEN due_date IS NOT NULL THEN 1 ELSE 0 END) AS with_due_date,
            SUM(CASE WHEN category != 'uncategorized' THEN 1 ELSE 0 END) AS with_category,
            AVG(llm_confidence - validation_score) AS avg_confidence_gap,
            SUM(review_required) AS review_count
        FROM action_items
        {where}
        """,
        params,
    )

    if not row or not row.get("total"):
        return {"total": 0.0}

    total = float(row["total"])
    risk_rows = client.fetch_all(
        f"SELECT risk_flags_json FROM action_items {where}", params
    )
    flag_counts: dict[str, int] = {}
    for r in risk_rows:
        try:
            flags = json.loads(r.get("risk_flags_json") or "[]")
        except Exception:
            flags = []
        for flag in flags:
            flag_counts[flag] = flag_counts.get(flag, 0) + 1

    result: dict[str, float] = {
        "total": total,
        "assignee_fill_rate": round(float(row["with_assignee"] or 0) / total, 3),
        "due_date_fill_rate": round(float(row["with_due_date"] or 0) / total, 3),
        "category_fill_rate": round(float(row["with_category"] or 0) / total, 3),
        "review_rate": round(float(row["review_count"] or 0) / total, 3),
        "avg_confidence_gap": round(float(row["avg_confidence_gap"] or 0), 3),
    }
    for flag, count in flag_counts.items():
        result[f"risk_{flag}"] = round(count / total, 3)
    return result


def full_evaluation_report(
    client: SQLiteClient,
    meeting_id: str,
    gold_path: str | Path | None = None,
) -> EvaluationReport:
    predicted = client.list_action_items(meeting_id)
    proxies = proxy_metrics(client, meeting_id)

    gold_file = Path(str(gold_path)) if gold_path else None
    if not gold_file or not gold_file.exists():
        return EvaluationReport(meeting_id=meeting_id, has_gold=False, proxy=proxies)

    gold_sample = load_gold_sample(gold_file)
    pairs = _match(predicted, gold_sample.gold_action_items)
    precision, recall, f1 = _prf(
        tp=len(pairs),
        total_predicted=len(predicted),
        total_gold=len(gold_sample.gold_action_items),
    )
    assignee_acc = (
        round(sum(1 for p in pairs if p.assignee_match) / len(pairs), 3) if pairs else 0.0
    )
    category_acc = (
        round(sum(1 for p in pairs if p.category_match) / len(pairs), 3) if pairs else 0.0
    )

    matched_pairs_display = [
        {
            "predicted": predicted[p.pred_idx].get("description", ""),
            "gold": gold_sample.gold_action_items[p.gold_idx].description,
            "similarity": p.similarity,
            "assignee_match": p.assignee_match,
            "category_match": p.category_match,
        }
        for p in pairs
    ]

    return EvaluationReport(
        meeting_id=meeting_id,
        has_gold=True,
        precision=precision,
        recall=recall,
        f1=f1,
        assignee_accuracy=assignee_acc,
        category_accuracy=category_acc,
        per_category=_per_category(predicted, gold_sample.gold_action_items, pairs),
        matched_pairs=matched_pairs_display,
        proxy=proxies,
    )
