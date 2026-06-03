from __future__ import annotations

from db.sqlite_client import SQLiteClient


def confidence_summary(client: SQLiteClient, meeting_id: str | None = None) -> dict[str, float]:
    where_sql = "WHERE meeting_id = ?" if meeting_id else ""
    params = (meeting_id,) if meeting_id else ()
    row = client.fetch_one(
        f"""
        SELECT
            COUNT(*) AS total,
            AVG(llm_confidence) AS avg_llm_confidence,
            AVG(validation_score) AS avg_validation_score,
            AVG(final_confidence) AS avg_final_confidence,
            SUM(review_required) AS review_required_count
        FROM action_items
        {where_sql}
        """,
        params,
    )
    return {
        "total": float(row["total"] or 0),
        "avg_llm_confidence": round(float(row["avg_llm_confidence"] or 0), 3),
        "avg_validation_score": round(float(row["avg_validation_score"] or 0), 3),
        "avg_final_confidence": round(float(row["avg_final_confidence"] or 0), 3),
        "review_required_count": float(row["review_required_count"] or 0),
    }


def validation_mismatches(
    client: SQLiteClient,
    meeting_id: str | None = None,
    min_gap: float = 0.2,
) -> list[dict[str, object]]:
    params: list[object] = [min_gap]
    meeting_clause = ""
    if meeting_id:
        meeting_clause = "AND meeting_id = ?"
        params.append(meeting_id)

    return client.fetch_all(
        f"""
        SELECT
            action_item_id,
            meeting_id,
            assignee_normalized,
            description,
            llm_confidence,
            validation_score,
            final_confidence,
            review_required,
            risk_flags_json
        FROM action_items
        WHERE (llm_confidence - validation_score) >= ?
        {meeting_clause}
        ORDER BY (llm_confidence - validation_score) DESC
        """,
        tuple(params),
    )


def low_confidence_items(
    client: SQLiteClient,
    meeting_id: str | None = None,
    threshold: float = 0.7,
) -> list[dict[str, object]]:
    params: list[object] = [threshold]
    meeting_clause = ""
    if meeting_id:
        meeting_clause = "AND meeting_id = ?"
        params.append(meeting_id)

    return client.fetch_all(
        f"""
        SELECT *
        FROM action_items
        WHERE final_confidence < ?
        {meeting_clause}
        ORDER BY final_confidence ASC
        """,
        tuple(params),
    )
