# core/platforms/xhs.py
"""XHS (小红书) platform plugin: URL recognition.

Stage A implementation: URL matching only. API client, signer, and
MediaItem conversion arrive in Plan 3.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import httpx

from core.errors import SkippableError
from core.platform import ContentRef, ListPage, MediaAsset, MediaItem


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
                extra=self._extract_xsec(url),
            )

        m = _DISCOVERY_RE.search(url)
        if m:
            return ContentRef(
                platform="xhs",
                content_type="single",
                resource_id=m.group(1),
                resolved_url=url,
                extra=self._extract_xsec(url),
            )

        m = _USER_RE.search(url)
        if m:
            return ContentRef(
                platform="xhs",
                content_type="user",
                resource_id=m.group(1),
                resolved_url=url,
                extra=self._extract_xsec(url),
            )

        m = _BOARD_RE.search(url)
        if m:
            return ContentRef(
                platform="xhs",
                content_type="collection",
                resource_id=m.group(1),
                resolved_url=url,
                extra=self._extract_xsec(url),
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

    @staticmethod
    def _extract_xsec(url: str) -> dict:
        """Pull xsec_token / xsec_source out of a URL's query string."""
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        out: dict = {}
        tok = params.get("xsec_token")
        src = params.get("xsec_source")
        if tok:
            out["xsec_token"] = tok[0]
        if src:
            out["xsec_source"] = src[0]
        return out


def note_to_media_item(note: dict) -> MediaItem:
    """Convert an XHS SSR note dict into a MediaItem.

    Accepts the ``note`` value from
    ``window.__INITIAL_STATE__.note.noteDetailMap[<note_id>]`` — i.e.
    the rich note object, NOT the outer
    ``{comments, currentTime, note, widgets}`` wrapper.

    Handles ``type="video"`` and ``type="normal"`` notes plus live
    photos (动图 — imageList entries whose ``stream.h264[0].masterUrl``
    is set).

    XHS wire format is camelCase (imageList, urlDefault, masterUrl,
    backupUrls, originVideoKey, noteId) — snake_case variants are NOT
    accepted.

    Raises:
        RuntimeError: For ``type="video"`` notes where neither
            ``video.consumer.originVideoKey`` nor
            ``video.media.stream.{h264,h265}`` yields a URL — this
            is the failure mode of JoeanAmier/XHS-Downloader#324.
            Surfaced at task level by the pipeline; does NOT kill
            the batch.
    """
    note_type = note.get("type", "normal")
    assets: list[MediaAsset] = []

    if note_type == "video":
        video_asset = _video_to_asset(note.get("video") or {})
        if video_asset is None:
            note_id = note.get("noteId") or note.get("id") or "?"
            raise RuntimeError(
                f"XHS video URL extraction failed for note {note_id}: "
                f"both originVideoKey fast-path "
                f"(video.consumer.originVideoKey) and stream fallback "
                f"(video.media.stream.h264/h265) yielded nothing. "
                f"Likely API schema drift — inspect note['video'] shape."
            )
        assets.append(video_asset)
        cover_asset = _cover_to_asset(note)
        if cover_asset is not None:
            assets.append(cover_asset)
    else:
        for img in note.get("imageList") or []:
            img_asset = _image_to_asset(img)
            if img_asset is not None:
                assets.append(img_asset)
            live_asset = _live_to_asset(img)
            if live_asset is not None:
                assets.append(live_asset)

    user = note.get("user") or {}
    author = (
        user.get("nickname")
        or user.get("nickName")
        or user.get("name")
        or "unknown"
    )

    # XHS 'time' / 'lastUpdateTime' are Unix milliseconds.
    raw_time = note.get("time") or note.get("lastUpdateTime") or 0
    try:
        raw_time = int(raw_time)
    except (TypeError, ValueError):
        raw_time = 0
    create_time = raw_time / 1000.0 if raw_time > 1e11 else float(raw_time)

    desc = note.get("desc") or note.get("title") or ""

    note_id = note.get("noteId") or note.get("id") or ""

    return MediaItem(
        platform="xhs",
        id=str(note_id),
        author=str(author),
        desc=str(desc),
        create_time=create_time,
        assets=assets,
        raw=note,
    )


