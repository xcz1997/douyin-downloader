# XHS 整合 Plan 2：XHS 基础设施（阶段 A）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不引入 XHS 签名 / API / mini-racer 依赖的前提下，把 XHS URL 识别、Cookie 多平台管理、Cookie 抓取工具全部就位。阶段 A 结束你能跑 `python xhs_cookie_extractor.py` 扫码登录 XHS 并把结果写进 `config.yml`，抖音下载流程行为保持不变。

**Architecture:** `XHSPlatform` 注册进 `PlatformRegistry`（暂无配套 `XHSPlatformClient` — 阶段 B 才接入）；`CookieState` 加 `platform` 字段；`CookieManager` 改成按平台持有多个 `CookieState`，按 URL 域名路由；`ConfigLoader` 新增 `cookies: {douyin: ..., xhs: ...}` 嵌套格式并保留 `cookie: "..."` 老字段兼容。

**Tech Stack:** Python 3.11+, Playwright（Chromium）, PyYAML, dataclasses

**Spec:** `docs/specs/2026-04-24-xhs-integration-design.md`

**前置条件:** Plan 1 已全部完成（commit `368eb86` 以后）。

**这份 Plan 覆盖 Spec 的**：Phase 5 的前半部分（Cookie 基础设施 + XHS URL 识别 + Cookie 工具）

**后续 Plan**：Plan 3（XHS 签名 / API / PlatformClient / 端到端）在本 Plan 完成后基于实际 cookie 与真实 API 响应续写。

---

## Task 1: XHSPlatform URL 识别

**Files:**
- Create: `core/platforms/xhs.py`
- Create: `tests/test_xhs_platform.py`

XHS 支持的 URL 形态（spec 表格节选 + 实测短链）：
- `https://xhslink.com/a/xxx` 或 `https://xhslink.com/m/xxx` — 短链
- `https://www.xiaohongshu.com/explore/{note_id}` — 单篇笔记
- `https://www.xiaohongshu.com/discovery/item/{note_id}` — 单篇笔记（旧入口）
- `https://www.xiaohongshu.com/user/profile/{user_id}` — 用户主页
- `https://www.xiaohongshu.com/board/{board_id}` — 合集
- `https://www.xiaohongshu.com/search_result?keyword=...` — 搜索
- `https://www.xiaohongshu.com/page/topics/{topic_id}` — 话题

---

### Step 1: 写测试

- [ ] **Step 1: 创建测试文件**

```python
# tests/test_xhs_platform.py
from core.platforms.xhs import XHSPlatform


def test_match_short_url_with_a_prefix():
    p = XHSPlatform()
    ref = p.match_url("https://xhslink.com/a/ABCDEFG")
    assert ref is not None
    assert ref.platform == "xhs"
    assert ref.content_type == "short"
    assert ref.resource_id is None


def test_match_short_url_with_m_prefix():
    """Real-world short link format: xhslink.com/m/xxx."""
    p = XHSPlatform()
    ref = p.match_url("https://xhslink.com/m/5kcCust1t6Z")
    assert ref is not None
    assert ref.content_type == "short"


def test_match_short_url_plain():
    """Bare xhslink.com/xxx (no /m/ or /a/) also qualifies."""
    p = XHSPlatform()
    ref = p.match_url("https://xhslink.com/Abc123xyz")
    assert ref is not None
    assert ref.content_type == "short"


def test_match_explore_note():
    p = XHSPlatform()
    ref = p.match_url(
        "https://www.xiaohongshu.com/explore/65f8a1b2c3d4e5f6a7b8c9d0"
    )
    assert ref is not None
    assert ref.content_type == "single"
    assert ref.resource_id == "65f8a1b2c3d4e5f6a7b8c9d0"


def test_match_discovery_item_note():
    p = XHSPlatform()
    ref = p.match_url(
        "https://www.xiaohongshu.com/discovery/item/65f8a1b2c3d4e5f6a7b8c9d0"
    )
    assert ref is not None
    assert ref.content_type == "single"
    assert ref.resource_id == "65f8a1b2c3d4e5f6a7b8c9d0"


def test_match_user_profile():
    p = XHSPlatform()
    ref = p.match_url(
        "https://www.xiaohongshu.com/user/profile/5abc123456789def0abcdef1"
    )
    assert ref is not None
    assert ref.content_type == "user"
    assert ref.resource_id == "5abc123456789def0abcdef1"


def test_match_user_profile_with_query():
    p = XHSPlatform()
    ref = p.match_url(
        "https://www.xiaohongshu.com/user/profile/5abc123?xhsshare=1&appuid=2"
    )
    assert ref is not None
    assert ref.content_type == "user"
    assert ref.resource_id == "5abc123"


def test_match_board_collection():
    p = XHSPlatform()
    ref = p.match_url("https://www.xiaohongshu.com/board/65f8a1b2c3d4")
    assert ref is not None
    assert ref.content_type == "collection"
    assert ref.resource_id == "65f8a1b2c3d4"


def test_match_search_with_keyword():
    p = XHSPlatform()
    ref = p.match_url(
        "https://www.xiaohongshu.com/search_result?keyword=%E5%92%96%E5%95%A1&source=web"
    )
    assert ref is not None
    assert ref.content_type == "search"
    # keyword 在 extra 里保持 URL-decoded
    assert ref.extra.get("keyword") == "咖啡"


def test_match_topic():
    p = XHSPlatform()
    ref = p.match_url("https://www.xiaohongshu.com/page/topics/65f8a1b2")
    assert ref is not None
    assert ref.content_type == "topic"
    assert ref.resource_id == "65f8a1b2"


def test_no_match_douyin():
    p = XHSPlatform()
    assert p.match_url("https://www.douyin.com/video/7123456") is None


def test_no_match_random():
    p = XHSPlatform()
    assert p.match_url("https://example.com/foo") is None
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_xhs_platform.py -v`
Expected: ModuleNotFoundError — `core.platforms.xhs` not defined.

