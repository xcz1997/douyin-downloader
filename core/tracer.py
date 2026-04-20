import json
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from core.models import TraceSpan


class Tracer:
    def __init__(self, log_dir: Path, session_id: str):
        self._log_dir = log_dir / "traces"
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._session_id = session_id
        self._file_path = self._log_dir / f"{time.strftime('%Y-%m-%d')}_session_{session_id}.jsonl"
        self._file = open(self._file_path, "a", encoding="utf-8")

    def _gen_id(self, prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:8]}"

    def start_trace(self, name: str, url: str) -> TraceSpan:
        trace_id = self._gen_id("t")
        span = TraceSpan(
            trace_id=trace_id,
            span_id=self._gen_id("s"),
            parent_id=None,
            name=name,
            start_time=time.time(),
            attributes={"url": url},
        )
        return span

    def start_span(self, parent: TraceSpan, name: str, **attrs) -> TraceSpan:
        span = TraceSpan(
            trace_id=parent.trace_id,
            span_id=self._gen_id("s"),
            parent_id=parent.span_id,
            name=name,
            start_time=time.time(),
            attributes=dict(attrs),
        )
        return span

    def end_span(self, span: TraceSpan, status: str = "ok", **attrs):
        span.end_time = time.time()
        span.status = status
        span.attributes.update(attrs)
        self._write_span(span)

    def add_event(self, span: TraceSpan, event: str, **data):
        entry = {"time": time.time(), "event": event, **data}
        span.events.append(entry)

    @contextmanager
    def context_span(self, parent: TraceSpan, name: str, **attrs):
        span = self.start_span(parent, name, **attrs)
        try:
            yield span
            self.end_span(span, status="ok")
        except Exception as e:
            self.end_span(span, status="error", error=str(e))
            raise

    def _write_span(self, span: TraceSpan):
        record = {
            "trace_id": span.trace_id,
            "span_id": span.span_id,
            "parent_id": span.parent_id,
            "name": span.name,
            "start": span.start_time,
            "end": span.end_time,
            "status": span.status,
            "duration_ms": round((span.end_time - span.start_time) * 1000, 1)
            if span.end_time
            else None,
            "attributes": span.attributes,
            "events": span.events,
        }
        self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._file.flush()

    def close(self):
        self._file.close()

    @staticmethod
    def replay(log_dir: Path, trace_id: str) -> str:
        traces_dir = log_dir / "traces"
        spans = []
        for f in traces_dir.glob("*.jsonl"):
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    record = json.loads(line)
                    if record["trace_id"] == trace_id:
                        spans.append(record)

        if not spans:
            return f"Trace {trace_id} not found"

        children = {}
        for s in spans:
            pid = s["parent_id"]
            if pid:
                children.setdefault(pid, []).append(s)

        lines = []

        def render(span, prefix="", is_last=True):
            dur = f"{span['duration_ms'] / 1000:.1f}s" if span.get("duration_ms") else "?"
            status = span["status"]
            attrs = " ".join(f"{k}={v}" for k, v in span["attributes"].items()
                            if k not in ("url",))
            connector = "└─" if is_last else "├─"
            if span["parent_id"] is None:
                lines.append(
                    f"Trace {span['trace_id']} | {span['name']} | {dur} | {status}"
                )
            else:
                lines.append(f"{prefix}{connector} {span['name']:20s} {dur:>6s}  {status:5s}  {attrs}")

            kids = children.get(span["span_id"], [])
            kids.sort(key=lambda s: s["start"])
            child_prefix = prefix + ("   " if is_last else "│  ")
            for i, kid in enumerate(kids):
                render(kid, child_prefix, i == len(kids) - 1)

            for evt in span.get("events", []):
                evt_data = {k: v for k, v in evt.items() if k not in ("time", "event")}
                evt_line = f"{prefix}{'   ' if is_last else '│  '}   └─ event: {evt['event']} {evt_data}"
                lines.append(evt_line)

        root_spans = [s for s in spans if s["parent_id"] is None]
        for rs in root_spans:
            render(rs)

        return "\n".join(lines)