def _video_to_asset(video: dict) -> MediaAsset | None:
    """Pick the highest-quality video URL from note.video.

    Preference (matches upstream XHS-Downloader deal_video_link):
      1. ``video.consumer.originVideoKey`` → unwatermarked original on
         sns-video-bd.xhscdn.com. Only exists for some notes.
      2. ``video.media.stream.{h264,h265,av1}[]`` sorted by height desc;
         pick ``backupUrls[0]`` if present else ``masterUrl``.

    Returns None when both paths yield nothing. Caller turns that into
    a RuntimeError with schema-drift diagnostics.
    """
    consumer = video.get("consumer") or {}
    origin_key = consumer.get("originVideoKey")
    if origin_key:
        primary = f"https://sns-video-bd.xhscdn.com/{origin_key}"
        return MediaAsset(
            url=primary, kind="video_main", ext="mp4",
            fallback_urls=_stream_backup_urls(video),
        )

    entries = _collect_stream_entries(video)
    if not entries:
        return None
    entries_sorted = sorted(entries, key=lambda e: e.get("height") or 0)
    best = entries_sorted[-1]
    primary = (best.get("backupUrls") or [None])[0] or best.get("masterUrl")
    if not primary:
        return None

    fallbacks: list[str] = []
    for u in best.get("backupUrls") or []:
        if u and u != primary and u not in fallbacks:
            fallbacks.append(u)
    master = best.get("masterUrl")
    if master and master != primary and master not in fallbacks:
        fallbacks.append(master)
    for e in entries_sorted[:-1]:
        for u in e.get("backupUrls") or []:
            if u and u not in fallbacks and u != primary:
                fallbacks.append(u)
        m = e.get("masterUrl")
        if m and m not in fallbacks and m != primary:
            fallbacks.append(m)

    return MediaAsset(
        url=primary, kind="video_main", ext="mp4",
        fallback_urls=fallbacks,
    )


def _collect_stream_entries(video: dict) -> list[dict]:
    """Flatten video.media.stream.{h264,h265,av1}[] into one list."""
    stream = (video.get("media") or {}).get("stream") or {}
    out: list[dict] = []
    for codec in ("h264", "h265", "av1"):
        out.extend(stream.get(codec) or [])
    return out


def _stream_backup_urls(video: dict) -> list[str]:
    """Gather every stream URL — used as fallback for originVideoKey primary."""
    out: list[str] = []
    for e in _collect_stream_entries(video):
        for u in e.get("backupUrls") or []:
            if u and u not in out:
                out.append(u)
        master = e.get("masterUrl")
        if master and master not in out:
            out.append(master)
    return out


def _image_to_asset(img: dict) -> MediaAsset | None:
    """Extract the static image URL, regenerating to unwatermarked CDN."""
    raw = img.get("urlDefault") or img.get("url") or ""
    if not raw:
        return None

    token = _extract_image_token(raw)
    if token:
        primary = f"https://sns-img-bd.xhscdn.com/{token}"
    else:
        primary = raw.split("!")[0]

    fallbacks: list[str] = []
    for key in ("url", "urlPre"):
        u = img.get(key)
        if u:
            cleaned = u.split("!")[0]
            if cleaned != primary and cleaned not in fallbacks:
                fallbacks.append(cleaned)
    raw_clean = raw.split("!")[0]
    if raw_clean != primary and raw_clean not in fallbacks:
        fallbacks.append(raw_clean)

    ext = "jpg"
    for sniff in (primary, raw):
        low = sniff.split("?")[0].lower()
        if low.endswith(".webp"):
            ext = "webp"
            break
        if low.endswith(".png"):
            ext = "png"
            break

    return MediaAsset(
        url=primary, kind="image", ext=ext, fallback_urls=fallbacks,
    )