- [ ] **Step 3: 实现 XHSPlatform**

Create `core/platforms/xhs.py`:

```python
# core/platforms/xhs.py
"""XHS (小红书) platform plugin: URL recognition.

Stage A implementation: URL matching only. API client, signer, and
MediaItem conversion arrive in Plan 3.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from core.platform import ContentRef


_SHORT_URL_RE = re.compile(r"^https?://xhslink\.com/[\w/]+")
_EXPLORE_RE = re.compile(r"xiaohongshu\.com/explore/([0-9a-fA-F]+)")
_DISCOVERY_RE = re.compile(r"xiaohongshu\.com/discovery/item/([0-9a-fA-F]+)")
_USER_RE = re.compile(r"xiaohongshu\.com/user/profile/([0-9a-fA-F]+)")
_BOARD_RE = re.compile(r"xiaohongshu\.com/board/([0-9a-fA-F]+)")
_TOPIC_RE = re.compile(r"xiaohongshu\.com/page/topics/([0-9a-fA-F]+)")
_SEARCH_RE = re.compile(r"xiaohongshu\.com/search_result")


class XHSPlatform:
    """URL recognition for Xiaohongshu (小红书).

    Precedence: short > explore > discovery > user > board > search > topic.
    """

    name = "xhs"

    def match_url(self, url: str) -> ContentRef | None:
        if _SHORT_URL_RE.match(url):
            return ContentRef(
                platform="xhs",
                content_type="short",
                resource_id=None,
                resolved_url=url,
            )

        m = _EXPLORE_RE.search(url)
        if m:
            return ContentRef(
                platform="xhs",
                content_type="single",
                resource_id=m.group(1),
                resolved_url=url,
            )

        m = _DISCOVERY_RE.search(url)
        if m:
            return ContentRef(
                platform="xhs",
                content_type="single",
                resource_id=m.group(1),
                resolved_url=url,
            )

        m = _USER_RE.search(url)
        if m:
            return ContentRef(
                platform="xhs",
                content_type="user",
                resource_id=m.group(1),
                resolved_url=url,
            )

        m = _BOARD_RE.search(url)
        if m:
            return ContentRef(
                platform="xhs",
                content_type="collection",
                resource_id=m.group(1),
                resolved_url=url,
            )

        if _SEARCH_RE.search(url):
            keyword = ""
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            kw_list = params.get("keyword", [])
            if kw_list:
                keyword = kw_list[0]
            return ContentRef(
                platform="xhs",
                content_type="search",
                resource_id=keyword or None,
                resolved_url=url,
                extra={"keyword": keyword} if keyword else {},
            )

        m = _TOPIC_RE.search(url)
        if m:
            return ContentRef(
                platform="xhs",
                content_type="topic",
                resource_id=m.group(1),
                resolved_url=url,
            )

        return None
```

- [ ] **Step 4: 运行测试验证 12 passed**

Run: `python -m pytest tests/test_xhs_platform.py -v`
Expected: 12 passed

- [ ] **Step 5: 不会和 DouyinPlatform 混淆（双向检查）**

Run:
```bash
python -c "
from core.platforms.xhs import XHSPlatform
from core.platforms.douyin import DouyinPlatform

x, d = XHSPlatform(), DouyinPlatform()

# 抖音 URL 不应被 XHS 匹配
assert x.match_url('https://v.douyin.com/abc') is None
assert x.match_url('https://www.douyin.com/video/123') is None
assert x.match_url('https://www.douyin.com/user/MS4wLjABabc') is None

# XHS URL 不应被抖音匹配
assert d.match_url('https://xhslink.com/m/5kcCust1t6Z') is None
assert d.match_url('https://www.xiaohongshu.com/explore/abc123') is None
assert d.match_url('https://www.xiaohongshu.com/user/profile/abc') is None

print('ok')
"
```
Expected: `ok`

- [ ] **Step 6: 提交**

```bash
git add core/platforms/xhs.py tests/test_xhs_platform.py
git commit -m "feat(xhs): XHSPlatform URL recognition"
```

---

## Task 2: CookieState 扩展 platform 字段

**Files:**
- Modify: `core/models.py`
- Modify: `tests/test_models.py`

现有的 `CookieState` 没有 platform 字段。加一个默认 `"douyin"` 保证向后兼容。

- [ ] **Step 1: 查看现有模型**

先 `cat core/models.py` 了解当前 `CookieState` 的字段顺序。

- [ ] **Step 2: 更新测试**

Add to the end of `tests/test_models.py`:

```python
def test_cookie_state_defaults_to_douyin():
    from core.models import CookieState
    s = CookieState(
        value="msToken=abc", source="config", obtained_at=1700000000.0,
    )
    assert s.platform == "douyin"
    assert s.is_valid is True


def test_cookie_state_explicit_platform():
    from core.models import CookieState
    s = CookieState(
        value="web_session=xxx", source="config",
        obtained_at=1700000000.0, platform="xhs",
    )
    assert s.platform == "xhs"
```

- [ ] **Step 3: 运行测试验证失败**

Run: `python -m pytest tests/test_models.py::test_cookie_state_defaults_to_douyin tests/test_models.py::test_cookie_state_explicit_platform -v`
Expected: 失败（`platform` 字段不存在）

- [ ] **Step 4: 修改 CookieState**

In `core/models.py`, find the `CookieState` dataclass:

```python
@dataclass
class CookieState:
    value: str
    source: str
    obtained_at: float
    is_valid: bool = True
    last_checked: float = 0
```

Change to:

```python
@dataclass
class CookieState:
    value: str
    source: str
    obtained_at: float
    platform: str = "douyin"
    is_valid: bool = True
    last_checked: float = 0
```

Note: `platform` 放在 `is_valid` 之前以匹配其他 dataclass 的字段排布（required 先 / optional 后，optional 内部常见放前）。检查已有的 `CookieState(...)` 构造是否都走 keyword arguments — 如果有 positional 构造会在编译时出错，需要修复。

