# 图文笔记图片转录功能设计

- 日期：2026-06-02
- 状态：设计待审
- 关联：复用 `2026-05-16-subtitle-extraction-design.md`（字幕功能）的分层与隔离理念

## 背景与问题

抖音图文笔记下载下来是一组图片：攻略图、红黑榜表格、美食清单、风景大片。**关键信息都嵌在图片里**，无法检索、复用、二次加工。

项目现有的 OCR（`core/subtitle/ocr_source.py`，基于 rapidocr，本是给视频抽帧抓字幕用的）只能逐行抓字符，做不到三件关键的事：

- 判断「这张是纯风景图，没有正文」，避免输出噪声
- 把表格、分栏排版还原成有结构的文字
- 结合上下文纠正模糊字

因此需要一个**用多模态大模型（VLM）把图片转成结构化文字稿**的能力。

## 目标

1. 把一个图文笔记目录里的图片，用 VLM 转成一份结构化文字稿（Markdown）
2. 集成进 TUI，新增「转录」面板（可配置、可选）
3. 也能作为独立 CLI 工具，对任意图片目录手动跑
4. 下载图文笔记后可选自动触发
5. **幂等**：重复跑不重复识别已转录的笔记
6. 失败只告警，绝不拖垮下载主流程（与字幕功能一致）

## 非目标（YAGNI）

- 不做本地 OCR、不做 OCR+VLM 混合（已决策走纯 VLM）
- 不做视频内容转录（那是现有字幕功能的范畴）
- 不做翻译、总结、改写（只忠实转录，保留原文与 emoji）
- 不做复杂的并发调度（先小并发/串行，按图片数线性计费）
- 不把图片 hash 入库做精细变更检测（幂等先用「输出文件是否存在」，见下）

## 关键决策（已与用户确认）

| 决策点 | 选择 |
|--------|------|
| 识别引擎 | 多模态大模型（VLM），非本地 OCR |
| 工具定位 | 项目正式功能，配置化、可选 |
| 触发方式 | TUI 面板 + 独立 CLI + 下载后可选自动，三条都要 |
| 模型接口 | **通用 OpenAI-compatible vision 协议**（base_url + model + key），一套代码接通义千问VL / GPT-4o / 智谱 / 本地 vLLM |
| 幂等 | 输出文字稿已存在则跳过，`--force` / 面板勾「覆盖」可强制重跑 |

## 架构（与字幕功能同构）

核心逻辑独立成模块，TUI 面板与 CLI 都只是薄壳——照搬字幕功能 `core/subtitle/` ←→ `tui/panels/subtitle.py` ←→ `extract_text.py` 的三层结构。

| 层 | 位置 | 职责 |
|----|------|------|
| 核心 | `core/transcribe/`（新建） | VLM 调用 + 遍历图片 + 组装文字稿 + 幂等 |
| TUI | `tui/panels/transcribe.py`（新建） | 照 `SubtitlePanel` 抄，驱动核心，进度写 LogPane |
| CLI | `transcribe_images.py`（新建） | 照 `extract_text.py` 抄，独立工具 |
| 配置 | `config.yml` 新增 `transcribe:` 段 | 开关 + 模型 + key 来源 |
| 下载集成 | `core/pipeline.py` 接入点 | 图文笔记下完后可选触发 |

**为什么不挤进现有字幕模块**：字幕是「带时间轴的视频字幕」（输出带时间戳的 Segment），这个是「整图内容提取」（输出整段文字稿），输入（视频 vs 图片目录）和输出语义都不同。硬合会让两边接口都变脏。但复用字幕那套「单源失败被隔离、不拖垮主流程」的理念。

## 核心模块拆解 `core/transcribe/`

### `config.py` — `TranscribeConfig`
dataclass，承载配置项（见下「配置 schema」）。从 `AppConfig` 解析。

### `client.py` — `VLMClient`
- 职责：单次 vision 调用。输入「图片路径列表 + prompt」，输出模型返回的文本。
- 协议：OpenAI-compatible `POST {base_url}/chat/completions`，`messages` 含 `image_url`（data URI / base64）。
- key：从环境变量读（`api_key_env` 指定变量名），不落盘。
- 错误：key 缺失 → 明确报错并提示设环境变量；网络错误 → 有限重试；返回非预期 → 抛带上下文的异常。
- 依赖：`requests` 或 `aiohttp`（项目已有）。无需第三方 SDK。

### `prompt.py` — 转录提示词
复刻本次会话验证有效的 prompt：逐图转录、保留原文用词/标点/emoji、不改写不翻译、纯风景图标注「（无文字/风景图）」并简述画面、按阅读顺序排列、用「### 图N」分隔。

