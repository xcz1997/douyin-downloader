"""Async Douyin API client with rate limiting, retry logic, and trace spans."""

import asyncio
import copy
import json
import re
import time

import aiohttp

import apiproxy
from apiproxy.common.utils import Utils
from apiproxy.douyin.result import Result
from apiproxy.douyin.urls import Urls
from core.errors import (
    ContentNotFoundError,
    CookieExpiredError,
    NetworkError,
    RateLimitError,
)
from core.logger import BoundLogger
from core.models import CookieState, TraceSpan
from core.tracer import Tracer

# The web aweme/detail API is gated (returns non-JSON even when logged
# in). The iesdouyin share page still server-renders the full aweme into
# window._ROUTER_DATA, but only for a mobile UA — a desktop UA gets
# redirected to the JS SPA which has no embedded data.
_SHARE_URL = "https://www.iesdouyin.com/share/video/{}/"
_SHARE_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
    "Mobile/15E148 Safari/604.1"
)
_ROUTER_DATA_RE = re.compile(
    r"_ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>", re.S
)


def _extract_ssr_aweme(html: str) -> dict:
    """Pull the raw aweme dict out of an iesdouyin share page.

    The aweme lives at ``_ROUTER_DATA.loaderData[<*/page>]
    .videoInfoRes.item_list[0]`` and has the same shape as an
    ``aweme_list`` item, so callers can feed it through the normal
    parse path.

    Raises:
        ContentNotFoundError: No ``_ROUTER_DATA`` or no item in it.
    """
    m = _ROUTER_DATA_RE.search(html)
    if not m:
        raise ContentNotFoundError("分享页无 _ROUTER_DATA（可能被重定向）")
    try:
        router = json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        raise ContentNotFoundError(f"_ROUTER_DATA 解析失败: {exc}") from exc
    for value in (router.get("loaderData") or {}).values():
        if not isinstance(value, dict):
            continue
        items = (value.get("videoInfoRes") or {}).get("item_list") or []
        if items:
            return items[0]
    raise ContentNotFoundError("分享页 _ROUTER_DATA 无 item_list")