- [ ] **Step 5: 修全部构造点**

Run: `grep -rn "CookieState(" core/ tests/ | grep -v __pycache__`

预期看到几处 `CookieState(value=..., source=..., obtained_at=..., is_valid=...)` 的构造（关键字参数形式不会受影响）。如有 positional 形式（极少见），改为 keyword。

- [ ] **Step 6: 运行测试验证**

Run: `python -m pytest tests/ -v`
Expected: 之前所有绿的测试仍绿 + 新增 2 个通过。

- [ ] **Step 7: 提交**

```bash
git add core/models.py tests/test_models.py
git commit -m "feat(models): CookieState 加 platform 字段（默认 douyin）"
```

---

## Task 3: ConfigLoader 支持 cookies 多平台分节

**Files:**
- Modify: `core/config.py`
- Modify: `tests/test_config.py`

目标：让 `config.yml` 同时支持以下两种格式——

**老格式（保留兼容，现有 config.yml 用的就是这种）**：
```yaml
cookie: "msToken=abc; ttwid=xyz"
```

**新格式（多平台）**：
```yaml
cookies:
  douyin: "msToken=abc; ttwid=xyz"
  xhs: "a1=...; web_session=..."
```

两种并存时新格式赢。ConfigLoader 最终产出一个 dict `{"douyin": "...", "xhs": "..."}` 给 `AppConfig`。

### Schema 层面的变化

`AppConfig.cookies` 现在的声明是 `cookies: str | dict | None`。我们收紧为 `dict[str, str]`（key 是平台名），并保留 `cookie_mode` 字段的语义不变（"string" / "dict" / "auto" / "none"）。

- [ ] **Step 1: 写测试**

Add at the top of `tests/test_config.py`:

```python
def test_cookies_new_multi_platform_format(tmp_path):
    """New format: cookies:{douyin: ..., xhs: ...}"""
    import yaml
    from core.config import ConfigLoader

    cfg_path = tmp_path / "config.yml"
    cfg_path.write_text(yaml.safe_dump({
        "links": ["https://www.douyin.com/video/123"],
        "save_path": str(tmp_path),
        "cookies": {
            "douyin": "msToken=abc",
            "xhs": "a1=xyz; web_session=qqq",
        },
    }), encoding="utf-8")

    cfg = ConfigLoader(str(cfg_path)).load()
    assert isinstance(cfg.cookies, dict)
    assert cfg.cookies["douyin"] == "msToken=abc"
    assert cfg.cookies["xhs"] == "a1=xyz; web_session=qqq"
    assert cfg.cookie_mode == "dict"


def test_cookie_old_single_format_migrates_to_douyin(tmp_path):
    """Old `cookie: "..."` string field migrates to cookies.douyin."""
    import yaml
    from core.config import ConfigLoader

    cfg_path = tmp_path / "config.yml"
    cfg_path.write_text(yaml.safe_dump({
        "links": ["https://www.douyin.com/video/123"],
        "save_path": str(tmp_path),
        "cookie": "msToken=abc; ttwid=xyz",
    }), encoding="utf-8")

    cfg = ConfigLoader(str(cfg_path)).load()
    assert isinstance(cfg.cookies, dict)
    assert cfg.cookies.get("douyin") == "msToken=abc; ttwid=xyz"
    assert "xhs" not in cfg.cookies
    # backward-compat: cookie_mode reflects that a string was supplied
    assert cfg.cookie_mode == "string"


def test_cookies_new_wins_over_cookie_old(tmp_path):
    """When both `cookie:` and `cookies:` present, new wins."""
    import yaml
    from core.config import ConfigLoader

    cfg_path = tmp_path / "config.yml"
    cfg_path.write_text(yaml.safe_dump({
        "links": ["https://www.douyin.com/video/123"],
        "save_path": str(tmp_path),
        "cookie": "legacy=old",
        "cookies": {"douyin": "new=value"},
    }), encoding="utf-8")

    cfg = ConfigLoader(str(cfg_path)).load()
    assert cfg.cookies["douyin"] == "new=value"


def test_cookie_none_produces_empty_dict(tmp_path):
    """No cookie in config → cookies is empty dict, cookie_mode=none."""
    import yaml
    from core.config import ConfigLoader

    cfg_path = tmp_path / "config.yml"
    cfg_path.write_text(yaml.safe_dump({
        "links": ["https://www.douyin.com/video/123"],
        "save_path": str(tmp_path),
    }), encoding="utf-8")

    cfg = ConfigLoader(str(cfg_path)).load()
    assert cfg.cookies == {}
    assert cfg.cookie_mode == "none"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_config.py -v`
Expected: 4 个新测试失败（旧逻辑仍认为 `cookies` 是字符串或 None）。

- [ ] **Step 3: 修改 `core/config.py`**

Open `core/config.py`. 找到 `_migrate_old_format` 函数，替换对 `cookies` 和 `cookie` 的处理逻辑。原代码：

```python
    # "cookies" (old name) → "cookie" (new name)
    if "cookies" in data and "cookie" not in data:
        data["cookie"] = data.pop("cookies")
    elif "cookies" in data:
        data.pop("cookies")
```

改为：

```python
    # Cookie handling: three supported forms
    # (a) new multi-platform:   cookies: {douyin: "...", xhs: "..."}
    # (b) old single string:    cookie: "msToken=abc; ..."
    # (c) legacy plural string: cookies: "msToken=abc; ..." (pre-v4.0)
    # New (a) always wins; (b) and (c) migrate to {"douyin": "..."}.
    new_cookies: dict[str, str] = {}

    if "cookies" in data and isinstance(data["cookies"], dict):
        # form (a) — keep as-is, drop legacy singular if it sneaked in
        new_cookies = {k: v for k, v in data["cookies"].items() if v}
        data.pop("cookie", None)
    elif "cookies" in data and isinstance(data["cookies"], str):
        # form (c) — plural string, migrate to douyin slot
        if data["cookies"].strip():
            new_cookies = {"douyin": data["cookies"]}
        data.pop("cookie", None)
    elif "cookie" in data and isinstance(data["cookie"], str):
        # form (b) — singular string, migrate to douyin slot
        if data["cookie"].strip():
            new_cookies = {"douyin": data["cookie"]}
    elif "cookie" in data and isinstance(data["cookie"], dict):
        # tolerate someone who wrote dict under `cookie:` singular
        new_cookies = {k: v for k, v in data["cookie"].items() if v}

    data.pop("cookies", None)
    data.pop("cookie", None)
    data["cookies"] = new_cookies
```

