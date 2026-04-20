# Downloader 重构设计规格

## 概述

对 `downloader.py` 进行模块化重构，从 1500+ 行单文件拆分为 10 个职责清晰的模块。新增 Cookie 交互式引导、全链路 Trace 体系、双轨日志系统和 Rich 实时仪表盘。

**兼容策略**：适度（B）— 配置格式精简优化，兼容旧格式自动迁移，命令行可加新参数。

## 模块结构

```
downloader.py                    ← 薄入口（~100行）
core/
  __init__.py
  errors.py                      ← 错误分类体系
  models.py                      ← 数据模型（纯 dataclass，零依赖）
  config.py                      ← 配置加载/校验/旧格式迁移
  cookie.py                      ← Cookie 检测/引导/生命周期
  tracer.py                      ← Trace 引擎（span 树 + JSON Lines）
  logger.py                      ← 双轨日志（Rich 控制台 + JSON Lines 文件）
  api_client.py                  ← 抖音 API 异步封装（复用 apiproxy/ 签名层）
  pipeline.py                    ← 下载管线调度器（编排层）
  downloader_engine.py           ← 文件下载引擎（async HTTP + 重试 + 限流）
  dashboard.py                   ← Rich Live 实时仪表盘
apiproxy/                        ← 保持不动（X-Bogus 签名 + 数据转换）
```

## 数据模型（models.py）

纯 dataclass，所有模块共享：

```python
@dataclass
class AppConfig:
    links: list[str]
    save_path: Path
    cookies: str | dict | None
    cookie_mode: str                  # "string" | "dict" | "auto" | "none"
    mode: list[str]                   # ["post", "like", "mix"]
    number: dict                      # {"post": 0, "like": 0, "mix": 0}
    start_time: str | None
    end_time: str | None
    download: DownloadOptions
    thread: int
    database: bool
    increase: dict                    # {"post": bool, "like": bool}
    retry_times: int
    log_level: str                    # "INFO" | "DEBUG" | "TRACE"

@dataclass
class DownloadOptions:
    music: bool = True
    cover: bool = True
    json: bool = True

@dataclass
class CookieState:
    value: str
    source: str                       # "config" | "browser" | "manual" | "file"
    obtained_at: float
    is_valid: bool = True
    last_checked: float = 0

@dataclass
class TraceSpan:
    trace_id: str                     # 顶层任务 ID
    span_id: str
    parent_id: str | None
    name: str                         # "resolve_url" | "api_fetch" | "download_file" 等
    start_time: float
    end_time: float | None = None
    status: str = "running"           # "running" | "ok" | "error" | "skipped"
    attributes: dict = field(default_factory=dict)
    events: list = field(default_factory=list)

@dataclass
class DownloadTask:
    task_id: str
    trace_id: str
    url: str
    content_type: str                 # "video" | "image" | "user" | "mix" | "music"
    resolved_url: str | None = None
    extracted_id: str | None = None
    status: str = "pending"           # "pending" | "running" | "done" | "failed" | "skipped"
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
```

## 错误分类（errors.py）

```python
class DouyinError(Exception):
    """基类"""

# 可恢复（自动重试）
class RetryableError(DouyinError): pass
class RateLimitError(RetryableError): pass
class NetworkError(RetryableError): pass

# 需用户介入
class CookieExpiredError(DouyinError): pass
class ConfigError(DouyinError): pass

# 可跳过（记录后继续下一个）
class SkippableError(DouyinError): pass
class ContentNotFoundError(SkippableError): pass
class DownloadFileError(SkippableError): pass
```

**各层职责：**

| 层 | 职责 | 不做 |
|---|---|---|
| api_client | HTTP 状态码翻译为异常，执行重试 | 不吞异常，不 print |
| downloader_engine | 文件 IO 错误处理，备选 URL 降级 | 不决定是否跳过 |
| pipeline | 决策：重试/跳过/中断，记录到 trace | 不处理 HTTP 细节 |
| dashboard | 展示错误，附带 trace_id | 不做决策 |

**pipeline 统一处理：**
- `CookieExpiredError` → 暂停所有任务，重新引导获取 Cookie，然后恢复
- `SkippableError` → 记录到 trace，标记 failed，继续下一个
- `RetryableError`（重试耗尽）→ 标记 failed
- 未预期 `Exception` → 记录完整堆栈 + trace，不中断其他任务

