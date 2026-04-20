"""Interactive guided Cookie manager for Douyin downloader.

Attempts to acquire a valid cookie through a waterfall of strategies:
config file → local browser → Playwright automation → manual input.
"""

from __future__ import annotations

import time

from core.errors import CookieExpiredError
from core.models import AppConfig, CookieState

_REQUIRED_FIELDS: list[str] = ["ttwid"]
_IMPORTANT_FIELDS: list[str] = [
    "sessionid",
    "sessionid_ss",
    "passport_csrf_token",
    "msToken",
]


class CookieManager:
    """Manages cookie acquisition and validation through multiple fallback strategies.

    Attributes:
        _config: Application configuration containing cookie settings.
        _tracer: Optional tracer for distributed tracing spans.
        _log: Logger instance for diagnostic output.
        _state: Current cookie state, set after successful acquisition.
    """

    def __init__(self, config: AppConfig, tracer, logger) -> None:
        self._config = config
        self._tracer = tracer
        self._log = logger
        self._state: CookieState | None = None

    @property
    def state(self) -> CookieState | None:
        """The current validated cookie state, or None if not yet acquired."""
        return self._state

    async def ensure_valid_cookie(self) -> CookieState:
        """Acquire a valid cookie using a prioritised waterfall of strategies.

        Tries each source in order: config, browser, playwright, manual input.

        Returns:
            A validated CookieState from the first successful source.

        Raises:
            CookieExpiredError: When all acquisition strategies are exhausted.
        """
        steps: list[tuple[str, object]] = [
            ("配置文件", self._try_config),
            ("本地浏览器", self._try_browser),
            ("Playwright", self._try_playwright),
            ("手动输入", self._try_manual),
        ]
        for name, fn in steps:
            if self._log:
                self._log.info(f"Cookie 检测: {name}...")
            result = await fn()
            if result and result.is_valid:
                self._state = result
                if self._log:
                    self._log.info(f"Cookie 有效", source=result.source)
                return result
            if self._log:
                self._log.debug(f"Cookie 检测: {name} 未通过")

        raise CookieExpiredError("无法获取有效 Cookie，所有方式均失败")

    async def _try_config(self) -> CookieState | None:
        """Attempt to load and validate a cookie from the config file.

        Returns:
            A CookieState if the config cookie has required fields, else None.
            Online validation failure is a warning, not a blocker.
        """
        state = self._state_from_config()
        if not state:
            return None
        parsed = self.parse_cookie_string(state.value)
        missing, _ = self.check_cookie_fields(parsed)
        if missing:
            if self._log:
                self._log.debug("配置 Cookie 缺少必需字段", missing=missing)
            return None
        valid, reason = await self.validate(state.value)
        state.is_valid = valid
        state.last_checked = time.time()
        if not valid and self._log:
            self._log.warn("Cookie 在线验证未通过，但字段完整，继续使用", reason=reason)
        return state

    async def _try_browser(self) -> CookieState | None:
        """Attempt to extract a cookie from the local browser cookie store.

        Returns:
            A valid CookieState extracted from Chrome/Edge, or None.
        """
        cookie_str = self.extract_from_browser()
        if not cookie_str:
            return None
        valid, _reason = await self.validate(cookie_str)
        if valid:
            return CookieState(
                value=cookie_str,
                source="browser",
                obtained_at=time.time(),
                is_valid=True,
                last_checked=time.time(),
            )
        return None

    async def _try_playwright(self) -> CookieState | None:
        """Attempt to acquire a cookie via Playwright browser automation.

        Returns:
            A CookieState from Playwright, or None if unavailable or failed.
        """
        try:
            from apiproxy.douyin.auth.cookie_manager import AutoCookieManager

            mgr = AutoCookieManager()
            cookies = mgr.get_cookies()
            if cookies:
                cookie_str = (
                    cookies
                    if isinstance(cookies, str)
                    else "; ".join(f"{k}={v}" for k, v in cookies.items())
                )
                return CookieState(
                    value=cookie_str,
                    source="playwright",
                    obtained_at=time.time(),
                    is_valid=True,
                    last_checked=time.time(),
                )
        except ImportError:
            pass
        except Exception:
            pass
        return None

    async def _try_manual(self) -> CookieState | None:
        """Prompt the user to paste a cookie string interactively.

        Returns:
            A CookieState from user input if required fields are present, else None.
        """
        print("\n" + "=" * 50)
        print("请手动提供 Cookie:")
        print("  1. 打开浏览器访问 douyin.com 并登录")
        print("  2. 按 F12 → Network → 刷新页面")
        print("  3. 点击任意请求 → Headers → 复制 Cookie 值")
        print("=" * 50)
        try:
            cookie_str = input("\n粘贴 Cookie: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if not cookie_str:
            return None
        parsed = self.parse_cookie_string(cookie_str)
        missing, warnings = self.check_cookie_fields(parsed)
        if missing:
            print(f"缺少必需字段: {', '.join(missing)}")
            return None
        for w in warnings:
            print(f"  缺少推荐字段: {w}")
        return CookieState(
            value=cookie_str,
            source="manual",
            obtained_at=time.time(),
            is_valid=True,
            last_checked=time.time(),
        )

    async def validate(self, cookie_str: str) -> tuple[bool, str]:
        """Validate a cookie string against the Douyin API.

        Args:
            cookie_str: Raw cookie string to validate.

        Returns:
            A tuple of (is_valid, reason) where reason is "ok" on success or
            an error description on failure.
        """
        parsed = self.parse_cookie_string(cookie_str)
        missing, _ = self.check_cookie_fields(parsed)
        if missing:
            return (False, f"missing_fields: {missing}")

        import aiohttp

        try:
            url = (
                "https://www.douyin.com/aweme/v1/web/im/resources/"
                "?device_platform=webapp&aid=6383"
            )
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36"
                ),
                "Referer": "https://www.douyin.com/",
                "Cookie": cookie_str,
            }
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 403:
                        return (False, "blocked")
                    try:
                        data = await resp.json(content_type=None)
                    except Exception:
                        return (False, f"non_json_response, status={resp.status}")
                    if data.get("status_code") == 0:
                        return (True, "ok")
                    return (False, f"status_code={data.get('status_code')}")
        except Exception as exc:
            return (False, str(exc))

    def _state_from_config(self) -> CookieState | None:
        """Build a CookieState from the raw cookie value in config.

        Handles both string and dict forms of the cookies field.

        Returns:
            A CookieState if a usable cookie string is found, else None.
        """
        raw = self._config.cookies
        if not raw:
            return None
        if isinstance(raw, dict):
            cookie_str = "; ".join(f"{k}={v}" for k, v in raw.items())
        else:
            cookie_str = str(raw).strip()
        if not cookie_str or cookie_str == "auto":
            return None
        return CookieState(
            value=cookie_str,
            source="config",
            obtained_at=time.time(),
        )

    def extract_from_browser(self) -> str | None:
        """Extract Douyin cookies from Chrome or Edge local cookie stores.

        Returns:
            A semicolon-separated cookie string if ttwid is present, else None.
        """
        try:
            import browser_cookie3

            for browser_fn in [browser_cookie3.chrome, browser_cookie3.edge]:
                try:
                    cj = browser_fn(domain_name=".douyin.com")
                    cookies = {c.name: c.value for c in cj}
                    if cookies.get("ttwid"):
                        return "; ".join(f"{k}={v}" for k, v in cookies.items())
                except Exception:
                    continue
        except ImportError:
            pass
        return None

    @staticmethod
    def parse_cookie_string(raw: str) -> dict[str, str]:
        """Parse a raw cookie header string into a key-value mapping.

        Handles quoted values and strips surrounding whitespace.

        Args:
            raw: A semicolon-delimited cookie string, e.g. "key=val; key2=val2".

        Returns:
            A dict mapping cookie name to cookie value.
        """
        result: dict[str, str] = {}
        for part in raw.split(";"):
            part = part.strip()
            if "=" in part:
                key, _, value = part.partition("=")
                result[key.strip()] = value.strip().strip('"')
        return result

    @staticmethod
    def check_cookie_fields(parsed: dict[str, str]) -> tuple[list[str], list[str]]:
        """Check a parsed cookie dict for required and recommended fields.

        Args:
            parsed: Dict of cookie name to value as returned by parse_cookie_string.

        Returns:
            A tuple of (missing_required, missing_recommended) field name lists.
        """
        missing = [f for f in _REQUIRED_FIELDS if f not in parsed]
        warnings = [f for f in _IMPORTANT_FIELDS if f not in parsed]
        return missing, warnings