Remove the legacy `elif "cookies" in data: data.pop("cookies")` block — handled above.

- [ ] **Step 4: 更新 `_build_config`**

In `core/config.py` `_build_config()`, find:

```python
        # Cookie / cookie_mode
        cookie_value = data.get("cookie")
        cookie_mode = _detect_cookie_mode(cookie_value)
```

Change to:

```python
        # Cookie / cookie_mode
        # After migration, cookies is always dict[str, str] (possibly empty).
        cookies_dict = data.get("cookies", {})
        if not isinstance(cookies_dict, dict):
            cookies_dict = {}
        cookie_mode = _cookie_mode_from_dict(cookies_dict, data)
```

Then update the `AppConfig` construction near the bottom of `_build_config`. Find:

```python
        return AppConfig(
            links=links,
            save_path=save_path,
            cookies=cookie_value,
            cookie_mode=cookie_mode,
```

Change to:

```python
        return AppConfig(
            links=links,
            save_path=save_path,
            cookies=cookies_dict,
            cookie_mode=cookie_mode,
```

- [ ] **Step 5: 替换 `_detect_cookie_mode` with `_cookie_mode_from_dict`**

Replace the old `_detect_cookie_mode` function with:

```python
def _cookie_mode_from_dict(
    cookies: dict[str, str], raw_data: dict[str, object],
) -> str:
    """Derive a legacy-compatible cookie_mode from the final cookies dict.

    Args:
        cookies: Post-migration ``{platform: cookie_string}`` dict.
        raw_data: The full (post-migration) config dict, used to decide
            whether the ``cookie`` field originally had the literal string
            ``"auto"``.

    Returns:
        One of ``"dict"``, ``"auto"``, ``"string"``, ``"none"``.
    """
    if not cookies:
        return "none"
    # If user wrote a dict explicitly, mode is "dict"; if they migrated
    # from the single-string form, we surface that as "string" so legacy
    # CookieManager behavior (validate one cookie) is preserved unless
    # the caller explicitly opts into multi-platform handling.
    if len(cookies) > 1:
        return "dict"
    sole_value = next(iter(cookies.values()), "")
    if sole_value == "auto":
        return "auto"
    return "string"
```

- [ ] **Step 6: 更新 `AppConfig.cookies` 类型注解**

In `core/models.py`, find `AppConfig`:

```python
@dataclass
class AppConfig:
    links: list[str]
    save_path: Path
    cookies: str | dict | None
    ...
```

Change `cookies` annotation to:

```python
    cookies: dict[str, str]
```

（去掉 `str | ... | None`，并明确要求 dict。）

- [ ] **Step 7: 处理 `ConfigLoader.save_cookie` 兼容性**

Find `ConfigLoader.save_cookie` — current code writes back the `cookie` (singular) field. Change signature to support both forms explicitly. Replace the whole method with:

```python
    def save_cookie(self, cookie_str: str, platform: str = "douyin") -> None:
        """Update the cookie for *platform* in the config file on disk.

        Reads the file, updates the cookie value, writes it back. Prefers
        the new ``cookies: {platform: ...}`` nested form when the file
        already has it; falls back to the singular ``cookie: "..."`` form
        only when the file has no ``cookies:`` block yet AND *platform*
        is ``"douyin"`` (the historical default). Other platforms always
        write under ``cookies.{platform}``.

        Args:
            cookie_str: New cookie string value to persist.
            platform: Target platform (default ``"douyin"`` for
                back-compat with old single-platform callers).
        """
        raw = self._read_yaml()

        if "cookies" in raw and isinstance(raw["cookies"], dict):
            raw["cookies"][platform] = cookie_str
        elif platform == "douyin" and isinstance(raw.get("cookie"), str):
            # preserve the legacy `cookie:` field for plain douyin use
            raw["cookie"] = cookie_str
        else:
            cookies_block = raw.setdefault("cookies", {})
            if not isinstance(cookies_block, dict):
                cookies_block = {}
                raw["cookies"] = cookies_block
            cookies_block[platform] = cookie_str
            # Drop the singular field if present AND platform is douyin
            # to avoid having two sources of truth:
            if platform == "douyin" and "cookie" in raw:
                del raw["cookie"]

        with self._path.open("w", encoding="utf-8") as fh:
            yaml.dump(
                raw, fh, allow_unicode=True,
                default_flow_style=False, sort_keys=False,
            )
```

- [ ] **Step 8: 运行测试**

Run: `python -m pytest tests/ -v`
Expected:
- 新 4 个配置测试全部通过
- 原有 `test_config.py` 其他测试仍通过（ConfigLoader 对原有行为保持兼容）
- 现实世界的 `config.yml` 仍能正确加载（Step 10 手工验证）

- [ ] **Step 9: 验证现有 config.yml 能正常解析**

```bash
python -c "
from core.config import ConfigLoader
cfg = ConfigLoader('config.yml').load()
print('cookies keys:', list(cfg.cookies.keys()))
print('cookie_mode:', cfg.cookie_mode)
print('douyin cookie prefix:', cfg.cookies.get('douyin', '')[:40] + '...')
"
```
Expected: `cookies keys: ['douyin']` + `cookie_mode: string` + cookie 前缀看到。

- [ ] **Step 10: 提交**

