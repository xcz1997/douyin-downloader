# TUI 主界面设计

- 日期：2026-05-17
- 状态：已与用户确认，待审阅
- 涉及：新增 `tui/` 包 + `tui.py` 入口 + `core/progress.py`（Protocol）；不改 core 业务逻辑、不动 CLI 脚本
- 可视化布局选型记录：`.superpowers/brainstorm/`（布局 A：左侧导航 + 内容区 + 底部常驻日志/进度）

## 1. 目标

把当前分散在多个脚本的功能整合进一个交互式终端界面（TUI）：抖音/小红书下载、视频字幕提取（track/ocr/asr 三源）、登录（抖音 cloak 扫码 / XHS 持久 profile）、设置项编辑。现有 CLI 脚本（`downloader.py`/`extract_text.py`/`cloak_douyin_login.py`/`xhs_login.py`）保留供脚本化/自动化。

## 2. 已确认决策

| 决策点 | 选择 |
|---|---|
| 界面形态 | 终端 TUI，框架 Textual（异步原生，契合项目 asyncio + rich 栈） |
| 架构关系 | TUI 包在 `core/` 上，直接调 pipeline/runner/session/ConfigLoader；CLI 脚本保留 |
| 导航布局 | A：左侧导航区块切换 + 右侧内容区 + 底部常驻日志/进度 |
| 长任务桥接 | 抽 `ProgressSink` Protocol（pipeline 实际调用的 Dashboard 方法面），rich `Dashboard` 与新 `TextualSink` 各实现之；pipeline 注入 `TextualSink`，零改 core 逻辑 |

## 3. 架构

### 3.1 新增结构（不碰 core 业务逻辑、不动 CLI 脚本）

```
tui/
  __init__.py
  app.py            # DownloaderApp(textual.App)：Sidebar 导航 + ContentSwitcher + 底部 LogView/进度 dock；全局快捷键（q 退出等）
  sink.py           # TextualSink：实现 ProgressSink，把方法调用转 Textual 消息推到 UI 线程
  widgets.py        # 共享 widget：LogView(RichLog) + 进度条/状态栏 + cookie/profile 指示灯
  panels/
    __init__.py
    download.py     # DownloadPanel
    subtitle.py     # SubtitlePanel
    login.py        # LoginPanel
    settings.py     # SettingsPanel
tui.py              # 入口：DownloaderApp().run()
core/progress.py    # ProgressSink @runtime_checkable Protocol（新增，仅类型契约）
```

### 3.2 与 core 的接缝（ProgressSink）

`core/pipeline.py` 不直接依赖 rich——它 duck-type 调用 `Dashboard` 的方法面。新增 `core/progress.py` 定义 `ProgressSink` `@runtime_checkable` `Protocol`，方法集合 = pipeline 实际调用的那组（依据 pipeline 现状）：`add_task`、`update_task`、`update_progress`、`update_file_progress`、`set_current_item`、`update_bytes_progress`、`clear_current_item`、`add_bytes`、`set_status`、`clear_status`、`log_done`、`log_item_done`、`record_api_call`、`set_cookie_state`、`get_state`、`refresh`、`start`、`stop`、`__enter__`、`__exit__`。

- 现有 rich `Dashboard`（`core/dashboard.py`）天然已满足该 Protocol——不修改它。
- `tui/sink.py` 的 `TextualSink` 实现同一 Protocol，每个方法把数据经 `app.call_from_thread` / `post_message` 推给 UI 线程的 LogView/进度 widget（线程安全）。
- `DownloadPipeline` 构造已有 `dashboard=` 参数 → TUI 注入 `TextualSink`。**pipeline/runner/session 零改动**。
- 实现期需确认：pipeline 当前调用的 Dashboard 方法集合与签名（实现首步以 grep 核对，Protocol 必须与现状逐一对齐，不臆造）。

### 3.3 长任务执行模型

面板操作 → Textual `@work`(async/thread) worker → worker 构造 core 对象注入 `TextualSink` → core 调 sink → sink 推 UI。

- 下载：`DownloadPipeline` 在 async worker 跑。
- 字幕：`SubtitleRunner.run()` 是同步（内部 `asyncio.to_thread`），在 Textual thread worker 跑；进度粒度 = 每视频一条结果（runner 无更细回调——已知粒度限制，如实记录，不为此改 core）。
- 登录：worker 跑 `cloak_douyin_login.main()` / `xhs_login.main()` 的协程，stdout 重定向进 TUI 日志，复用已测逻辑。
- 停止：`worker.cancel()` + 协作式取消；退出前 `await xhs_session.close()`。

## 4. 各功能区交互

