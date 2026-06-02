from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DownloadOptions:
    music: bool = True
    cover: bool = True
    json: bool = True


@dataclass
class SubtitleConfig:
    enabled: bool = False
    sources: list[str] = field(default_factory=lambda: ["track", "ocr", "asr"])
    asr_model: str = "0.6b"
    ocr_interval: float = 0.5
    ocr_similarity: float = 0.7


@dataclass
class TranscribeConfig:
    enabled: bool = False
    auto_after_download: bool = False
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: str = "qwen-vl-max"
    api_key_env: str = "DASHSCOPE_API_KEY"
    api_key: str = ""            # 直接配置的 key（优先）；留空则回退 api_key_env 环境变量
    max_images: int = 0          # 0 = 不限
    overwrite: bool = False      # 幂等：False=已存在跳过
    timeout: int = 60
    retry: int = 2


@dataclass
class XHSConfig:
    profile_dir: str = ""


@dataclass
class AppConfig:
    links: list[str]
    save_path: Path
    cookies: dict[str, str]
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
    subtitle: SubtitleConfig = field(default_factory=SubtitleConfig)
    transcribe: TranscribeConfig = field(default_factory=TranscribeConfig)
    xhs: XHSConfig = field(default_factory=XHSConfig)


@dataclass
class CookieState:
    value: str
    source: str
    obtained_at: float
    platform: str = "douyin"
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
    # Excludes _data.json — counts only real media (images/videos/cover/music)
    # actually written. Pipeline uses this for limit accounting so a note that
    # produced only a sidecar json doesn't count toward `number.post`.
    media_files_written: int = 0