```bash
git add core/config.py core/models.py tests/test_config.py
git commit -m "$(cat <<'EOF'
feat(config): cookies 多平台分节 + 向后兼容 cookie 老字段

- 新格式：cookies: {douyin: "...", xhs: "..."}
- 老格式自动迁移到 cookies.douyin
- AppConfig.cookies 类型从 str | dict | None 收紧为 dict[str, str]
- ConfigLoader.save_cookie 支持 platform 参数
EOF
)"
```

---

## Task 4: CookieManager 改造为按平台路由

**Files:**
- Modify: `core/cookie.py`
- Modify: `tests/test_cookie.py`

当前 `CookieManager.ensure_valid_cookie()` 返回一个 `CookieState`（以抖音语义工作）。新语义：
- 构造时读 `config.cookies` 拿到所有平台的 cookie 字符串
- `ensure_valid_cookie(platform: str = "douyin") -> CookieState` 按平台验证 / 返回
- 新增 `get_for_url(url: str) -> CookieState | None` 根据 URL 域名路由（pipeline 暂不用，但留好口子）
- 保留单参旧签名（默认 `platform="douyin"`）保证 pipeline `run()` 不改代码

### Step-by-step

- [ ] **Step 1: 先阅读现有 `core/cookie.py` 和 `tests/test_cookie.py`**

```bash
wc -l core/cookie.py tests/test_cookie.py
```

了解 `CookieManager` 现有方法和测试覆盖范围。

- [ ] **Step 2: 写新测试**

Add to `tests/test_cookie.py`（末尾）：

```python
import pytest

from core.cookie import CookieManager
from core.models import AppConfig, DownloadOptions


def _make_config(cookies: dict) -> AppConfig:
    return AppConfig(
        links=[], save_path=Path("."),
        cookies=cookies, cookie_mode="dict",
        mode=["post"], number={"post": 0},
        start_time=None, end_time=None,
        download=DownloadOptions(),
        thread=1, database=False, increase={}, retry_times=3,
        log_level="INFO",
    )


class _FakeLogger:
    def info(self, *a, **k): pass
    def warn(self, *a, **k): pass
    def error(self, *a, **k): pass
    def debug(self, *a, **k): pass


@pytest.mark.asyncio
async def test_ensure_valid_cookie_returns_douyin_by_default():
    cfg = _make_config({"douyin": "msToken=abc", "xhs": "a1=xyz"})
    mgr = CookieManager(cfg, tracer=None, logger=_FakeLogger())
    state = await mgr.ensure_valid_cookie()  # no platform arg
    assert state.platform == "douyin"
    assert "msToken=abc" in state.value


@pytest.mark.asyncio
async def test_ensure_valid_cookie_explicit_xhs():
    cfg = _make_config({"douyin": "msToken=abc", "xhs": "a1=xyz"})
    mgr = CookieManager(cfg, tracer=None, logger=_FakeLogger())
    state = await mgr.ensure_valid_cookie(platform="xhs")
    assert state.platform == "xhs"
    assert "a1=xyz" in state.value


@pytest.mark.asyncio
async def test_ensure_valid_cookie_unknown_platform_raises():
    cfg = _make_config({"douyin": "msToken=abc"})
    mgr = CookieManager(cfg, tracer=None, logger=_FakeLogger())
    with pytest.raises(Exception):  # CookieError or similar
        await mgr.ensure_valid_cookie(platform="bilibili")


def test_get_for_url_douyin():
    cfg = _make_config({"douyin": "msToken=abc", "xhs": "a1=xyz"})
    mgr = CookieManager(cfg, tracer=None, logger=_FakeLogger())
    mgr._states["douyin"] = CookieState(
        value="msToken=abc", source="config",
        obtained_at=0.0, platform="douyin",
    )
    state = mgr.get_for_url("https://www.douyin.com/video/123")
    assert state is not None
    assert state.platform == "douyin"


def test_get_for_url_xhs():
    cfg = _make_config({"douyin": "msToken=abc", "xhs": "a1=xyz"})
    mgr = CookieManager(cfg, tracer=None, logger=_FakeLogger())
    mgr._states["xhs"] = CookieState(
        value="a1=xyz", source="config",
        obtained_at=0.0, platform="xhs",
    )
    for url in [
        "https://www.xiaohongshu.com/explore/abc",
        "https://xhslink.com/m/xxx",
    ]:
        state = mgr.get_for_url(url)
        assert state is not None
        assert state.platform == "xhs"


def test_get_for_url_unknown_returns_none():
    cfg = _make_config({"douyin": "msToken=abc"})
    mgr = CookieManager(cfg, tracer=None, logger=_FakeLogger())
    assert mgr.get_for_url("https://example.com/foo") is None
```

Add necessary imports at top of the test file if missing:

```python
from pathlib import Path
from core.models import CookieState
```

- [ ] **Step 3: 跑新测试验证失败**

Run: `python -m pytest tests/test_cookie.py -v -k "ensure_valid_cookie or get_for_url"`
Expected: 失败 — `ensure_valid_cookie` 不接收 `platform` 参数，`get_for_url` 不存在。

- [ ] **Step 4: 改造 `core/cookie.py`**

开 `core/cookie.py`。找到 `CookieManager.__init__` 和 `ensure_valid_cookie`，做以下改动：

**改动 1：`__init__` 读取 dict 并准备 per-platform 状态**

原代码会读 `self._config.cookies`（曾经是 `str | dict | None`）。改为：

```python
    def __init__(
        self,
        config: AppConfig,
        tracer,  # 原签名保留
        logger,
    ) -> None:
        self._config = config
        self._tracer = tracer
        self._log = logger
        # per-platform CookieState cache; lazily populated by ensure_valid_cookie
        self._states: dict[str, CookieState] = {}
        # snapshot of config cookies: {platform: raw_string}
        cookies_raw = config.cookies
        if not isinstance(cookies_raw, dict):
            cookies_raw = {}
        self._raw_cookies: dict[str, str] = dict(cookies_raw)
```

