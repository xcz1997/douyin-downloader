# 字幕提取系统设计

- 日期：2026-05-16
- 状态：已与用户确认，待审阅
- 涉及：`extract_text.py` 改造 + 新增 `core/subtitle/` 包 + `core/pipeline.py` / `core/config.py` 集成

## 1. 目标

给视频生成带时间戳、分段的字幕，来源有三种，**三源全部跑、各出一份独立文件**：

1. **track**：抖音 API 自带字幕轨（webvtt / caption 文件）
2. **ocr**：识别画面里烧死的硬字幕
3. **asr**：语音识别（视频本身无字幕时转写）

输出 Whisper 风格 segments JSON，同时保留人类可读 TXT。可选接进下载流程，下载完自动出字幕。

## 2. 范围与一个硬现实

本需求是 4 个相对独立的子系统（json 输出 / pipeline 集成 / 字幕轨 / ASR），共用一套带时间戳的 segments schema 与同一集成点，放进同一份 spec，分阶段实现。

**硬现实**：现有 `extract_text.py` 是「OCR 全程去重塌缩」模式——`Deduplicator`（`extract_text.py:86`）把整个视频文字去重合成一坨扁平文本，**全程不记录时间信息**。「加时间戳 JSON」不是加一句 `json.dump`，而是要改 OCR 路径的核心数据模型：从「全局去重一坨」改成「按字幕块记录 (出现时间, 消失时间, 文本)」。帧号 ÷ fps 即时间戳（子进程内 `fps` / `frame_idx` 均可得），可行但改动面是本设计中最大的一块，风险最高。

## 3. 已确认的决策

| 决策点 | 选择 |
|---|---|
| 多源策略 | 三源全跑，各出一份文件 |
| JSON 格式 | Whisper 风格 segments，同时保留 TXT |
| ASR 引擎 | mlx-qwen3-asr（中文最准 + Apple Silicon 原生加速，依赖小，Apache 2.0；调研依据见下） |
| pipeline 集成 | config.yml 加总开关，默认关 |
| 架构 | 方案 A：抽 `core/subtitle/` 包，三源实现统一接口；`extract_text.py` 退化成薄 CLI 壳 |

### ASR 引擎调研依据（技术调研守则产出）

- 候选对比 mlx-qwen3-asr vs faster-whisper。
- mlx-qwen3-asr：中文 SOTA（超 Whisper-large-v3，支持 22 中文方言）、Apple Silicon 原生 Metal 加速、依赖极小（mlx/numpy/regex/huggingface-hub）、Apache 2.0、活跃维护（2026-05-16 仍发版、462 测试）、原生输出词级时间戳 + json/srt/vtt。模型 0.6B≈1.2GB / 1.7B≈3.4GB。风险：较新（96 stars，单维护者）。
- faster-whisper：生态成熟（MIT，v1.2.1）但 Apple Silicon 上仅 CPU（无 GPU 加速、慢），中文弱一档。
- 决定理由：内容是中文短视频；项目本就为 ARM 原生加速选了 RapidOCR，ASR 同理。成熟度风险用「抽象 ASR 接口」缓解（见 §6 风险）。
- 来源：
  - https://github.com/moona3k/mlx-qwen3-asr/
  - https://pypi.org/project/mlx-qwen3-asr/
  - https://github.com/Blaizzy/mlx-audio
  - https://pypi.org/project/faster-whisper/

## 4. 架构

新增包 `core/subtitle/`，沿用项目现有 `core/platforms/` 的分层与接口化风格：

```
core/subtitle/
  schema.py      # Segment / SubtitleDoc 数据结构 + JSON/TXT 写出
  base.py        # SubtitleSource 抽象接口
  ocr_source.py  # 从 extract_text.py 搬入并改造成带时间戳
  track_source.py
  asr_source.py
  runner.py      # 按配置跑选定的源，各源独立产出文件
```

- `extract_text.py` 退化成薄 CLI 壳，调 `core.subtitle.runner`，保留原命令行参数（`--interval` / `--similarity` / `--collect` / `-o` / `--no-dashboard` / `--memory` / `--workers`），新增 `--sources` 选跑哪几个源。CLI 默认仍只跑 ocr，保持现有用户行为不破坏。
- pipeline 通过 `SubtitleRunner` 调用，与 `downloader_engine` 解耦（引擎只管下载，字幕是后处理）。

### SubtitleSource 接口（`base.py`）

```python
class SubtitleSource(Protocol):
    name: str  # "track" | "ocr" | "asr"
    def is_available(self) -> bool: ...   # 依赖/平台可用性
    def extract(self, video_path: Path, raw: dict | None) -> SubtitleDoc | None: ...
```

`raw` 为该作品的平台原始 dict（track_source 用它找字幕轨；ocr/asr 不需要）。返回 `None` 表示该源对此视频无产出（如无字幕轨），非异常。

## 5. 数据流

### 5.1 schema（`schema.py`）

```json
{
  "source": "asr",
  "video": "xxx.mp4",
  "language": "zh",
  "duration": 23.4,
  "segments": [
    { "id": 0, "start": 1.20, "end": 3.45, "text": "第一句" },
    { "id": 1, "start": 3.50, "end": 6.10, "text": "第二句" }
  ]
}
```

