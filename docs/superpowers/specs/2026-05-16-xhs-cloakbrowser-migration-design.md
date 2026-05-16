# XHS 浏览器迁移到 CloakBrowser 设计

- 日期：2026-05-16
- 状态：已与用户确认，待审阅
- 涉及：重写 `core/platforms/xhs_browser.py` 内部 + 新增 `xhs_login.py` + 配置加 `xhs.profile_dir`
- 背景参考：`https://yousali.com/posts/20260213-browser-automation-anti-detection/`（反检测分层）；CloakBrowser 0.3.28（本地已装，CHROMIUM_VERSION=146.0.7680.177.3）

## 1. 目标与动机

`cloak_douyin_login.py`（取 cookie）已用 CloakBrowser，但运行时 XHS 数据抓取热路径 `core/platforms/xhs_browser.py` 仍是裸 Playwright，且踩了反检测文章点名的弱招：

- `chromium.launch()` + `--disable-blink-features=AutomationControlled`
- `new_context(user_agent=写死 Chrome/122)` —— 覆盖 UA 造成 HTTP 头与 `navigator.userAgentData` 不一致
- `add_init_script("navigator.webdriver=undefined")` —— JS 注入打补丁本身可被检测（CloakBrowser 特意在 C++ 层做以规避此问题）

额外隐患：取 cookie 用 CloakBrowser、用 cookie 抓数据用 Playwright，**获取指纹 ≠ 使用指纹**，本身是风控信号。

目标：把 XHS 运行时数据会话迁到 CloakBrowser，消除上述弱招，统一获取/使用指纹，同时**对外接口不变**使消费方零改动。

## 2. 已确认决策

| 决策点 | 选择 |
|---|---|
| Profile 模型 | 两者都支持、配置选：`xhs.profile_dir` 非空 → 持久 profile；空 → 临时 context + 注入 cookie |
| 交互登录 | 持久模式=信任 profile，不阻塞（headless 可跑）；注入模式=保留现有人工确认提示 |
| 依赖缺失 | `cloakbrowser` 导入失败硬报错、不回退 Playwright（不静默用弱栈）；抖音 aiohttp 路径不受影响 |
| 架构 | 方案 A：原地重写 `xhs_browser.py` 内部，对外 `start()/page()/close()` 接口不变 |

## 3. 架构

### 3.1 `core/platforms/xhs_browser.py` 重写（接口稳定）

对外契约不变（`xhs.py:494/620` 用 `async with session.page()`，`downloader.py:129-131/168-169` 用 `start()/close()`）：

构造：
```
__init__(cookie_header: str, *, headless: bool | None = None,
         interactive: bool | None = None, profile_dir: str | None = None)
```
新增 `profile_dir`：None/"" → 注入模式；非空 → 持久模式。其余参数语义保留（`headless` 默认由 `XHS_HEADLESS` 环境变量决定；`interactive` 默认 `not headless`，仅注入模式生效）。

`start()`：
```
try: import cloakbrowser
except ImportError: raise <明确错误，含 "pip install cloakbrowser"，不回退>
launch_kwargs = dict(headless=self._headless, humanize=True)   # 不传 user_agent
if self._profile_dir:                                          # 持久模式
    self._context = await cloakbrowser.launch_persistent_context_async(
        user_data_dir=self._profile_dir, **launch_kwargs)
    # 不 add_cookies、不 interactive（信任 profile）
else:                                                          # 注入模式
    self._context = await cloakbrowser.launch_context_async(**launch_kwargs)
    await self._context.add_cookies(
        _cookie_header_to_playwright(self._cookie_header))
    if self._interactive:
        await self._await_login_confirmation()
```

删除项（全部是文章点名弱招或 Playwright 特有层）：`_pw` / `_browser` 三件套（CloakBrowser 直接返回 context，简化为单一 `self._context`）、`add_init_script(navigator.webdriver)`、`user_agent=` 覆盖、`args=["--disable-blink-features=AutomationControlled"]`、`new_context()`。

保留项：`_cookie_header_to_playwright`（CloakBrowser ctx.add_cookies 吃 Playwright cookie 形状）、`_await_login_confirmation`（仅注入模式调用）。

`page()`：`@asynccontextmanager`，`self._context is None` 时 raise（同现状），否则 `pg = await self._context.new_page()`，`finally` 关闭。去掉对 `_browser` 的检查。

`close()`：`if self._context is not None: await self._context.close(); self._context = None`，吞异常，幂等。

### 3.2 新增 `xhs_login.py`（持久 profile 登录脚本）

仿 `cloak_douyin_login.py` 结构，但写持久 profile 而非 config cookie：
- `cloakbrowser.launch_persistent_context_async(user_data_dir=<profile_dir>, headless=False)`（不传 user_agent）
- 打开 `https://www.xiaohongshu.com`，轮询 XHS 登录态 cookie（`web_session`）出现，超时 `LOGIN_TIMEOUT=300s`
- 登录成功后 profile 自动持久化（**不写 config.yml**——profile 目录本身即持久化），打印成功 + 提示"以后数据抓取会复用此 profile"
- profile_dir 从命令行参数或 config 读取（实现期定，默认与 config `xhs.profile_dir` 一致）