**改动 2：`ensure_valid_cookie` 接收 `platform` 参数**

找到 `async def ensure_valid_cookie(self)` 签名，改为：

```python
    async def ensure_valid_cookie(
        self, platform: str = "douyin",
    ) -> CookieState:
        """Return a valid cookie for *platform*, obtaining / refreshing as needed.

        Args:
            platform: Platform identifier (``"douyin"`` / ``"xhs"``).
                Defaults to ``"douyin"`` to preserve legacy callers.

        Returns:
            CookieState ready for use by the API client of *platform*.

        Raises:
            CookieError: No cookie is configured for *platform* and no
                acquisition channel yields one.
        """
        if platform in self._states and self._states[platform].is_valid:
            return self._states[platform]

        raw = self._raw_cookies.get(platform, "")
        if not raw:
            raise CookieError(
                f"no cookie configured for platform={platform!r}"
            )

        # Keep legacy validation logic for douyin; for xhs we just trust the
        # string (no in-process validation endpoint in Stage A; Plan 3 wires
        # the real XHS /selfinfo check).
        if platform == "douyin":
            state = await self._validate_douyin(raw)
        else:
            state = CookieState(
                value=raw, source="config",
                obtained_at=time.time(), platform=platform,
                is_valid=True, last_checked=time.time(),
            )

        state.platform = platform
        self._states[platform] = state
        return state
```

Make sure `time` is imported at top of the file if not already.

**改动 3：把原有的抖音校验抽成私有方法**

原本 `ensure_valid_cookie` 里的抖音校验（调抖音 API 判断 cookie 有效）抽出来成 `_validate_douyin(self, raw_cookie: str) -> CookieState`。具体步骤：

1. 读原先 `ensure_valid_cookie` 的 body，除了"决定 platform / 缓存"以外的逻辑（读 config.cookies / 调 douyin 验证接口 / 构造 CookieState）全部挪到新私有方法
2. `_validate_douyin` 返回 `CookieState(value=..., source=..., obtained_at=..., platform="douyin", is_valid=True)`

如果实现过于复杂，**最小改动方案**：保留原方法 body 几乎不变，但在顶部加：

```python
    async def ensure_valid_cookie(
        self, platform: str = "douyin",
    ) -> CookieState:
        if platform in self._states and self._states[platform].is_valid:
            return self._states[platform]

        if platform != "douyin":
            raw = self._raw_cookies.get(platform, "")
            if not raw:
                raise CookieError(f"no cookie configured for {platform!r}")
            state = CookieState(
                value=raw, source="config",
                obtained_at=time.time(), platform=platform,
            )
            self._states[platform] = state
            return state

        # Legacy douyin path — unchanged below
        raw_cookies = self._raw_cookies.get("douyin", "")
        # ... (all existing douyin logic, reading from raw_cookies instead of self._config.cookies)
        ...
        state.platform = "douyin"
        self._states["douyin"] = state
        return state
```

具体代码结构取决于原文件现状。目标：旧抖音路径行为 100% 保持。

**改动 4：新增 `get_for_url`**

在 `CookieManager` 类末尾追加：

```python
    def get_for_url(self, url: str) -> CookieState | None:
        """Return cached CookieState for the platform owning *url*.

        Does not trigger validation — meant for read-only routing. Use
        ``ensure_valid_cookie(platform=...)`` if you need validation.

        Args:
            url: The full URL being routed.

        Returns:
            Cached CookieState, or None if neither url pattern matches
            or the cookie hasn't been acquired yet.
        """
        if "xiaohongshu.com" in url or "xhslink.com" in url:
            return self._states.get("xhs")
        if "douyin.com" in url:
            return self._states.get("douyin")
        return None
```

- [ ] **Step 5: 跑全部测试**

Run: `python -m pytest tests/ -v`

Expected:
- 新增 6 个 cookie 测试通过
- 原有抖音 cookie 测试仍通过
- 其他测试不受影响

如果有测试因为 `config.cookies` 类型变化（`str | dict | None` → `dict[str, str]`）而报错，那是因为测试里手工构造 `AppConfig(cookies="...")`。改成 `AppConfig(cookies={"douyin": "..."})`。

- [ ] **Step 6: 提交**

```bash
git add core/cookie.py tests/test_cookie.py
git commit -m "feat(cookie): CookieManager 按平台管理 CookieState + get_for_url"
```

---

## Task 5: 入口 downloader.py 处理多平台 cookie

**Files:**
- Modify: `downloader.py`

现在 `cmd_download` 在 `PlatformRegistry` 里注册了 `DouyinPlatform`。本 task 要：
1. 注册 `XHSPlatform`（虽然 `XHSPlatformClient` 还没写，注册一个占位 client — 要点：让 URL 识别生效，真下载到阶段 B 接入前会在 `fetch_single/fetch_list` 处失败，这个是明确的 "not implemented" 错误而不是静默跳过）。
2. `cmd_download` 读 `config.cookies.douyin` 给 `DouyinAPIClient.update_cookie`。
3. XHS 占位 client 不需要 cookie（阶段 A 没签名没 API 调用）。

- [ ] **Step 1: 新增 XHS 占位 PlatformClient（只负责表态 "not implemented"）**

Append to `core/platforms/xhs.py`:

```python
class XHSPlatformClient:
    """Stage A placeholder. Real implementation lands in Plan 3.

    Exists so DownloadPipeline can detect "XHS URL matched but downloader
    not yet wired" and report a clear error instead of silently skipping.
    """

    async def resolve_short_url(self, url: str) -> str:
        raise NotImplementedError(
            "XHS short URL resolution not yet implemented "
            "(pending Plan 3 Stage B)"
        )

    async def fetch_single(self, ref, span):
        raise NotImplementedError(
            "XHS single-note fetch not yet implemented "
            "(pending Plan 3 Stage B)"
        )

    async def fetch_list(self, ref, cursor, span):
        raise NotImplementedError(
            "XHS list fetch not yet implemented "
            "(pending Plan 3 Stage B)"
        )
```