## Trace 引擎（tracer.py）

全链路追踪，span 树结构。一个 URL = 一个 trace，内含多层 span。

**API：**

```python
class Tracer:
    def __init__(self, log_dir: Path, session_id: str): ...
    def start_trace(self, name: str, url: str) -> TraceSpan: ...
    def start_span(self, parent: TraceSpan, name: str, **attrs) -> TraceSpan: ...
    def end_span(self, span: TraceSpan, status="ok", **attrs): ...
    def add_event(self, span: TraceSpan, event: str, **data): ...
    def context_span(self, parent, name, **attrs):
        """上下文管理器，自动 start/end + 异常捕获"""
    @staticmethod
    def replay(log_dir: Path, trace_id: str):
        """从文件还原并打印 span 树"""
```

**存储：**`logs/traces/YYYY-MM-DD_session_<id>.jsonl`，每行一个 span 闭合记录：

```json
{"trace_id":"t_8f3a","span_id":"s_001","parent_id":null,"name":"download_user","start":1745136000.1,"end":1745136075.3,"status":"ok","duration_ms":75200,"attributes":{"url":"...","total_posts":37},"events":[{"time":1745136000.5,"event":"cookie_check_passed"}]}
```

**--replay 输出：**

```
Trace t_8f3a | download_user | 75.2s | ok
├─ resolve_url          0.4s  ok
├─ cookie_check         0.1s  ok   source=config
├─ api_fetch            1.3s  ok   count=37
├─ download_media #1    2.1s  ok   files=9
│  ├─ download_file     1.8s  ok   image_1.jpg 245KB
│  └─ download_file     0.2s  skip file_exists
└─ download_media #37   0.8s  error
   └─ event: api_error {status_code=0}
```

## 双轨日志（logger.py）

**控制台轨道**：Rich 格式，人类可读，默认 INFO 级别。
**文件轨道**：JSON Lines，结构化全量记录，默认 DEBUG 级别。

| 级别 | 控制台默认 | 文件默认 | 用途 |
|---|---|---|---|
| ERROR | 显示 | 记录 | 必须人工介入 |
| WARN | 显示 | 记录 | 自动恢复的异常 |
| INFO | 显示 | 记录 | 关键业务节点 |
| DEBUG | 隐藏 | 记录 | API 请求/文件操作细节 |
| TRACE | 隐藏 | 记录 | span 开合/限流/Cookie 内部 |

**API：**

```python
class DualLogger:
    def __init__(self, log_dir: Path, console_level="INFO", file_level="DEBUG"): ...
    def get(self, module: str) -> BoundLogger: ...
    def bind_trace(self, trace_id: str, span_id: str = None) -> BoundLogger: ...
```

`--verbose` 只提升控制台到 DEBUG，文件始终全量。日志方法支持 `**kwargs` 结构化字段，控制台和文件各自渲染。

**存储：**`logs/app/YYYY-MM-DD.jsonl`

## Cookie 管理（cookie.py）

交互式引导，逐步降级：

```
1. 检查配置文件 Cookie → 验证 → 有效则使用
2. 扫描本地浏览器 Cookie DB (Chrome/Edge/Safari) → 验证 → 有效则询问后使用
3. Playwright 自动获取（需已安装）→ 打开浏览器登录 → 提取
4. 手动引导 → 打印步骤说明 → 用户粘贴 → 解析校验
```

每步成功后自动保存到配置文件。全过程在 trace span 下记录。

**验证方式**：请求轻量 API（如用户信息接口），检查响应是否包含有效数据。

**API：**

```python
class CookieManager:
    async def ensure_valid_cookie(self) -> CookieState: ...
    async def validate(self, cookie_str: str) -> tuple[bool, str]: ...
    def extract_from_browser(self) -> str | None: ...
    def save_to_config(self, cookie: CookieState): ...
```

## API 客户端（api_client.py）

对 `apiproxy/` 的异步封装，统一请求行为：

