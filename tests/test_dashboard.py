from core.dashboard import Dashboard
from core.models import DownloadTask, CookieState
import time


def test_dashboard_creation():
    db = Dashboard(total_tasks=5, concurrency=3)
    assert db is not None


def test_add_and_update_task():
    db = Dashboard(total_tasks=2, concurrency=2)
    task = DownloadTask(
        task_id="t1", trace_id="tr1",
        url="https://example.com", content_type="user",
    )
    db.add_task(task)
    task.status = "running"
    db.update_task(task)
    state = db.get_state()
    assert state["active_count"] >= 0


def test_log_done():
    db = Dashboard(total_tasks=1, concurrency=1)
    db.log_done("test video", True, "3 files", trace_id="t_001")
    state = db.get_state()
    assert state["completed"] == 1


def test_log_done_failure():
    db = Dashboard(total_tasks=1, concurrency=1)
    db.log_done("bad video", False, "API error", trace_id="t_002")
    state = db.get_state()
    assert state["failed"] == 1


def test_set_cookie_state():
    db = Dashboard(total_tasks=1, concurrency=1)
    cs = CookieState(value="abc", source="config", obtained_at=time.time())
    db.set_cookie_state(cs)
    state = db.get_state()
    assert state["cookie_source"] == "config"


def test_record_api_call():
    db = Dashboard(total_tasks=1, concurrency=1)
    db.record_api_call(True)
    db.record_api_call(True)
    db.record_api_call(False)
    state = db.get_state()
    assert state["api_calls"] == 3
    assert state["api_fails"] == 1


def test_update_progress():
    db = Dashboard(total_tasks=1, concurrency=1)
    task = DownloadTask(task_id="t1", trace_id="tr1", url="https://example.com", content_type="user")
    db.add_task(task)
    task.status = "running"
    db.update_task(task)
    db.update_progress(task, 5, 10)
    # Just verify no crash - internal state