- [ ] **Step 2: 修改 `downloader.py` cmd_download**

找到现有的 import 区（函数内部）：

```python
    from core.platform import PlatformRegistry
    from core.platforms.douyin import DouyinPlatform, DouyinPlatformClient
```

改为：

```python
    from core.platform import PlatformRegistry
    from core.platforms.douyin import DouyinPlatform, DouyinPlatformClient
    from core.platforms.xhs import XHSPlatform, XHSPlatformClient
```

找到 `registry.register(DouyinPlatform(), DouyinPlatformClient(api))`，在它下面追加：

```python
    registry.register(XHSPlatform(), XHSPlatformClient())
```

找到 cookie 流转部分：

```python
    # Cookie needs to flow into DouyinAPIClient; CookieManager would also
    # push it during pipeline.run(), but we acquire it here so we can
    # update the api client before any request is made.
    cookie_state = await cookie_mgr.ensure_valid_cookie()
    api.update_cookie(cookie_state)
```

改为：

```python
    # Acquire the Douyin cookie up-front so DouyinAPIClient has credentials
    # before pipeline.run() fires any request. XHS cookie is acquired
    # lazily per-task if/when XHS URLs are encountered (once Plan 3
    # ships XHSAPIClient).
    try:
        cookie_state = await cookie_mgr.ensure_valid_cookie(platform="douyin")
        api.update_cookie(cookie_state)
    except Exception as exc:
        log.warn("抖音 Cookie 获取失败（若本次只下载 XHS 可忽略）", error=str(exc))
```

让抖音 cookie 缺失不再阻塞 XHS 专用的下载任务（虽然阶段 A 还没有真实的 XHS 下载能力）。

- [ ] **Step 3: 冒烟**

```bash
python -c "import downloader; print('ok')"
python downloader.py --help | head -5
```

Both should succeed.

- [ ] **Step 4: 跑测试套件**

Run: `python -m pytest tests/ -v`
Expected: 全部绿（本 task 不涉及 pipeline 具体执行，只装配）。

- [ ] **Step 5: 提交**

```bash
git add core/platforms/xhs.py downloader.py
git commit -m "$(cat <<'EOF'
feat(entry): 注册 XHSPlatform + 占位 Client，cookie 按平台获取

XHSPlatformClient 在阶段 A 只抛 NotImplementedError，让 pipeline 看到
XHS URL 时报出明确错误而不是静默跳过。抖音 cookie 获取失败不再阻塞，
为只下载 XHS 的使用场景铺路（阶段 B 会接入真实 XHS 下载能力）。
EOF
)"
```

---

## Task 6: xhs_cookie_extractor.py — Playwright 扫码工具

**Files:**
- Create: `xhs_cookie_extractor.py`（项目根目录，和 `cookie_extractor.py` 并列）

目标：运行 `python xhs_cookie_extractor.py`，打开浏览器访问 `https://www.xiaohongshu.com`，等用户扫码登录，成功后把 cookie 写回 `config.yml` 的 `cookies.xhs` 节。

参考现有 `cookie_extractor.py` 的结构与风格（尤其是 Playwright 启动 / 等待登录 / 写 config 的流程）。

- [ ] **Step 1: 先看现有 `cookie_extractor.py` 了解项目风格**

```bash
wc -l cookie_extractor.py
head -60 cookie_extractor.py
```

阅读重点：它是如何初始化 Playwright / 等待登录完成 / 写 config 的。

- [ ] **Step 2: 创建 `xhs_cookie_extractor.py`**