class RateLimiter:
    """Token-bucket rate limiter enforcing a minimum interval between calls.

    Args:
        max_per_second: Maximum number of requests allowed per second.
    """

    def __init__(self, max_per_second: float = 2.0) -> None:
        self._interval: float = 1.0 / max_per_second
        self._last: float = 0.0
        self._lock: asyncio.Lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until the next request slot is available."""
        async with self._lock:
            now = time.time()
            wait = self._interval - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.time()


class DouyinAPIClient:
    """Async HTTP client for the Douyin web API.

    Handles rate limiting, automatic retries on transient errors, and emits
    OpenTelemetry-style trace spans for every outbound request.

    Args:
        cookie_state: Active cookie credential.
        tracer: Tracer instance used to record spans.
        logger: Bound logger for structured log output.
        rate_limit: Maximum requests per second (default 2.0).
        max_retries: Number of retry attempts on retryable errors (default 3).
    """

    def __init__(
        self,
        cookie_state: CookieState,
        tracer: Tracer,
        logger: BoundLogger,
        rate_limit: float = 2.0,
        max_retries: int = 3,
    ) -> None:
        self._cookie = cookie_state
        self._tracer = tracer
        self._log = logger
        self._rate_limiter = RateLimiter(rate_limit)
        self._max_retries = max_retries
        self._retry_delays: list[int] = [1, 2, 5]
        self._session: aiohttp.ClientSession | None = None
        self._urls = Urls()
        self._result = Result()
        self._utils = Utils()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _ensure_session(self) -> None:
        """Lazily create the aiohttp session if it is absent or closed."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
            )

    def _headers(self) -> dict[str, str]:
        """Build the HTTP headers required by the Douyin web API."""
        cookie_val = self._cookie.value if self._cookie else ""
        return {
            "User-Agent": apiproxy.ua,
            "Referer": "https://www.douyin.com/",
            "accept-encoding": "gzip, deflate",
            "Cookie": cookie_val,
        }

    async def _request(
        self,
        parent_span: TraceSpan,
        name: str,
        url: str,
        parse_fn=None,
        **attrs,
    ) -> dict:
        """Execute an authenticated GET request inside a child trace span.

        Retries on ``aiohttp.ClientError``, ``asyncio.TimeoutError``, and
        ``json.JSONDecodeError``; propagates ``CookieExpiredError`` and
        ``RateLimitError`` immediately without retrying.

        Args:
            parent_span: The parent trace span for the new child span.
            name: Span name (e.g. ``"api_get_video"``).
            url: Full URL including X-Bogus query string.
            parse_fn: Optional callable that receives the raw ``dict`` and
                returns the final result. May raise ``ContentNotFoundError``.
            **attrs: Additional span attributes.

        Returns:
            Parsed response payload as a ``dict``.

        Raises:
            CookieExpiredError: HTTP 403 received.
            RateLimitError: HTTP 429 received.
            NetworkError: All retry attempts exhausted.
        """
        await self._ensure_session()
        with self._tracer.context_span(parent_span, name, **attrs) as span:
            await self._rate_limiter.acquire()
            self._tracer.add_event(span, "rate_limit_passed")

            last_error: Exception | None = None
            for attempt in range(self._max_retries + 1):
                try:
                    span.attributes["attempt"] = attempt + 1
                    async with self._session.get(url, headers=self._headers()) as resp:
                        span.attributes["status_code"] = resp.status

                        if resp.status == 403:
                            self._tracer.add_event(span, "blocked", status=403)
                            raise CookieExpiredError("403 Forbidden")

                        if resp.status == 429:
                            self._tracer.add_event(span, "rate_limited", status=429)
                            raise RateLimitError("429 Too Many Requests")

                        if resp.status != 200:
                            raise NetworkError(f"HTTP {resp.status}")

                        text = await resp.text()
                        data = json.loads(text)

                        if parse_fn:
                            return parse_fn(data)
                        return data

                except (CookieExpiredError, RateLimitError):
                    raise
                except (
                    aiohttp.ClientError,
                    asyncio.TimeoutError,
                    json.JSONDecodeError,
                ) as exc:
                    last_error = exc
                    self._tracer.add_event(
                        span, "retry", attempt=attempt + 1, error=str(exc)
                    )
                    self._log.warn("请求重试", attempt=attempt + 1, error=str(exc))
                    if attempt < self._max_retries:
                        delay = self._retry_delays[
                            min(attempt, len(self._retry_delays) - 1)
                        ]
                        await asyncio.sleep(delay)
                    else:
                        raise NetworkError(
                            f"重试{self._max_retries}次后失败: {last_error}"
                        )

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    async def get_video_info(
        self, aweme_id: str, parent_span: TraceSpan
    ) -> dict:
        """Fetch and parse a single video/image post by its aweme ID.

        Args:
            aweme_id: The numeric string ID of the post.
            parent_span: Parent trace span.

        Returns:
            Parsed ``awemeDict`` for the post.

        Raises:
            ContentNotFoundError: The post was not found in the response.
        """
        params = f"aweme_id={aweme_id}&device_platform=webapp&aid=6383"
        url = self._urls.POST_DETAIL + self._utils.getXbogus(params)

        def parse(data: dict) -> dict:
            detail = data.get("aweme_detail")
            if not detail:
                raise ContentNotFoundError(f"aweme_id={aweme_id} 未找到")
            self._result.clearDict(self._result.awemeDict)
            aweme_type = 1 if detail.get("images") else 0
            self._result.dataConvert(aweme_type, self._result.awemeDict, detail)
            return copy.deepcopy(self._result.awemeDict)

        try:
            return await self._request(
                parent_span, "api_get_video", url,
                parse_fn=parse, aweme_id=aweme_id,
            )
        except (NetworkError, ContentNotFoundError):
            # aweme/detail API gave nothing usable — fall back to the
            # iesdouyin share-page SSR. It yields a raw aweme dict (same
            # shape as an aweme_list item), which is what fetch_single ->
            # aweme_to_media_item expects, so return it directly without
            # the legacy dataConvert path.
            return await self._fetch_share_ssr(aweme_id, parent_span)

    async def _fetch_share_ssr(
        self, aweme_id: str, parent_span: TraceSpan
    ) -> dict:
        """Fetch a single aweme from the iesdouyin share SSR page.

        Pure-HTTP fallback (mobile UA, no cookie/X-Bogus needed) for when
        the gated web aweme/detail API returns non-JSON.
        """
        await self._ensure_session()
        with self._tracer.context_span(
            parent_span, "share_ssr_fallback", aweme_id=aweme_id
        ):
            async with self._session.get(
                _SHARE_URL.format(aweme_id),
                headers={"User-Agent": _SHARE_MOBILE_UA},
            ) as resp:
                if resp.status != 200:
                    raise NetworkError(f"分享页 HTTP {resp.status}")
                html = await resp.text()
        return _extract_ssr_aweme(html)

    async def get_user_posts(
        self, sec_uid: str, cursor: int, parent_span: TraceSpan
    ) -> dict:
        """Fetch a page of a user's published posts.

        Args:
            sec_uid: The user's ``sec_uid`` identifier.
            cursor: Pagination cursor (0 for the first page).
            parent_span: Parent trace span.

        Returns:
            Raw API response dict.
        """
        params = (
            f"sec_user_id={sec_uid}&count=35&max_cursor={cursor}"
            f"&device_platform=webapp&aid=6383&channel=channel_pc_web"
            f"&pc_client_type=1&version_code=170400&version_name=17.4.0"
            f"&cookie_enabled=true&screen_width=1920&screen_height=1080"
            f"&browser_language=zh-CN&browser_platform=MacIntel"
            f"&browser_name=Chrome&browser_version=122.0.0.0"
        )
        url = self._urls.USER_POST + self._utils.getXbogus(params)
        return await self._request(
            parent_span, "api_user_posts", url, sec_uid=sec_uid, cursor=cursor
        )

    async def get_user_likes(
        self, sec_uid: str, cursor: int, parent_span: TraceSpan
    ) -> dict:
        """Fetch a page of a user's liked posts.

        Args:
            sec_uid: The user's ``sec_uid`` identifier.
            cursor: Pagination cursor (0 for the first page).
            parent_span: Parent trace span.

        Returns:
            Raw API response dict.
        """
        params = (
            f"sec_user_id={sec_uid}&count=35&max_cursor={cursor}"
            f"&device_platform=webapp&aid=6383"
        )
        url = self._urls.USER_FAVORITE_A + self._utils.getXbogus(params)
        return await self._request(
            parent_span, "api_user_likes", url, sec_uid=sec_uid, cursor=cursor
        )

    async def get_mix_list(
        self, sec_uid: str, parent_span: TraceSpan
    ) -> list[dict]:
        """Fetch all playlist (mix) metadata for a user.

        Args:
            sec_uid: The user's ``sec_uid`` identifier.
            parent_span: Parent trace span.

        Returns:
            List of mix info dicts (may be empty).
        """
        params = f"sec_user_id={sec_uid}&device_platform=webapp&aid=6383"
        url = self._urls.USER_MIX_LIST + self._utils.getXbogus(params)
        data = await self._request(
            parent_span, "api_mix_list", url, sec_uid=sec_uid
        )
        return data.get("mix_infos", [])

    async def get_mix_items(
        self, mix_id: str, cursor: int, parent_span: TraceSpan
    ) -> dict:
        """Fetch a page of posts belonging to a playlist.

        Args:
            mix_id: The playlist ID string.
            cursor: Pagination cursor (0 for the first page).
            parent_span: Parent trace span.

        Returns:
            Raw API response dict.
        """
        params = (
            f"mix_id={mix_id}&count=35&cursor={cursor}"
            f"&device_platform=webapp&aid=6383"
        )
        url = self._urls.USER_MIX + self._utils.getXbogus(params)
        return await self._request(
            parent_span, "api_mix_items", url, mix_id=mix_id, cursor=cursor
        )

    async def get_music_items(
        self, music_id: str, cursor: int, parent_span: TraceSpan
    ) -> dict:
        """Fetch a page of posts that use a particular music track.

        Args:
            music_id: The music track ID string.
            cursor: Pagination cursor (0 for the first page).
            parent_span: Parent trace span.

        Returns:
            Raw API response dict.
        """
        params = (
            f"music_id={music_id}&count=35&cursor={cursor}"
            f"&device_platform=webapp&aid=6383"
        )
        url = self._urls.MUSIC + self._utils.getXbogus(params)
        return await self._request(
            parent_span, "api_music_items", url, music_id=music_id, cursor=cursor
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def update_cookie(self, cookie_state: CookieState) -> None:
        """Replace the active cookie credential.

        Args:
            cookie_state: New cookie to use for subsequent requests.
        """
        self._cookie = cookie_state

    async def close(self) -> None:
        """Close the underlying aiohttp session and release its connections."""
        if self._session and not self._session.closed:
            await self._session.close()