- **下载区**：链接来源单选（config.yml / 手动输 URL，自动识别抖音/XHS）；并发数；"同时提取字幕"开关；开始/停止。XHS 路径依 `config.xhs.profile_dir`：有持久 profile 直接用；无则注入模式且**强制 `interactive=False`**（TUI 绝不调 `input()`），cookie 失效时日志提示去登录区。
- **字幕区**：文件/目录输入；源多选（track/ocr/asr）；asr 模型（0.6b/1.7b）；ocr interval/similarity；开始/停止。
- **登录区**：两动作——抖音 `cloak_douyin_login.main()`、XHS `xhs_login.main()`（持久 profile）；worker 跑、轮询登录态、stdout 进日志；完成刷新顶部 cookie/profile 指示灯。
- **设置区**：`ConfigLoader.load()` 读关键项（`save_path`、`thread`/并发、`retry_times`、`subtitle.*`、`xhs.profile_dir`、links）填表单；保存写回 config.yml；**界面明确提示 yaml.dump 会丢注释（项目既有行为，不在本设计"修"它）**。

## 5. 错误处理

边界原则：任何 core 异常不崩 TUI。

- worker 内 core 异常 → 捕获、日志区标红、该任务标失败、app 继续运行。
- 缺 `cloakbrowser` → `XHSBrowserSession.start()` 抛 RuntimeError，TUI 捕获并提示「XHS 跳过，抖音不受影响」（与 `downloader.py` 现有降级一致）。
- 配置保存失败 → 设置区内联报错，不崩。
- 任务进行中退出 → 确认弹窗 → 取消所有 worker + `await session.close()` → 再退出。
- `TextualSink` 跨线程推送一律经 Textual 安全机制（`call_from_thread`/`post_message`），不直接操作 widget。

## 6. 测试

不真跑浏览器/网络/下载/OCR/ASR（沿用全项目纪律，用 mock/fake）。

- **Protocol 一致性**：`tests/test_tui_progress_protocol.py` 断言 rich `Dashboard` 与 `TextualSink` 都 `isinstance(..., ProgressSink)`（守住接缝；任一漂移即失败）。
- **TextualSink 翻译**：每个方法 → 正确的消息/日志映射，用 fake 消息泵断言（不需真 app）。
- **面板逻辑**：Textual `App.run_test()` pilot——app 启动、导航切换面板、退出快捷键；"开始下载"以 mock 的 pipeline 断言用正确 config + `TextualSink` 起 worker（仿既有 mock cloakbrowser/mlx 手法）。
- **设置区**：tmp config 读 → 改字段 → 存 → 重载断言持久（仿 `test_xhs_config.py`/`test_subtitle_config.py`）。
- **冒烟 + 清理**：app 启动冒烟；退出时 worker/session 清理被调用（mock session 断言 close 调用）。
- 既有全量测试（≈210）须保持绿（本设计仅新增 `tui/` + `core/progress.py`，不改 core 逻辑）。

## 7. 分期（一个 spec，实现计划分 4 期，每期独立可验收）

1. **骨架**：`core/progress.py` Protocol + `tui/app.py` 导航壳 + `widgets.py`（LogView/进度/指示灯）+ `tui/sink.py` `TextualSink` + **设置区**（读写 config）+ `tui.py` 入口 + Protocol 一致性测试。可验收：启动 TUI、切换导航、改存配置。
2. **下载区**：接 `DownloadPipeline`（worker + TextualSink）。
3. **字幕区**：接 `SubtitleRunner`。
4. **登录区** + 顶部 cookie/profile 状态指示灯。

## 8. 范围与不做项

- 仅做 TUI 前端 + ProgressSink 接缝。**不**改 pipeline/runner/session/config 的业务逻辑；**不**动任何 CLI 脚本（`downloader.py` 等保留）；**不**碰 `downloader_legacy.py` / 旧 cookie 工具（`cookie_extractor.py`/`get_cookies_manual.py`/`xhs_cookie_extractor.py`）——注意到即可，不顺手清。
- CloakBrowser 范围外的盲点（纯 aiohttp 抓取面 JA3、代理轮换、CAPTCHA）不在本设计——TUI 不改变这些。
- 新增依赖 `textual`（加入 requirements）。Textual 是 Python 交互式 TUI 事实标准、异步原生、与 rich 同作者同栈，属标准选型，不另做调研。

## 9. 实现期需确认

- `core/pipeline.py` 实际调用的 `Dashboard` 方法**精确集合与签名**——实现 `ProgressSink` 首步以 grep 逐一核对，Protocol 严格对齐现状（§3.2 列表为依据，实际以代码为准）。
- `cloak_douyin_login.main()` / `xhs_login.main()` 的 stdout 重定向进 TUI 的可靠方式（worker 内 `contextlib.redirect_stdout` 到一个推 LogView 的写入器）——实现登录区前以真实签名确认。
- Textual `@work` thread vs async worker 对 `SubtitleRunner`（阻塞）vs `DownloadPipeline`（async）的正确选择——实现各区时按 Textual 文档确认。
