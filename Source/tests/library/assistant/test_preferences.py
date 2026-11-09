"""Answer preferences in AgentCore Memory long-term records, per user."""
from library.assistant import preferences


class PagedMemory:
    def __init__(self, pages):
        self.pages = pages
        self.asked = []
        self.deleted = []

    def list_memory_records(self, **kwargs):
        self.asked.append(kwargs)
        return self.pages[len(self.asked) - 1]

    def batch_delete_memory_records(self, memoryId, records):
        self.deleted.append([r["memoryRecordId"] for r in records])


def _record(record_id, text="Prefers tables", created="2026-11-09"):
    return {"memoryRecordId": record_id, "content": {"text": text}, "createdAt": created}


def test_each_user_has_their_own_namespace():
    assert preferences.namespace("abc-123") == "/preferences/abc-123"


def test_load_reads_every_page_newest_first_and_skips_empty_text():
    memory = PagedMemory([
        {"memoryRecordSummaries": [_record("1", "Old one", "2026-11-01"), _record("2", " ")], "nextToken": "t"},
        {"memoryRecordSummaries": [_record("3", "New one", "2026-11-09")]},
    ])

    assert preferences.load(memory, "m", "abc") == ["New one", "Old one"]
    assert memory.asked[0]["namespace"] == "/preferences/abc"
    assert memory.asked[1]["nextToken"] == "t"


def test_a_failed_read_leaves_no_preferences():
    class Broken:
        def list_memory_records(self, **kwargs):
            raise RuntimeError("throttled")

    assert preferences.load(Broken(), "m", "abc") == []


def test_forget_deletes_every_record_in_batches():
    memory = PagedMemory([{"memoryRecordSummaries": [_record(str(i)) for i in range(150)]}])
    assert preferences.forget(memory, "m", "abc") == 150
    assert [len(batch) for batch in memory.deleted] == [100, 50]
