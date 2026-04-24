from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DownloadOptions:
    music: bool = True
    cover: bool = True
    json: bool = True


@dataclass
class AppConfig:
    links: list[str]
    save_path: Path
    cookies: str | dict | None
    cookie_mode: str
    mode: list[str]
    number: dict
    start_time: str | None
    end_time: str | None
    download: DownloadOptions
    thread: int
    database: bool
    increase: dict
    retry_times: int
    log_level: str


@dataclass
class CookieState:
    value: str
    source: str
    obtained_at: float
    is_valid: bool = True
    last_checked: float = 0


@dataclass
class TraceSpan:
    trace_id: str
    span_id: str
    parent_id: str | None
    name: str
    start_time: float
    end_time: float | None = None
    status: str = "running"
    attributes: dict = field(default_factory=dict)
    events: list = field(default_factory=list)


@dataclass
class DownloadTask:
    task_id: str
    trace_id: str
    url: str
    content_type: str
    resolved_url: str | None = None
    extracted_id: str | None = None
    status: str = "pending"
    error: str | None = None
    file_paths: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)


@dataclass
class DownloadResult:
    task: DownloadTask
    success: bool
    files_written: int
    elapsed: float
    error: str | None = None
    bytes_downloaded: int = 0
