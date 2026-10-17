"""Old task IDs must remain findable/cancellable after newer tasks arrive."""
from core.orchestration.agent_contract import TaskContext
from core.orchestration.shared_task_junction import SharedTaskJunction
from core.storage.sqlite_store import AegisSQLiteStore


def test_exact_lookup_filters_before_limit(tmp_path):
    store = AegisSQLiteStore(str(tmp_path / "lookup.db"))
    store.insert("tasks", {"task_id": "older", "_created_at": 1})
    for number in range(10):
        store.insert("tasks", {"task_id": str(number), "_created_at": number + 2})
    assert store.find("tasks", {"task_id": "older"}, limit=1)[0]["task_id"] == "older"


def test_old_task_can_be_cancelled_and_upsert_does_not_duplicate(tmp_path):
    store = AegisSQLiteStore(str(tmp_path / "junction.db"))
    junction = SharedTaskJunction(store)
    junction.start(TaskContext(task_id="older", goal="Local desktop task"), "worker")
    for number in range(10):
        junction.start(TaskContext(task_id=str(number), goal="Other task"), "worker")
    result = junction.cancel("older", "Stop before any further input")
    assert result and result["status"] == "CANCEL_REQUESTED"
    assert store.count("tasks") == 11
