from core.models import DownloadTask, CookieState
from core.progress import ProgressSink
from tui.sink import TextualSink


def _task():
    return DownloadTask(task_id="t1", trace_id="x", url="u", content_type="video")


def test_sink_satisfies_protocol():
    assert isinstance(TextualSink(lambda e: None), ProgressSink)


def test_emits_events_for_calls():
    events = []
    s = TextualSink(events.append)

    s.set_status("正在获取列表…")
    s.log_done("作品1", True, "3 文件")
    s.add_task(_task())
    s.update_bytes_progress(50, 100, "v.mp4")
    s.set_cookie_state(CookieState(value="c", source="config",
                                   obtained_at=0.0, platform="douyin"))

    kinds = [e["kind"] for e in events]
    assert kinds == [
        "status", "log", "add_task", "bytes_progress", "cookie_state"
    ]
    assert events[0]["payload"]["message"] == "正在获取列表…"
    assert events[3]["payload"] == {
        "bytes_done": 50, "bytes_total": 100, "name": "v.mp4"
    }
    assert events[4]["payload"]["platform"] == "douyin"


def test_get_state_returns_local_aggregate():
    s = TextualSink(lambda e: None)
    s.add_task(_task())
    st = s.get_state()
    assert st["total"] == 1 and "completed" in st and "failed" in st


def test_update_task_drives_completed_and_failed_counters():
    s = TextualSink(lambda e: None)
    ok = DownloadTask(task_id="a", trace_id="x", url="u",
                      content_type="video")
    ok.status = "done"          # real success status from core/pipeline.py
    bad = DownloadTask(task_id="b", trace_id="x", url="u",
                       content_type="video")
    bad.status = "failed"       # real failure status from core/pipeline.py
    s.update_task(ok)
    s.update_task(bad)
    st = s.get_state()
    assert st["completed"] == 1
    assert st["failed"] == 1


def test_noop_methods_do_not_raise():
    s = TextualSink(lambda e: None)
    s.start(); s.stop(); s.refresh(); s.clear_status()
    s.clear_current_item(); s.add_bytes(10); s.record_api_call(True)
