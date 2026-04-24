# 小红书整合设计规格

## 概述

将 [XHS-Downloader](https://github.com/JoeanAmier/XHS-Downloader) 的小红书下载能力接入现有 v4.0 架构。采用 **平台扩展模式**：pipeline / engine / dashboard / tracer / cookie 基础设施统一复用，XHS 仅作为新注册的平台插件接入，不引入 XHS-Downloader 源码，仅同步其签名 JS 脚本。

**范围**：
- 支持 XHS 内容类型：单笔记、用户主页、用户收藏、合集、搜索、话题
- Live Photo 视频一起下载，与图片同目录、按文件名区分
- 抖音下载行为保持 100% 兼容（目录层级会新增 `douyin/` 一级，有迁移命令）

**非目标**：
- 不做 XHS 直播、评论、IP 归属信息抓取
- 不移植 XHS-Downloader 的 TUI / REST server 界面

## 架构决策

| 维度 | 选择 | 理由 |
|------|------|------|
| 整合粒度 | 平台扩展（A） | 保持 v4.0 分层风格，未来接 B站/快手同模式 |
| 签名策略 | JS runtime（PyMiniRacer） | 与上游 XHS-Downloader 对齐，升级只换 JS 文件 |
| Cookie 组织 | 按平台分节 `cookies.{platform}` | Schema 可扩展，老 `cookie:` 字段自动迁移 |
| Cookie 获取 | Playwright 扫码（仿 `cookie_extractor.py`） | 与抖音工具链一致 |
| 下载目录 | 平台分根 `save_path/{platform}/` | 管理清晰，跨平台不冲突 |
| Live Photo | 同目录文件名区分 `image_1.jpg` + `image_1_live.mp4` | 浏览方便，关联清晰 |
| 搜索输入 | 顶层 `search_keywords: [...]` + URL 形式均支持 | 灵活 |
| 数据库 | XHS 独立表 + 顺便补齐 v4.0 数据库写入 | 去重、增量下载 |

## 平台抽象层（核心改造）

在 `core/platform.py` 新增统一抽象，让 pipeline 不再写死任何平台细节。

```python
@dataclass
class MediaAsset:
    url: str
    fallback_urls: list[str]
    kind: str              # "video_main" | "video_live" | "image" | "cover" | "music"
    suggested_filename: str
    ext: str               # "mp4" | "jpg" | "webp" | "mp3"

@dataclass
class MediaItem:
    platform: str          # "douyin" | "xhs"
    id: str
    author: str
    desc: str
    create_time: float
    assets: list[MediaAsset]
    raw: dict              # 原始响应，落盘 _data.json 用

@dataclass
class ContentRef:
    platform: str
    content_type: str      # "single" | "user" | "collection" | "music" | "search" | "topic"
    resource_id: str | None
    resolved_url: str
    extra: dict            # 搜索关键词、排序方式等

@dataclass
class ListPage:
    items: list[MediaItem]
    next_cursor: str | int | None   # 平台无关，None 表示已到末尾
    has_more: bool

class Platform(Protocol):
    name: str
    def match_url(self, url: str) -> ContentRef | None: ...

class PlatformClient(Protocol):
    async def resolve_short_url(self, url: str) -> str: ...
    async def fetch_single(self, ref: ContentRef, span: TraceSpan) -> MediaItem: ...
    async def fetch_list(
        self, ref: ContentRef, cursor, span: TraceSpan,
    ) -> ListPage: ...

class PlatformRegistry:
    def register(self, platform: Platform, client: PlatformClient) -> None: ...
    def match(self, url: str) -> tuple[Platform, PlatformClient, ContentRef] | None: ...
```

**pipeline 泛型化**：
- `DownloadPipeline._prepare_tasks` 从 `PlatformRegistry.match()` 拿到 `(platform, client, ref)`
- `_execute_task` 按 `ref.content_type` 分发，但调用统一的 `client.fetch_single` / `client.fetch_list`
- 翻页循环、进度更新、错误处理逻辑完全复用，不再区分 douyin/xhs

**DownloadEngine 去抖音化**：
- `download_media(item: MediaItem, ...)` 取代原来的 `download_media(aweme: dict, ...)`
- 所有 `_get_video_url` / `_get_cover_urls` / `_get_music_url` 删除 —— 提取逻辑改由各平台的 `PlatformClient.to_media_item()` 负责
- Engine 只管按 `MediaAsset.kind` 拼文件名、`MediaAsset.url + fallback_urls` 下载

## 抖音适配层

新增 `core/douyin/platform.py`（或就放在 `core/douyin_adapter.py`）：

- `DouyinPlatform` — URL 正则识别（保留现有 `_SHORT_URL_RE` / `_VIDEO_RE` 等）
- `DouyinPlatformClient` — 包一层 `DouyinAPIClient`，把返回 dict 转成 `MediaItem`
- 转换函数 `aweme_to_media_item(aweme: dict) -> MediaItem`：把 `video.bit_rate` / `images` / `music.play_url` / `video.cover` 提取成 `MediaAsset` 列表

**现有 `DouyinAPIClient` 不动**，只在上面加适配层。这样 apiproxy/ 里 X-Bogus 签名和 Result.dataConvert 的代码零改动。

## XHS 子模块

```
core/xhs/
  __init__.py            # 导出 XHSPlatform / XHSPlatformClient
  platform.py            # URL 识别 → ContentRef
  client.py              # HTTP + 签名注入
  signer.py              # PyMiniRacer 管理 + JS 调用
  parser.py              # API 响应 → MediaItem
  cookie.py              # 从 cookie 字符串抽 a1/web_session/webId
  urls.py                # API 端点常量
  sign.js                # vendored 签名脚本（从 XHS-Downloader 同步，打版本注释）
```

**URL 识别规则**：

| 模式 | content_type | resource_id |
|------|--------------|-------------|
| `xhslink.com/{code}` | 短链，resolve 后递归 | — |
| `/explore/{note_id}` | `single` | note_id |
| `/discovery/item/{note_id}` | `single` | note_id |
| `/user/profile/{user_id}` | `user` | user_id |
| `/board/{board_id}` | `collection` | board_id |
| `/search_result?keyword=xxx` | `search` | keyword |
| `/page/topics/{topic_id}` | `topic` | topic_id |

**签名器**（`signer.py`）：

```python
class XHSSigner:
    def __init__(self, js_path: Path) -> None:
        self._ctx = MiniRacer()
        self._ctx.eval(js_path.read_text())
        self._lock = asyncio.Lock()

    async def sign(
        self, path: str, body: dict | None, a1: str,
    ) -> dict[str, str]:
        # 返回 {"x-s": "...", "x-t": "...", "x-s-common": "..."}
        async with self._lock:
            return json.loads(self._ctx.call("sign", path, body, a1))
```

- MiniRacer 实例启动一次，整个进程共用
- 签名是 CPU 工作（~5-10ms），加 `asyncio.Lock` 串行化即可；如果将来证实是瓶颈再改 `run_in_executor` 真并发
- JS 文件打版本注释：`// Source: XHS-Downloader v2.x, synced on 2026-04-24`

**API 端点**（`urls.py`）：

| 常量 | 路径 | 用途 |
|------|------|------|
| `BASE` | `https://edith.xiaohongshu.com` | API 域名 |
| `NOTE_FEED` | `/api/sns/web/v1/feed` | 单篇笔记 |
| `USER_INFO` | `/api/sns/web/v1/user/otherinfo` | 用户信息 |
| `USER_POSTED` | `/api/sns/web/v1/user_posted` | 用户发布 |
| `USER_COLLECTED` | `/api/sns/web/v2/note/collect/page` | 用户收藏 |
| `SEARCH` | `/api/sns/web/v1/search/notes` | 搜索 |
| `TOPIC` | `/api/sns/web/v1/page/info` | 话题聚合 |
| `SELF` | `/api/sns/web/v1/user/selfinfo` | 登录态探测 |

**错误识别**：XHS 失败响应不是 HTTP 状态码而是 body 里 `success: false, code: 300012`。`XHSAPIClient._request` 需要在 `parse_fn` 前检查 body，抛 `SignatureError` / `XHSRateLimitError` / `CookieExpiredError`。

## Cookie 体系

**配置文件**：

```yaml
cookies:
  douyin: "msToken=...; ttwid=..."
  xhs: "a1=...; web_session=...; webId=...; gid=..."
```

**旧字段兼容**：ConfigLoader 检测到顶层 `cookie: "..."`（单数）时自动迁移到 `cookies.douyin`，打 WARN 日志。

**CookieState 扩展**：

```python
@dataclass
class CookieState:
    value: str
    source: str
    obtained_at: float
    platform: str = "douyin"   # 新增
    is_valid: bool = True
    last_checked: float = 0
```

**CookieManager 改造**：
- 内部维护 `dict[str, CookieState]`（key 为 platform）
- `ensure_valid_cookie(platform: str)` 而非单参
- `get_for_url(url: str) -> CookieState`：按域名路由

**新工具** `xhs_cookie_extractor.py`：
- Playwright 打开 `https://www.xiaohongshu.com`
- 等用户扫码登录
- 监听 `cookie` 变更，捕获 `a1` / `web_session` / `webId`
- 调 `SELF` 接口验证
- 写回 `config.yml` 的 `cookies.xhs` 节（保留其他字段）

## 落盘结构

```
save_path/
├── douyin/
│   └── <作者昵称>/
│       └── <YYYY-MM-DD_HH-MM-SS>_<desc>/
│           ├── <同上>.mp4
│           ├── <同上>_music.mp3
│           ├── <同上>_cover.jpg
│           └── <同上>_data.json
└── xhs/
    └── <作者昵称>/
        └── <YYYY-MM-DD_HH-MM-SS>_<desc>/
            ├── image_1.jpg
            ├── image_1_live.mp4     # Live Photo（可选，download.live_photo 控制）
            ├── image_2.jpg
            ├── <同上>_cover.jpg
            └── <同上>_data.json
```

视频型 XHS 笔记：
```
xhs/<作者>/<时间>_<desc>/
├── <同上>.mp4
├── <同上>_cover.jpg
└── <同上>_data.json
```

**目录迁移**：首次运行检测到 `save_path/` 下直接是用户名目录（无 `douyin/` 层），打印警告并建议 `python downloader.py --migrate-layout`。该命令将所有现有顶层目录移动进 `douyin/`。

## 数据库层

**现状澄清**：`apiproxy/douyin/database.py` 是 legacy v3.x 写入层，v4.0 的新 pipeline **目前没有使用**。本次顺便补齐。

**新建 `core/database.py`**：

```python
class DownloadRepository:
    def __init__(self, db_path: Path) -> None: ...
    def exists(self, platform: str, content_type: str, resource_id: str) -> bool: ...
    def upsert(
        self, platform: str, content_type: str, resource_id: str,
        meta: dict, raw: dict,
    ) -> None: ...
```

**表结构**：

抖音（保留 legacy 表名，新写入走新 Repository）：
- `t_user_post(id, sec_uid, aweme_id UNIQUE, rawdata)`
- `t_user_like(id, sec_uid, aweme_id UNIQUE, rawdata)`
- `t_mix(id, sec_uid, mix_id, aweme_id, rawdata)`
- `t_music(id, music_id, aweme_id UNIQUE, rawdata)`

XHS 独立：
- `t_xhs_note(id, note_id UNIQUE, author_id, type, rawdata, created_at)`
- `t_xhs_user_note(id, user_id, note_id UNIQUE, rawdata)`
- `t_xhs_collection(id, board_id, note_id, rawdata, UNIQUE(board_id, note_id))`
- `t_xhs_search(id, keyword, note_id, rawdata, UNIQUE(keyword, note_id))`

**增量下载**：pipeline 在调 `engine.download_media` 前查 `repo.exists()`，命中则跳过（`increase.{mode}: true` 时生效）。

## Dashboard 改造

- 顶部 Cookie 状态改为两行：
  ```
  Cookie  抖音: ✓ (config, 2min 前)
          小红书: ✓ (config, 5min 前)
  ```
- 任务卡片前缀标识平台：`[抖音] https://v.douyin.com/xxx` / `[小红书] https://xiaohongshu.com/explore/xxx`
- 完成区每条带平台标：`[小红书] 咖啡探店 — 5 文件, 3.2s`
- 统计区分平台：`抖音 10/12 ✓  小红书 8/8 ✓`

## 配置文件最终形态

```yaml
# 下载链接（所有平台混排）
links:
  - https://v.douyin.com/xxx
  - https://www.xiaohongshu.com/explore/yyy
  - https://xhslink.com/zzz

save_path: ./Downloaded/

# Cookie 按平台分节
cookies:
  douyin: "msToken=...; ttwid=..."
  xhs: "a1=...; web_session=...; webId=..."

# 下载模式（抖音 user 链接生效）
mode: [ post ]

# 下载选项
download:
  music: true
  cover: true
  metadata: true
  live_photo: true      # XHS 专用

# XHS 搜索
search_keywords:
  - "咖啡探店"
xhs_search:
  count: 20
  sort: "general"       # general | time | hot

# 时间过滤
time_range:
  start: ""
  end: ""

# 性能
concurrency: 5
retry: 3
database: true
log_level: INFO
```

## 新增 / 变更错误类型

`core/errors.py` 新增：

```python
class SignatureError(RetryableError):
    """XHS 签名失败（通常 JS runtime 异常或 a1 过期）。"""

class XHSRateLimitError(RetryableError):
    """XHS 限流响应（code=300012 等），独立于抖音 429。"""
```

## 依赖变更

`requirements.txt` 新增：

```
mini-racer>=0.12
```

Playwright 已有，不新增。

## 向后兼容

| 项 | 旧 | 新 | 迁移策略 |
|----|---|---|---------|
| cookie 配置 | `cookie: "..."` | `cookies.douyin: "..."` | ConfigLoader 自动迁移，WARN 日志 |
| 下载目录 | `save_path/<作者>/...` | `save_path/douyin/<作者>/...` | `--migrate-layout` 命令自动重组 |
| CLI 命令 | 现有全部 | 现有全部保留 + 新 `--migrate-layout` | 零破坏 |
| 数据库 | 未被 v4.0 使用 | 新建 Repository | 老 `data.db` 数据保留不动 |
| `downloader_legacy.py` | 并存 | 并存 | 不动 |

## 实施阶段

| 阶段 | 目标 | 完成判据 |
|------|------|----------|
| 1 | 平台抽象骨架 | `core/platform.py` + pipeline 泛型化，抖音仍能跑 |
| 2 | 抖音适配层 | `DouyinPlatformClient` + `aweme → MediaItem`，五种内容类型回归通过 |
| 3 | 目录分平台 + config 迁移 | 落盘进入 `douyin/`，`--migrate-layout` 正确工作 |
| 4 | 数据库层 | `DownloadRepository` + 增量跳过可验证 |
| 5 | XHS 核心接入 | 签名 + 单笔记 + 用户主页可下载 |
| 6 | XHS 完整功能 | 收藏、合集、搜索、话题、cookie 工具、Dashboard 全部就绪 |

每阶段独立可验证，如时间不允许可按需截取前 N 个阶段合并上线。

## 测试策略

**单元测试**（`tests/`）：
- URL 识别：每平台每 content_type 一组 URL 样本
- 签名器：固定 a1 + path + body → 断言输出稳定签名（录制一次真实结果作 fixture）
- Cookie 解析：字符串 → `XHSCookieFields` 提取
- Config 迁移：老 `cookie:` → 新 `cookies.douyin`
- MediaItem 转换：抖音 aweme dict / XHS note dict → 标准 `MediaItem`

**集成冒烟**（`tests/integration/`，需真 cookie，CI skip）：
- 每阶段一个脚本：下已知公开内容，检查文件大小 > 阈值

**人工冒烟清单**：阶段 2 / 阶段 5 结束各跑一次现有 `config.yml`，对比产出目录。

## 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| XHS 签名 JS 版本漂移 | XHS 全部 API 失败 | 预留 `sign.js` 更新说明文档；建议每月从上游同步一次；加 weekly 集成冒烟告警 |
| MiniRacer 并发瓶颈 | 高并发时签名串行拖慢 | 实测 5-10ms/次，预计够用；如成瓶颈换 `run_in_executor` + 多实例 |
| XHS 分页 cursor 不兼容 | pipeline 通用化困难 | `ListPage.next_cursor` 设为 `str | int | None`，pipeline 只看 `has_more` |
| 目录迁移误操作 | 用户数据错位 | `--migrate-layout` 干跑模式（先列出拟移动文件，加 `--confirm` 才执行） |
| Cookie 同时双平台过期 | 用户困惑 | Dashboard 分平台显示状态；`--validate-cookie` 支持 `--platform` 参数 |
