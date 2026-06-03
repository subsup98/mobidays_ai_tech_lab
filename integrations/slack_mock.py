from __future__ import annotations

import json
from typing import Any

from db.sqlite_client import SQLiteClient
from models import stable_hash


def build_slack_payload(action_item: dict[str, Any]) -> dict[str, Any]:
    review_label = "REVIEW REQUIRED" if action_item.get("review_required") else "OK"
    due_date = action_item.get("due_date") or "no due date"
    priority = action_item.get("priority") or "medium"

    return {
        "channel": "#campaign-actions",
        "username": "Mobidays Action Bot",
        "text": f"[{review_label}] {action_item.get('description')}",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Action*: {action_item.get('description')}\n"
                        f"*Assignee*: {action_item.get('assignee_normalized')}\n"
                        f"*Due*: {due_date}\n"
                        f"*Priority*: {priority}\n"
                        f"*Confidence*: {action_item.get('final_confidence')}"
                    ),
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"action_item_id={action_item.get('action_item_id')} | "
                            f"review_required={bool(action_item.get('review_required'))}"
                        ),
                    }
                ],
            },
        ],
    }


def generate_and_store_payloads(
    client: SQLiteClient,
    meeting_id: str | None = None,
) -> list[dict[str, Any]]:
    action_items = client.list_action_items(meeting_id)
    payload_rows = []

    for action_item in action_items:
        payload = build_slack_payload(action_item)
        payload_rows.append(
            {
                "payload_id": stable_hash("slack", action_item["action_item_id"]),
                "action_item_id": action_item["action_item_id"],
                "payload_json": json.dumps(payload, ensure_ascii=False),
                "sent_mock": 1,
            }
        )

    client.upsert_many(
        "slack_payloads",
        payload_rows,
        conflict_columns=["payload_id"],
        update_columns=["payload_json", "sent_mock"],
    )
    return payload_rows
