import json
import sys
import time
from pathlib import Path

_LEVEL_ORDER = {"TRACE": 0, "DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}


class BoundLogger:
    def __init__(self, module: str, file_writer, console_level: int,
                 file_level: int, trace_id: str | None = None,
                 span_id: str | None = None):
        self._module = module
        self._file_writer = file_writer
        self._console_level = console_level
        self._file_level = file_level
        self._trace_id = trace_id
        self._span_id = span_id

    def bind_trace(self, trace_id: str, span_id: str | None = None) -> "BoundLogger":
        return BoundLogger(
            self._module, self._file_writer, self._console_level,
            self._file_level, trace_id, span_id,
        )

    def trace(self, msg: str, **kwargs):
        self._log("TRACE", msg, kwargs)

    def debug(self, msg: str, **kwargs):
        self._log("DEBUG", msg, kwargs)

    def info(self, msg: str, **kwargs):
        self._log("INFO", msg, kwargs)

    def warn(self, msg: str, **kwargs):
        self._log("WARN", msg, kwargs)

    def error(self, msg: str, **kwargs):
        self._log("ERROR", msg, kwargs)

    def _log(self, level: str, msg: str, extra: dict):
        level_num = _LEVEL_ORDER.get(level, 20)
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")

        if level_num >= self._file_level:
            record = {"ts": ts, "level": level, "module": self._module, "msg": msg}
            if self._trace_id:
                record["trace_id"] = self._trace_id
            if self._span_id:
                record["span_id"] = self._span_id
            record.update(extra)
            self._file_writer(record)

        if level_num >= self._console_level:
            extra_str = " ".join(f"{k}={v}" for k, v in extra.items())
            line = f"{ts[11:]} {level:<5s} [{self._module}] {msg}"
            if extra_str:
                line += f" ({extra_str})"
            print(line, file=sys.stderr)


class DualLogger:
    def __init__(self, log_dir: Path, console_level: str = "INFO",
                 file_level: str = "DEBUG"):
        self._app_dir = log_dir / "app"
        self._app_dir.mkdir(parents=True, exist_ok=True)
        self._file_path = self._app_dir / f"{time.strftime('%Y-%m-%d')}.jsonl"
        self._file = open(self._file_path, "a", encoding="utf-8")
        self._console_level = _LEVEL_ORDER.get(console_level, 20)
        self._file_level = _LEVEL_ORDER.get(file_level, 10)

    def get(self, module: str) -> BoundLogger:
        return BoundLogger(
            module, self._write_record, self._console_level, self._file_level,
        )

    def _write_record(self, record: dict):
        self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._file.flush()

    def close(self):
        self._file.close()