```python
# xhs_cookie_extractor.py
"""Interactive XHS cookie extractor.

Launches a Playwright-controlled Chromium window, navigates to
https://www.xiaohongshu.com, waits for the user to log in via QR-code,
harvests the authenticated cookies, and writes the result back into
``config.yml`` under ``cookies.xhs``.

Usage:
    python xhs_cookie_extractor.py              # uses ./config.yml
    python xhs_cookie_extractor.py other.yml    # specify a config file
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import yaml

try:
    from playwright.async_api import async_playwright
except ImportError:
    print(
        "ERROR: playwright not installed. Install with:\n"
        "    pip install playwright\n"
        "    playwright install chromium",
        file=sys.stderr,
    )
    sys.exit(1)


# Cookie fields we care about — the signer and API use these.
REQUIRED_FIELDS = {"a1", "web_session", "webId"}

# Fields that are nice to have but not mandatory.
OPTIONAL_FIELDS = {"gid", "xsecappid", "websectiga", "customer-sso-sid"}


def _cookies_to_header_string(cookies: list[dict]) -> str:
    """Turn a Playwright cookie list into a ``Cookie:`` header value."""
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)


def _has_required(cookies: list[dict]) -> bool:
    """Return True iff the harvest contains the fields the signer needs."""
    names = {c["name"] for c in cookies}
    return REQUIRED_FIELDS.issubset(names)


async def _harvest(config_path: Path) -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        await page.goto("https://www.xiaohongshu.com/explore")

        print("=" * 60)
        print("请在浏览器里扫码登录 XHS。")
        print("登录完成后脚本会自动提取 cookie 并写入 config.yml。")
        print("（最长等待 5 分钟；登录后可保持浏览器打开不动）")
        print("=" * 60)

        # Poll every 3s for up to 5 min, checking whether the required
        # cookies are present.
        deadline = asyncio.get_event_loop().time() + 300
        harvested: list[dict] | None = None
        while asyncio.get_event_loop().time() < deadline:
            cookies = await context.cookies("https://www.xiaohongshu.com")
            if _has_required(cookies):
                harvested = cookies
                break
            await asyncio.sleep(3)

        await context.close()
        await browser.close()

    if harvested is None:
        print("ERROR: 未在 5 分钟内检测到有效登录（缺少 a1 / web_session / webId）", file=sys.stderr)
        sys.exit(2)

    cookie_str = _cookies_to_header_string(harvested)
    _write_cookie_to_config(config_path, cookie_str)
    names = sorted({c["name"] for c in harvested})
    print(f"\n成功：已抓取 {len(harvested)} 个 cookie，写入 {config_path}")
    print(f"  关键字段: {', '.join(sorted(REQUIRED_FIELDS))}")
    extra = [n for n in names if n in OPTIONAL_FIELDS]
    if extra:
        print(f"  额外字段: {', '.join(extra)}")


def _write_cookie_to_config(config_path: Path, cookie_str: str) -> None:
    """Write *cookie_str* into ``cookies.xhs`` of *config_path*.

    Preserves the existing ``cookie:`` (singular, legacy douyin) field
    and all other keys. Creates ``cookies:`` block if it doesn't exist.
    """
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    else:
        data = {}

    cookies_block = data.get("cookies")
    if not isinstance(cookies_block, dict):
        cookies_block = {}
        # Migrate legacy `cookie:` → `cookies.douyin` so the new dict
        # form is self-consistent after we add the xhs key.
        legacy = data.pop("cookie", None)
        if isinstance(legacy, str) and legacy.strip():
            cookies_block["douyin"] = legacy
        data["cookies"] = cookies_block

    cookies_block["xhs"] = cookie_str

    with config_path.open("w", encoding="utf-8") as fh:
        yaml.dump(
            data, fh, allow_unicode=True,
            default_flow_style=False, sort_keys=False,
        )


def main() -> None:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config.yml")
    asyncio.run(_harvest(config_path))


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 语法冒烟（不实际启动浏览器）**

```bash
python -c "import xhs_cookie_extractor; print('ok')"
python xhs_cookie_extractor.py --help 2>&1 | head -3 || true
```

实际运行（需要用户交互，所以不在 CI 里跑）：

```bash
# 只在你本地手动执行一次来抓 cookie
python xhs_cookie_extractor.py
```

- [ ] **Step 4: 提交**

```bash
git add xhs_cookie_extractor.py
git commit -m "feat(xhs): Playwright 扫码抓取 XHS cookie 并写入 config.yml"
```

---

## Task 7: 手工验证阶段 A

**无代码变更**，只是跑一遍确认：

- [ ] **Step 1: 跑全部单元测试**

```bash
python -m pytest tests/ -v
```

Expected: 全部通过。

- [ ] **Step 2: 语法 / 导入冒烟**

```bash
python -c "import downloader; import xhs_cookie_extractor; print('ok')"
python downloader.py --help | head -10
```

Expected: 都成功。

- [ ] **Step 3: （可选）本地运行 `xhs_cookie_extractor.py` 扫码登录**

```bash
python xhs_cookie_extractor.py
```

在弹出的浏览器窗口中扫码登录，确认脚本成功提取 cookie 并写入 `config.yml`。事后打开 `config.yml` 验证：

```yaml
cookies:
  douyin: "（原有抖音 cookie）"
  xhs: "a1=...; web_session=...; webId=..."
```

- [ ] **Step 4: 测试 XHS URL 识别能走通 pipeline（但会因为占位 client 抛 NotImplementedError）**

在 `config.yml` 里临时加一条 XHS 链接：

```yaml
links:
  - https://v.douyin.com/cGYAzzSDbRQ/    # 抖音仍正常工作
  - https://xhslink.com/m/5kcCust1t6Z    # XHS URL — 会被识别但下载会抛 NotImplementedError
```

跑 `python downloader.py -c config.yml --no-dashboard`。预期日志中能看到：

```
pipeline   task_001 started (platform=xhs, content_type=short) ...
pipeline   未预期错误: XHS short URL resolution not yet implemented ...
```

即 URL 识别 + routing 成功，只是下载能力还没接入 — 这是阶段 A 的预期终点。

- [ ] **Step 5: 打 tag**

```bash
git tag xhs-plan-2-done
```

---

## Plan 2（阶段 A）完成标准

- [ ] `XHSPlatform` 能识别 `xhslink.com/{a,m,bare}/*` 短链、`explore/{note_id}` / `discovery/item/{note_id}` / `user/profile/{user_id}` / `board/{board_id}` / `search_result?keyword=*` / `page/topics/{topic_id}`
- [ ] `XHSPlatform` 和 `DouyinPlatform` 没有交叉误判
- [ ] `AppConfig.cookies` 类型为 `dict[str, str]`，老 `cookie:` 字段自动迁移
- [ ] `CookieManager.ensure_valid_cookie(platform="xhs")` 返回 XHS CookieState
- [ ] `CookieManager.get_for_url(url)` 按域名路由
- [ ] `xhs_cookie_extractor.py` 能启动 Playwright 扫码抓 cookie 写入 config.yml
- [ ] `downloader.py` 注册了 XHSPlatform + 占位 XHSPlatformClient
- [ ] 全部 100+ 单元测试通过
- [ ] 现有 `config.yml` 无需改动即可继续跑抖音下载

## 交接给阶段 B 的上下文

完成本 Plan 后，以下基础设施就绪供阶段 B 使用：

1. **`core/platforms/xhs.py`** 已有 `XHSPlatform` 和占位 `XHSPlatformClient`，阶段 B 只需重写 `XHSPlatformClient` 的三个方法体（resolve_short_url / fetch_single / fetch_list）
2. **Cookie 已抓好**：`config.yml::cookies.xhs` 含 `a1 / web_session / webId`，阶段 B 签名器从这里读
3. **CookieManager** 的 `ensure_valid_cookie(platform="xhs")` 返回现成的 `CookieState`，XHSAPIClient 可以接这个（就像 `DouyinAPIClient.update_cookie` 一样）

**阶段 B 的主题**：`XHSSigner` (PyMiniRacer + sign.js) + `XHSAPIClient` (feed / user_posted 两个端点) + `note → MediaItem` 转换 + 真实的 `XHSPlatformClient` 方法体 + 跑通秃头金金的用户主页端到端下载。