## 4. 配置

镜像既有 subtitle 块写法：

`config.yml`：
```yaml
xhs:
  profile_dir: ""    # 空=注入模式（用 cookies.xhs）；设路径=持久 profile 模式
```

- `core/models.py`：新增 `XHSConfig`（`profile_dir: str = ""`，定义在 `AppConfig` 之前）；`AppConfig` 末尾加 `xhs: XHSConfig = field(default_factory=XHSConfig)`。
- `core/config.py`：`_DEFAULTS` 加 `"xhs": {"profile_dir": ""}`；导入 `XHSConfig`；`_build_config` 镜像 subtitle 构造 `XHSConfig(profile_dir=str((data.get("xhs",{}) or {}).get("profile_dir","")))` 并作 kwarg 传入 `AppConfig`；`generate_default` 模板加 `xhs:` 段（`profile_dir: ""`）。
- 缺 `xhs:` 键时 `profile_dir=""` → 注入模式 → **行为与今天完全一致（不破坏既有）**。
- `downloader.py`：`XHSBrowserSession(xhs_state.value, profile_dir=config.xhs.profile_dir or None)`（单行改动）。

## 5. 错误处理

- `cloakbrowser` 导入失败 → `start()` raise 明确异常（含 `pip install cloakbrowser`）。`downloader.py` 现有 try/except 捕获 → 走"XHS Cookie 获取失败，纯抖音可忽略"降级，注册 `XHSPlatformClient(None)`。**不回退 Playwright，不静默降级**。抖音 aiohttp 全程不受影响。
- 持久模式 profile_dir 不存在 → CloakBrowser 自建空 profile；登录态无效时不在 `start()` 探测/阻塞（符合"持久=信任"决策），靠后续 XHS 请求失败时报错提示去跑 `xhs_login.py`。
- `close()` 幂等吞异常（同现状）。

## 6. 测试

不真启浏览器：`monkeypatch.setitem(sys.modules, "cloakbrowser", fake)` 注入假模块（仿 `asr_source` 的 mlx mock 手法）。新建 `tests/test_xhs_browser_cloak.py` + 扩展 `tests/test_subtitle_config.py` 风格的配置测试（或新建 `tests/test_xhs_config.py`）。

- `_cookie_header_to_playwright`：纯函数——cookie header → Playwright 形状；引号剥离；无 `=` 的段跳过；domain/path 固定。
- 模式选择：`profile_dir` 设 → 断言调 `launch_persistent_context_async(user_data_dir=…)`，**不**调 `add_cookies`，不调 `_await_login_confirmation`；`profile_dir` 空 → 断言调 `launch_context_async`，调 `add_cookies(解析结果)`。
- 依赖缺失：假装 `import cloakbrowser` 抛 ImportError → `start()` 抛含安装提示的异常；断言不调用任何 Playwright。
- interactive 门控：注入+headed → 调 `_await_login_confirmation`；持久模式 → 不调。
- 反检测参数：断言 launch 调用 kwargs **不含** `user_agent`，**含** `humanize=True`，**不含** `add_init_script` 调用、无 `--disable-blink-features` arg。
- `page()`：started 后 yield `new_page()` 结果并在退出时 close；未 started raise RuntimeError。
- 配置：`xhs.profile_dir` 默认 `""`、yaml 解析、缺 `xhs:` 块时为 `""`（向后兼容）——仿 `test_subtitle_config.py`。
- `xhs_login.py`：`py_compile` 编译检查 + mock cloakbrowser 跑 `main` 的登录轮询分支（检测到 `web_session` 即成功 / 超时分支）。
- 回归：现有 `tests/test_xhs_browser_session.py` 若断言旧 Playwright 内部细节需同步更新为新接口语义（实现期核对——只改因接口实现变化而失效的断言，不改外部行为契约）。

## 7. 范围与不做项

- 仅迁移运行时 XHS 数据会话 + 新增持久登录脚本 + 配置开关。**不**改抖音路径、不动 `xhs_cookie_extractor.py` / `get_cookies_manual.py`（独立 cookie 工具，本设计不触碰——注意到即可，不顺手重构）。
- 代理/IP 轮换、CAPTCHA、纯 aiohttp 抓取面的 JA3——CloakBrowser 范围外，本设计不覆盖（评估已知告知用户）。
- `cloak_douyin_login.py` 的 UA 覆盖已在前置提交 `e91a811` 单独修掉，不在本设计范围。

## 8. 实现期需确认

- CloakBrowser context 是否支持 `add_cookies`（标准 Playwright BrowserContext 契约，`cloak_douyin_login.py` 已用 `ctx.cookies()`/`new_page()`/`close()`，高度可信但实现首步需以真实调用确认）。
- `launch_persistent_context_async` profile 目录不存在时的行为（预期自建空 profile）——实现 `xhs_login.py` 前以真实调用确认。
- 现有 `tests/test_xhs_browser_session.py` 的断言依赖哪些旧内部细节——实现期 grep 核对再决定最小同步改动。
