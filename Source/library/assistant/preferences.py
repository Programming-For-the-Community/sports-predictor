"""
The user's answer preferences ("tables for numbers", "keep it short"), kept in
AgentCore Memory long-term records: the memory's user-preference strategy
(Terraform/agentcore-assistant.tf) reads the turn events history.save writes
and extracts them in the background under PREFERENCE_NAMESPACE, one namespace
per Cognito sub. Favourite teams stay in DynamoDB (user_store) -- exact values
the app shows and removes; preferences are free-form and only the agent reads
them.

Reads are best-effort: a failure leaves the answer without preferences.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# The strategy's namespace template; {actorId} is the sub history.save writes under.
PREFERENCE_NAMESPACE = "/preferences/{actorId}"
MAX_PREFERENCES = 20
DELETE_BATCH = 100


def namespace(sub: str) -> str:
    return PREFERENCE_NAMESPACE.replace("{actorId}", sub)


def _records(memory, memory_id: str, sub: str, limit: int | None) -> list[dict]:
    kwargs = {"memoryId": memory_id, "namespace": namespace(sub), "maxResults": min(limit or DELETE_BATCH, DELETE_BATCH)}
    records: list[dict] = []
    while True:
        page = memory.list_memory_records(**kwargs)
        records.extend(page.get("memoryRecordSummaries", []))
        if not page.get("nextToken") or (limit and len(records) >= limit):
            return records[:limit] if limit else records
        kwargs["nextToken"] = page["nextToken"]


def load(memory, memory_id: str, sub: str) -> list[str]:
    """The user's preference texts, newest first."""
    try:
        records = _records(memory, memory_id, sub, MAX_PREFERENCES)
    except Exception:
        logger.exception("Preferences not read")
        return []
    records.sort(key=lambda r: str(r.get("createdAt", "")), reverse=True)
    return [text for r in records if (text := ((r.get("content") or {}).get("text") or "").strip())]


def forget(memory, memory_id: str, sub: str) -> int:
    """Deletes every preference record of the user; returns how many."""
    ids = [r["memoryRecordId"] for r in _records(memory, memory_id, sub, None) if r.get("memoryRecordId")]
    for start in range(0, len(ids), DELETE_BATCH):
        memory.batch_delete_memory_records(memoryId=memory_id, records=[{"memoryRecordId": i} for i in ids[start:start + DELETE_BATCH]])
    return len(ids)