```python
class DouyinAPIClient:
    def __init__(self, cookie_state, tracer, logger, rate_limit=2.0, max_retries=3): ...
    async def get_video_info(self, aweme_id, parent_span) -> dict: ...
    async def get_user_posts(self, sec_uid, cursor, parent_span) -> dict: ...
    async def get_user_likes(self, sec_uid, cursor, parent_span) -> dict: ...
    async def get_mix_list(self, sec_uid, parent_span) -> list[dict]: ...
    async def get_mix_items(self, mix_id, cursor, parent_span) -> dict: ...
    async def get_music_items(self, music_id, cursor, parent_span) -> dict: ...
    async def close(self): ...
```

**内部统一请求流程（`_request` 方法）：**
1. 等待限流器
2. 构建带 X-Bogus 签名的 URL（调用 `apiproxy.common.utils.getXbogus()`）
3. 发起 `aiohttp` 异步请求
4. 403 → 抛 `CookieExpiredError`
5. 429 → 抛 `RateLimitError`（重试时自动退避）
6. 超时/网络错误 → 抛 `NetworkError`（自动重试）
7. 成功 → 调用 `apiproxy.douyin.result.Result.dataConvert()` 转换数据
8. 全程在 trace span 内记录

**与 apiproxy/ 的边界**：只调用其公开接口（`getXbogus`、`dataConvert`、`Urls`），不修改其内部代码。

## 下载引擎 + 管线调度

### downloader_engine.py

```python
class DownloadEngine:
    def __init__(self, save_path, tracer, logger, concurrency=5): ...
    async def download_media(self, aweme, parent_span) -> DownloadResult: ...
    async def download_file(self, url, path, parent_span, fallback_urls=None) -> bool: ...
```

单个文件下载支持：文件已存在跳过、多 URL 降级、大文件断点续传、进度上报。

### pipeline.py

编排层，组合调用其他模块：

```python
class DownloadPipeline:
    def __init__(self, config, api, engine, cookie_mgr, tracer, logger, dashboard): ...
    async def run(self): ...
    async def _prepare_tasks(self, parent_span) -> list[DownloadTask]: ...
    async def _execute_task(self, task): ...
    async def _handle_single(self, task, root): ...
    async def _handle_user(self, task, root): ...
    async def _handle_mix(self, task, root): ...
    async def _handle_music(self, task, root): ...
```

分发逻辑：`_prepare_tasks` 并行解析所有 URL → `_execute_task` 按类型分发 → 各 handler 内循环获取+下载。

## 仪表盘（dashboard.py）

Rich Live 实时刷新，4 个区域：

1. **顶栏**：运行时间 | 内存/CPU | Cookie 状态 | API 统计
2. **任务队列**：所有 URL 的状态（排队/下载中/完成）+ 作品级进度
3. **当前下载**：正在下载的具体文件 + 类型 + 进度/速度
4. **完成记录**：最近 8 条，失败附带 trace_id

`--no-dashboard` 降级为纯文本输出。

## 配置系统（config.py）

**新格式关键字段**：`links`、`save_path`、`cookie`、`mode`、`limit`、`time_range`、`download`、`incremental`、`concurrency`、`retry`、`database`、`log_level`。

**旧格式兼容**：自动迁移 `link→links`、`path→save_path`、`cookies→cookie`、`thread→concurrency`、`number→limit`、`increase→incremental`、`json→download.metadata` 等，迁移时控制台提示建议更新。

**命令行参数**（新增）：
- `python downloader.py URL [URL...]` — 快速模式
- `python downloader.py -c config.yml` — 配置文件模式
- `python downloader.py --replay <trace_id>` — 回放 trace
- `python downloader.py --validate-cookie` — 仅检测 Cookie
- `python downloader.py --generate-config` — 生成默认配置
- `--save-path`、`--concurrency`、`--verbose`、`--no-dashboard` — 覆盖配置

## 日志目录结构

```
logs/
  traces/          ← tracer.py（span 数据）
    2026-04-20_session_a1b2c3.jsonl
  app/             ← logger.py（业务日志）
    2026-04-20.jsonl
```

## 不变部分

- `apiproxy/` 整个目录保持不动
- `extract_text.py` 独立工具不受影响
- `cookie_extractor.py`、`get_cookies_manual.py` 的核心逻辑迁入 `core/cookie.py`，原文件可保留但标记为 deprecated