def _extract_image_token(url: str) -> str | None:
    """Return the CDN token from an XHS image URL, else None."""
    parts = url.split("/")
    if len(parts) < 6:
        return None
    tail = "/".join(parts[5:])
    token = tail.split("!")[0]
    return token or None


async def _resolve_xhslink(url: str) -> str:
    """Follow one redirect on an xhslink.com short URL.

    Returns the Location header for 3xx responses, else the original URL.
    Mobile UA is used because xhslink serves different Location values
    to desktop vs mobile and we want the mobile form (richer xsec_token).
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1"
        ),
    }
    try:
        async with httpx.AsyncClient(
            follow_redirects=False, timeout=10.0,
        ) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("Location")
                if loc:
                    return loc
    except Exception:
        pass
    return url


def _live_to_asset(img: dict) -> MediaAsset | None:
    """If this imageList entry is a live photo (动图), emit the video."""
    stream = img.get("stream") or {}
    entries = stream.get("h264") or []
    if not entries:
        return None
    first = entries[0]
    master = first.get("masterUrl")
    if not master:
        return None
    fallbacks: list[str] = []
    for u in first.get("backupUrls") or []:
        if u and u != master and u not in fallbacks:
            fallbacks.append(u)
    return MediaAsset(
        url=master, kind="video_live", ext="mp4",
        fallback_urls=fallbacks,
    )


def _cover_to_asset(note: dict) -> MediaAsset | None:
    """Pick the cover URL for a video note from imageList[0]."""
    images = note.get("imageList") or []
    if not images:
        return None
    first = images[0]
    raw = first.get("urlDefault") or first.get("url")
    if not raw:
        return None

    token = _extract_image_token(raw)
    primary = (
        f"https://sns-img-bd.xhscdn.com/{token}"
        if token else raw.split("!")[0]
    )

    fallbacks: list[str] = []
    for key in ("url", "urlPre"):
        u = first.get(key)
        if u:
            cleaned = u.split("!")[0]
            if cleaned != primary and cleaned not in fallbacks:
                fallbacks.append(cleaned)

    return MediaAsset(
        url=primary, kind="cover", ext="jpg", fallback_urls=fallbacks,
    )


class XHSPlatformClient:
    """PlatformClient for XHS using a shared Playwright session.

    XHS PC web SSRs single-note payloads into window.__INITIAL_STATE__
    (no /api/sns/web/v1/feed on note pages anymore). fetch_single
    navigates and reads SSR state via page.evaluate.

    Args:
        session: Started ``XHSBrowserSession`` (cookies injected) or
            ``None`` if no XHS cookie configured. ``None`` is permitted
            so the platform can still register; calls then raise a
            clear RuntimeError naming the missing-cookie cause.

    The client does NOT own the session lifecycle; ``downloader.py``
    is responsible for ``session.start()`` and ``session.close()``.
    """

    _USER_POSTED_ENDPOINT = "/api/sns/web/v1/user_posted"

    # JavaScript evaluated inside the page context to extract a single
    # note from SSR state. Returns the plain ``note`` dict (shedding
    # the {comments, currentTime, note, widgets} wrapper) OR null if
    # the noteId key is missing from noteDetailMap.
    #
    # Vue 3 reactive proxies create cyclic back-refs (``dep``,
    # ``effect``) that break a direct ``return entry.note`` via
    # page.evaluate ("object reference chain is too long"). The
    # cycle-safe JSON round-trip sheds the proxy and returns a plain
    # object that Playwright can serialize back across CDP.
    _NOTE_EXTRACT_JS = """
        (noteId) => {
            const map = window.__INITIAL_STATE__
                && window.__INITIAL_STATE__.note
                && window.__INITIAL_STATE__.note.noteDetailMap;
            if (!map) return null;
            const entry = map[noteId];
            if (!entry || !entry.note) return null;
            const seen = new WeakSet();
            const safe = JSON.stringify(entry.note, (k, v) => {
                if (typeof v === 'object' && v !== null) {
                    if (seen.has(v)) return undefined;
                    seen.add(v);
                }
                return v;
            });
            return JSON.parse(safe);
        }
    """

    def __init__(
        self, session, *,
        hydrate_delay_range: tuple[float, float] = (0.5, 1.0),
    ) -> None:
        self._session = session
        # Random sleep between consecutive fetch_single hydrates in
        # fetch_list, to mimic human browsing cadence and reduce XHS
        # bot-detection signals. Tests pass (0, 0) to skip the delay.
        self._hydrate_delay_range = hydrate_delay_range

    def _require_session(self) -> None:
        if self._session is None:
            raise SkippableError(
                "XHS downloader not available: no XHS cookie configured "
                "(run `python xhs_cookie_extractor.py`)"
            )

    async def resolve_short_url(self, url: str) -> str:
        return await _resolve_xhslink(url)

    async def fetch_single(self, ref: ContentRef, span) -> MediaItem:
        """Fetch one note by reading SSR state after navigating.

        XHS's PC web no longer fires /api/sns/web/v1/feed on note
        detail pages (verified 2026-04-24); the note payload lives at
        ``window.__INITIAL_STATE__.note.noteDetailMap[<id>].note``.
        """
        import asyncio

        self._require_session()
        del span  # SSR read has no inner API call to attribute
        target = self._build_explore_url(ref)
        note_id = ref.resource_id

        async with self._session.page() as pg:
            await pg.goto(target, wait_until="domcontentloaded")
            note = None
            for _ in range(15):
                note = await pg.evaluate(self._NOTE_EXTRACT_JS, note_id)
                if note is not None:
                    break
                await asyncio.sleep(0.2)

        if note is None:
            raise RuntimeError(
                f"XHS SSR state missing for note {note_id}: "
                f"window.__INITIAL_STATE__.note.noteDetailMap[{note_id}] "
                f"is empty. Possible causes: (a) note deleted, "
                f"(b) cookie risk-controlled (check for captcha), "
                f"(c) XHS changed the state key layout — inspect "
                f"window.__INITIAL_STATE__ in devtools to confirm."
            )
        return note_to_media_item(note)

    async def fetch_list(
        self, ref: ContentRef, cursor, span, *, limit: int = 0,
    ) -> ListPage:
        """Fetch all notes for a user profile and hydrate each via SSR.

        XHS's /user_posted responses are triggered by scroll on the SPA;
        we auto-scroll to bottom, collecting every response, then make
        a second pass calling fetch_single for each note to get media
        URLs (single-note SSR state is the only path to full URLs).

        The ``cursor`` parameter is ignored: we always return the full
        list in one call with ``has_more=False``. Pipeline's cursor
        loop naturally exits after one iteration.

        ``limit`` (when > 0) caps how many notes we hydrate. Hydration is
        expensive — one chromium navigation per note (~1.3s) — so honoring
        the hint takes a smoke-test from ~9 minutes (405 notes) to ~30s
        (3 notes). Without it, pipeline-level limit only saves the
        download phase, not the hydrate phase.
        """
        del cursor  # XHS pagination is scroll-driven, not cursor-driven.

        if ref.content_type != "user":
            raise ValueError(
                f"XHS fetch_list currently supports content_type='user', "
                f"got {ref.content_type!r}"
            )

        listings = await self._collect_user_listings(ref)

        items: list[MediaItem] = []
        for note_stub in listings:
            if limit > 0 and len(items) >= limit:
                break
            # /user_posted uses snake_case while SSR uses camelCase —
            # accept both so this code survives a future API alignment.
            note_id = (
                note_stub.get("noteId")
                or note_stub.get("note_id")
                or note_stub.get("id")
            )
            if not note_id:
                continue
            xsec = (
                note_stub.get("xsec_token")
                or note_stub.get("xsecToken")
                or ""
            )
            sub_ref = ContentRef(
                platform="xhs",
                content_type="single",
                resource_id=str(note_id),
                resolved_url="",
                extra={"xsec_token": xsec, "xsec_source": "pc_user"},
            )
            try:
                items.append(await self.fetch_single(sub_ref, span))
            except Exception as exc:
                # Skip notes that fail (deleted, private, #324-style
                # schema drift on a single item, etc.) — don't let one
                # bad note kill the whole batch. The error is recorded
                # in the per-task tracer span if available.
                if span is not None:
                    try:
                        span.attributes[f"skip_{note_id}"] = str(exc)[:200]
                    except Exception:
                        pass
                continue
            # Pace consecutive hydrates so we don't burst-navigate the
            # explore page. Skipped on failure (no real navigation
            # happened in fetch_single beyond the first redirect).
            await self._sleep_between_hydrates()

        return ListPage(items=items, next_cursor=None, has_more=False)

    async def _sleep_between_hydrates(self) -> None:
        import asyncio
        import random
        lo, hi = self._hydrate_delay_range
        if hi <= 0:
            return
        await asyncio.sleep(random.uniform(lo, hi))

    async def _collect_user_listings(self, ref: ContentRef) -> list[dict]:
        """Scroll the profile page collecting /user_posted note stubs.

        Stops when the API signals ``has_more=False`` OR three
        consecutive scrolls produce no new note IDs OR the safety
        cap (40 scrolls ≈ 1200 notes) is hit.
        """
        import asyncio

        self._require_session()
        target = self._build_profile_url(ref)
        collected: dict[str, dict] = {}  # note_id -> stub
        done = asyncio.Event()
        max_quiet = 3
        max_total_scrolls = 40

        async with self._session.page() as pg:
            def _on_response(resp):
                if self._USER_POSTED_ENDPOINT not in resp.url:
                    return

                async def _consume():
                    try:
                        body = await resp.json()
                    except Exception:
                        return
                    data = body.get("data") or {}
                    notes = data.get("notes") or []
                    for n in notes:
                        nid = (
                            n.get("noteId")
                            or n.get("note_id")
                            or n.get("id")
                        )
                        if nid:
                            collected[str(nid)] = n
                    has_more = data.get("has_more")
                    if has_more is None:
                        has_more = data.get("hasMore")
                    if has_more is False:
                        done.set()

                asyncio.create_task(_consume())

            pg.on("response", _on_response)
            await pg.goto(target, wait_until="domcontentloaded")
            await asyncio.sleep(1.5)  # initial SSR settle

            quiet_scrolls = 0
            for _ in range(max_total_scrolls):
                if done.is_set():
                    break
                before = len(collected)
                await pg.evaluate(
                    "window.scrollTo(0, document.body.scrollHeight)"
                )
                await asyncio.sleep(1.2)
                if len(collected) == before:
                    quiet_scrolls += 1
                    if quiet_scrolls >= max_quiet:
                        break
                else:
                    quiet_scrolls = 0

        return list(collected.values())

    def _build_explore_url(self, ref: ContentRef) -> str:
        base = f"https://www.xiaohongshu.com/explore/{ref.resource_id}"
        params: list[str] = []
        tok = ref.extra.get("xsec_token") if ref.extra else None
        src = ref.extra.get("xsec_source") if ref.extra else None
        if tok:
            params.append(f"xsec_token={tok}")
        if src:
            params.append(f"xsec_source={src}")
        return f"{base}?{'&'.join(params)}" if params else base

    def _build_profile_url(self, ref: ContentRef) -> str:
        base = f"https://www.xiaohongshu.com/user/profile/{ref.resource_id}"
        params: list[str] = []
        tok = ref.extra.get("xsec_token") if ref.extra else None
        src = ref.extra.get("xsec_source") if ref.extra else None
        if tok:
            params.append(f"xsec_token={tok}")
        if src:
            params.append(f"xsec_source={src}")
        return f"{base}?{'&'.join(params)}" if params else base
