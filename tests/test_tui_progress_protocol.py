from core.progress import ProgressSink
from core.dashboard import Dashboard


def test_rich_dashboard_satisfies_progress_sink():
    # The existing rich Dashboard is the reference implementation of the
    # seam. If this fails, the Protocol drifted from what pipeline needs.
    d = Dashboard(total_tasks=1, concurrency=1)
    assert isinstance(d, ProgressSink)


def test_progress_sink_is_runtime_checkable():
    class Missing:
        pass

    assert not isinstance(Missing(), ProgressSink)