### `runner.py` — `ImageTranscriber`
- 入口：`transcribe_dir(note_dir) -> Path | None`
- 流程：
  1. 在目录内找 `*_data.json`（元信息）与 `image_*.{webp,jpg,png}`（按序号排序）
  2. **幂等检查**：目标 `文字稿_<作者>.md` 已存在且未开启覆盖 → 跳过，返回 None
  3. 调 `VLMClient` 得到图片转录文本
  4. 组装：元信息（作者/发布/互动/话题，from data.json）+ 作者正文 `desc` + 图片转录
  5. 写出 `文字稿_<作者>.md`
- 隔离：单张图失败标记后继续；整个笔记失败抛出由调用方 log.warn 吞掉。
- `build_transcribe_spec(...)` 纯函数做参数校验/解析 → 便于单测（照 `build_runner_spec`）。

## 数据流

```
图文笔记目录
  → 找 *_data.json + image_*.{webp,jpg,png}
  → 幂等检查：文字稿_<作者>.md 已存在? ──是──> 跳过(log "已转录")
        │否
  → 图片编码为 data URI
  → VLMClient(prompt + images) ──OpenAI vision──> 转录文本
  → 组装(元信息 + 正文 desc + 图片转录)
  → 写 文字稿_<作者>.md
```

## 配置 schema

`config.yml` 新增（默认全关，零配置时不影响现有行为）：

```yaml
transcribe:
  enabled: false                 # 总开关；false 时 CLI/面板仍可手动用
  auto_after_download: false     # 下载图文笔记后自动转录
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  model: "qwen-vl-max"           # 可换任意 OpenAI-compatible vision 模型
  api_key_env: "DASHSCOPE_API_KEY"  # key 从该环境变量读，不写进配置文件
  max_images: 0                  # 单笔记最多转录张数，0=不限
  overwrite: false               # 幂等：false=已存在跳过；true=每次重跑
  timeout: 60                    # 单次请求超时(秒)
  retry: 2                       # 单图失败重试次数
```

key 只从环境变量读、不落盘，避免把密钥提交进配置文件。

## TUI 集成

- `tui/app.py`：`_SECTIONS` 加 `"转录"`，`_SECTION_ID` / 图标各加一项（照「字幕」）。
- `tui/panels/transcribe.py`：`TranscribePanel(Static)`，照 `SubtitlePanel` 结构：
  - 输入卡：图片目录路径
  - 选项卡：「覆盖重跑」checkbox；可选「模型 / base_url」覆盖（留空用 config）
  - 操作：「开始转录」「停止」按钮
  - 进度：每个笔记一行结果写入 `LogPane`（粒度=每目录一行，同字幕）
  - 纯函数 `build_transcribe_spec` 便于单测
- thread worker 跑核心（VLM 调用是阻塞 HTTP），不卡 UI。

## CLI 集成

`transcribe_images.py`（照 `extract_text.py` 薄壳）：

```bash
python transcribe_images.py ./JIN/douyin/某作者/某笔记目录/
python transcribe_images.py ./JIN/douyin/           # 递归批量
python transcribe_images.py <dir> --force --model qwen-vl-max
```

## 下载流程集成

`core/pipeline.py` 中，图文笔记（content_type=image）下载成功后：
若 `transcribe.enabled and transcribe.auto_after_download` → 调 `ImageTranscriber.transcribe_dir(笔记目录)`。
任何异常仅 `log.warn`，不改变下载任务的成功/失败计数。

## 错误处理

| 场景 | 行为 |
|------|------|
| key 环境变量未设 | 明确报错 + 提示「请设置环境变量 X」，不静默 |
| 单张图调用失败 | 重试 N 次，仍失败则该图标「[识别失败]」，继续其余图 |
| 整个笔记转录失败 | 抛出 → 调用方（CLI/面板/pipeline）log.warn，不影响其他笔记与下载 |
| 网络超时 | 按 `retry` 重试 |
| 目录无图片 / 无 data.json | 跳过并提示 |

## 测试策略

- `build_transcribe_spec` 纯函数：空目录、缺 key、参数解析等分支
- 幂等：输出已存在→跳过；`--force`/overwrite→覆盖重跑
- `VLMClient`：mock HTTP，验证请求体（含 image_url、model）与响应解析，**不真调 API**
- `prompt`：组装快照
- `ImageTranscriber`：mock client，验证文字稿组装（元信息+正文+图片转录）正确、单图失败隔离
- TUI 面板 smoke：照 `test_tui_subtitle_panel.py`

## 成本/性能说明（写进文档与 CLI 帮助）

- 每张图一次 vision 调用，费用随图片数线性增长
- `max_images` 可限制单笔记张数；触发截断时必须 log 提示「仅转录前 N/总 M 张」，不静默丢弃
- `overwrite=false` 的幂等避免重复付费

## 依赖关系

- 转录的输入是「已下载的图文笔记目录」，依赖图文笔记能正确下载。图文笔记短链识别的修复在 `fix/douyin-note-shortlink` 分支，建议先合入 main，再基于其上做本功能（或本功能分支 rebase 其上）。功能代码本身不直接依赖该修复。