- 时间单位：秒（float，保留 2 位）。
- TXT：每个 segment 的 `text` 一行，纯文本，与现有 `_text.txt` 风格兼容。
- 输出文件名：视频 `<stem>.mp4` 同目录下 `<stem>.<source>.json` 与 `<stem>.<source>.txt`（如 `xxx.ocr.json`、`xxx.asr.txt`）。CLI `--collect` / `-o` 沿用原有目录规则，文件名加 `.<source>` 区分。

### 5.2 三源产出

- **track_source**：探 `raw` 中的字幕轨字段（实现期需确认抖音 `aweme_detail` 实际结构，候选 `video.caption` / `video.cla_info.caption_urls` / `seo`）。下载 webvtt/json 解析成 segments。无轨 → 返回 `None`。小红书目前按无轨处理（best-effort）。
- **ocr_source**（核心改造）：逐帧 OCR，记录每个文字块首次出现帧 `f0` 与最后出现帧 `f1` → `start = f0/fps`、`end = f1/fps`；连续相同/相似块合并为一段并延长 `end`。保留 `is_noise` / `normalize` 噪声过滤。废弃全局塌缩式 `Deduplicator`，改为「段累积器」。多进程并发模型与内存控制保留。
- **asr_source**：对已下载 mp4 调 `mlx_qwen3_asr.transcribe()`，其原生返回带时间戳 segments，映射进 schema。模型默认 `0.6b`，可配 `1.7b`。重依赖懒加载。

## 6. 配置

`config.yml` 新增块，`core/config.py` 增对应配置模型（沿用现有 dataclass/解析风格）：

```yaml
subtitle:
  enabled: false               # 总开关，默认关
  sources: [track, ocr, asr]   # 跑哪几个源，可填子集
  asr:
    model: "0.6b"              # 或 1.7b
  ocr:
    interval: 0.5
    similarity: 0.7
```

缺失整个 `subtitle` 块时等价于 `enabled: false`，行为与现状完全一致（不破坏既有行为）。

## 7. pipeline 集成

- 集成点：`DownloadPipeline`（`core/pipeline.py`）中，一个视频作品下载成功后调 `SubtitleRunner.run(video_path, raw)`。
- 不放进 `downloader_engine`：引擎职责单一（只下载），字幕是后处理。
- OCR/ASR 为阻塞 CPU 重活，pipeline 为 async，用 `await asyncio.to_thread(runner.run, ...)` 执行，不阻塞事件循环。
- `enabled: false` 时整步跳过，零开销。
- 非视频作品（纯图文 / 小红书图集）→ 跳过字幕。

## 8. 错误处理

核心原则：**字幕失败绝不影响下载**。

- 三源互相隔离：某源抛异常（无字幕轨 / ASR 模型下载失败 / OpenCV 打不开）→ warn 一条、跳过该源输出、其他源照常。
- 重依赖（mlx-qwen3-asr、opencv、rapidocr）懒加载；开关开了但依赖缺失 → 清晰报错带 `pip install` 提示，跳过该源，不崩 pipeline。
- mlx-qwen3-asr 仅 Apple Silicon：非 Mac/非 ARM 上 asr 源 `is_available()` 返回 False，记一条「不支持」并跳过。
- `runner` 捕获每个源的异常并降级，不向 pipeline 抛出。

## 9. 测试

- **schema**：JSON/TXT 序列化往返；空 segments 边界。
- **ocr_source**：造假帧序列（mock OCR 返回），断言 segments 的 start/end 由帧号正确换算、相邻同块正确合并——重点覆盖本设计风险最高的改造逻辑。
- **track_source**：喂样例 webvtt / caption json → 断言解析；「无轨」→ 返回 `None`。
- **asr_source**：mock `mlx_qwen3_asr.transcribe` 返回固定 segments → 断言映射；不跑真模型；非 Apple Silicon 时 `is_available()` 为 False。
- **runner**：三源其一抛异常 → 其他照出、不向上抛；按 `sources` 子集只跑选定源。
- **pipeline 集成**：`enabled:false` 不调 runner；`true` 每视频调用一次且 `video_path` / `raw` 正确；非视频作品跳过。

## 10. 分阶段实现建议

1. **阶段 1（基础，含最高风险改造）**：`schema.py` + `base.py` + `ocr_source.py`（OCR 带时间戳改造）+ `runner.py`（仅 ocr）+ `extract_text.py` 退化为 CLI 壳。可独立验收：CLI 跑 OCR 出 json+txt。
2. **阶段 2（扩源）**：`track_source.py` + `asr_source.py`（mlx-qwen3-asr），接入 `runner`。
3. **阶段 3（集成）**：`core/config.py` 加 subtitle 配置 + `core/pipeline.py` 接 `SubtitleRunner`，config.yml 加默认关开关。

## 11. 未决/实现期需确认

- 抖音 `aweme_detail` 中字幕轨的确切字段与文件格式（webvtt? json?）——实现 track_source 前需用真实 raw 样例确认。
- 小红书是否存在可用字幕轨——暂按无轨处理。
- mlx-qwen3-asr 的具体 Python API 形态（`transcribe()` 返回结构、segments 字段名）——实现 asr_source 前对照其文档/版本确认。
