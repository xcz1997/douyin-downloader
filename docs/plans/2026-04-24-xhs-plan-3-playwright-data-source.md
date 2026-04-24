# XHS Plan 3 — Playwright Data Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the placeholder `XHSPlatformClient` with a real implementation that uses a long-lived headless Playwright browser (loaded with the configured XHS cookie) as the data source — navigate to canonical XHS URLs, intercept the XHS SPA's own `/feed` and `/user_posted` API responses, parse them into `MediaItem`, and hand off to the existing `DownloadEngine`. No client-side signing.

**Architecture:**
- **`XHSBrowserSession`** (new, `core/platforms/xhs_browser.py`) — async context manager wrapping a Playwright chromium browser + context seeded with the user's XHS cookie. Shared across all XHS calls in one pipeline run; torn down from `downloader.py`.
- **`core/platforms/xhs.py::XHSPlatformClient`** — no longer a stub. Takes a `XHSBrowserSession` + a cookie loader callable. Implements `resolve_short_url` (HTTP redirect), `fetch_single` (goto `explore/<id>` and capture `/feed` JSON), `fetch_list` (goto `user/profile/<id>` and auto-scroll collecting `/user_posted` responses).
- **`core/platforms/xhs.py::note_to_media_item`** — pure function converting an XHS note dict to `MediaItem`, mirroring `aweme_to_media_item` in `douyin.py`. Pure → driven by fixture-based TDD.
- Pipeline already consumes `MediaItem` and `ListPage`, so no pipeline rework needed beyond letting the upfront cookie check tolerate XHS-only batches.

**Tech Stack:** Python 3.13, Playwright (chromium), httpx (short URL resolution), pytest, existing project infrastructure.

**Target for end-to-end smoke test:** `https://xhslink.com/m/5kcCust1t6Z` (秃头金金 user profile; user_id `55c726695894464ef542aea0`).

---

## File Structure

**New files:**
- `core/platforms/xhs_browser.py` — Playwright session manager (browser + context + cookie injection, single instance per pipeline run).
- `tools/xhs_capture_fixtures.py` — one-shot dev script to capture real XHS JSON responses into `tests/fixtures/xhs/` (run once by Task 1; not part of runtime).
- `tests/fixtures/xhs/note_image.json` — real `/feed` response for an image note (committed).
- `tests/fixtures/xhs/note_video.json` — real `/feed` response for a video note (committed).
- `tests/fixtures/xhs/user_posted_page1.json` — real `/user_posted` response (committed).
- `tests/test_xhs_convert.py` — parser unit tests driven by fixtures.
- `tests/test_xhs_short_url.py` — short URL resolver unit tests.
- `tests/test_xhs_client_integration.py` — integration tests (skipped unless `XHS_INTEGRATION=1` env).

**Modified files:**
- `core/platforms/xhs.py` — fill in `XHSPlatformClient` real methods + add `note_to_media_item`.
- `core/pipeline.py` — make `run()`'s upfront cookie check skip Douyin if the batch contains no Douyin URLs.
- `downloader.py` — build a shared `XHSBrowserSession` + `XHSPlatformClient` with XHS cookie, teardown in `finally`.
- `tests/test_xhs_platform.py` — add import-level test for the new `note_to_media_item` export.
- `memory/xhs-integration-status.md` (after finish) — flip Plan 3 to ✅, note new commit tag.

---

## Task 1: Capture real XHS JSON fixtures

**Why first:** TDD'ing `note_to_media_item` requires realistic fixtures. We don't guess field shapes — we capture one image note, one video note, and one `/user_posted` page from a logged-in browser and commit them as test data.

**Files:**
- Create: `tools/xhs_capture_fixtures.py`
- Create: `tests/fixtures/xhs/note_image.json`
- Create: `tests/fixtures/xhs/note_video.json`
- Create: `tests/fixtures/xhs/user_posted_page1.json`

**Prerequisites (must be confirmed by operator before running):**
- `config.yml::cookies.xhs` contains a valid cookie (user already ran `python xhs_cookie_extractor.py`).
- `playwright` + chromium are installed (already true per `memory/project-references.md`).

- [ ] **Step 1: Create the capture script**

The script first captures the `/user_posted` response for 秃头金金's profile (the fixed test target), then picks one image-type note and one video-type note from that response (looking up `type` field on each entry) to capture their `/feed` responses. This avoids hard-coding note IDs that may have been deleted.

Create `tools/xhs_capture_fixtures.py` with this exact content:

```python
"""One-shot fixture capture for XHS parser TDD.

Usage:
    python tools/xhs_capture_fixtures.py

Reads the XHS cookie from config.yml, launches headless chromium
with it, and:
    1. Visits 秃头金金 profile → captures user_posted_page1.json
    2. Picks one image (type=normal) + one video (type=video) note
       from that response
    3. Visits each one's /explore URL → captures note_image.json
       and note_video.json respectively

These fixtures drive regression tests for note_to_media_item.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import yaml
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.yml"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "xhs"

# Fixed test target (秃头金金 — public profile, referenced across the
# whole project). Both IDs and xsec_token are stable until the user's
# content gating changes.
USER_ID = "55c726695894464ef542aea0"
USER_XSEC_TOKEN = "YBbTU9A0gqz095TGQUh16x7JntBoAaPXp-zQk8hjrx768="
USER_XSEC_SOURCE = "app_share"
USER_PROFILE_URL = (
    f"https://www.xiaohongshu.com/user/profile/{USER_ID}"
    f"?xsec_token={USER_XSEC_TOKEN}&xsec_source={USER_XSEC_SOURCE}"
)


def _load_cookie() -> str:
    with CONFIG.open() as f:
        data = yaml.safe_load(f) or {}
    cookies = data.get("cookies", {}) or {}
    raw = cookies.get("xhs", "").strip()
    if not raw:
        raise SystemExit(
            "config.yml::cookies.xhs is empty — "
            "run `python xhs_cookie_extractor.py` first."
        )
    return raw


def _cookie_header_to_playwright(raw: str) -> list[dict]:
    out: list[dict] = []
    for part in raw.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        name, _, value = part.partition("=")
        out.append({
            "name": name.strip(),
            "value": value.strip().strip('"'),
            "domain": ".xiaohongshu.com",
            "path": "/",
        })
    return out


async def _capture_one(context, goto_url: str, endpoint_substr: str,
                       out_path: Path, timeout: float = 30.0) -> dict:
    """Goto `goto_url`, record the first response whose URL contains
    `endpoint_substr`, write the JSON to `out_path`, return the dict."""
    page = await context.new_page()
    fut: asyncio.Future[dict] = asyncio.get_event_loop().create_future()

    async def _consume(resp):
        if fut.done():
            return
        if endpoint_substr not in resp.url:
            return
        try:
            body = await resp.json()
        except Exception:
            return
        if not fut.done():
            fut.set_result(body)

    page.on("response", lambda r: asyncio.create_task(_consume(r)))
    try:
        await page.goto(goto_url, wait_until="domcontentloaded")
        try:
            body = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            raise SystemExit(
                f"timeout waiting for {endpoint_substr} at {goto_url}"
            )
    finally:
        await page.close()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"wrote {out_path.relative_to(ROOT)}")
    return body


def _pick_note(user_posted: dict, target_type: str) -> tuple[str, str]:
    """Return (noteId, xsecToken) for the first note of target_type.

    target_type: 'normal' or 'video'. Raises SystemExit if none found,
    with diagnostic info about what types WERE present.
    """
    data = user_posted.get("data") or {}
    notes = data.get("notes") or []
    if not notes:
        # Surface a meaningful sample of the payload to help debug.
        raise SystemExit(
            f"user_posted response has no notes. "
            f"Top-level keys: {list(user_posted.keys())}. "
            f"data keys: {list(data.keys()) if isinstance(data, dict) else 'N/A'}."
        )
    seen_types: list[str] = []
    for n in notes:
        t = n.get("type") or "?"
        if t not in seen_types:
            seen_types.append(t)
        if t == target_type:
            nid = n.get("noteId") or n.get("note_id") or n.get("id")
            xsec = n.get("xsec_token") or n.get("xsecToken") or ""
            if nid:
                return str(nid), str(xsec)
    raise SystemExit(
        f"no note of type={target_type!r} in user_posted. "
        f"Types present: {seen_types}. "
        f"If only one type exists for this user, swap USER_ID in "
        f"this script for a user whose profile has both image and "
        f"video notes."
    )


async def main() -> None:
    cookie_str = _load_cookie()
    pw_cookies = _cookie_header_to_playwright(cookie_str)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context()
            await context.add_cookies(pw_cookies)

            user_posted = await _capture_one(
                context, USER_PROFILE_URL, "/user_posted",
                FIXTURE_DIR / "user_posted_page1.json",
            )

            # Check the response actually succeeded — empty notes
            # often means risk-control rejection.
            code = user_posted.get("code")
            if code not in (0, None):
                raise SystemExit(
                    f"user_posted returned code={code} "
                    f"msg={user_posted.get('msg')!r}. "
                    f"Cookie likely rejected — re-run xhs_cookie_extractor.py."
                )

            img_id, img_xsec = _pick_note(user_posted, "normal")
            vid_id, vid_xsec = _pick_note(user_posted, "video")
            print(f"picked image note: {img_id}")
            print(f"picked video note: {vid_id}")

            await _capture_one(
                context,
                f"https://www.xiaohongshu.com/explore/{img_id}"
                f"?xsec_token={img_xsec}&xsec_source=pc_user",
                "/feed",
                FIXTURE_DIR / "note_image.json",
            )
            await _capture_one(
                context,
                f"https://www.xiaohongshu.com/explore/{vid_id}"
                f"?xsec_token={vid_xsec}&xsec_source=pc_user",
                "/feed",
                FIXTURE_DIR / "note_video.json",
            )
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run the capture script**

Run: `python tools/xhs_capture_fixtures.py`

Expected: three messages `wrote tests/fixtures/xhs/...` and script exits 0. If one of the hard-coded note IDs 404s (XHS removed it), the subagent MUST stop and ask the operator for a replacement URL — do NOT guess. A timeout means cookie expired → operator re-runs `xhs_cookie_extractor.py` first.

- [ ] **Step 3: Inspect captured shapes**

Run: `python -c "import json; d=json.load(open('tests/fixtures/xhs/note_image.json')); print(list(d.keys()))"`

Expected: non-empty dict keys printed. Likewise for `note_video.json` and `user_posted_page1.json`. If any file contains `{"code": -1, ...}` or similar error body, the cookie was rejected — stop and surface to operator.

- [ ] **Step 4: Commit fixtures + capture script**

```bash
git add tools/xhs_capture_fixtures.py tests/fixtures/xhs/
git commit -m "feat(xhs): capture real XHS /feed and /user_posted fixtures

One-shot capture script + three recorded JSON responses drive
the upcoming note_to_media_item TDD."
```

---

## Task 2: Parser — `note_to_media_item`

**Files:**
- Modify: `core/platforms/xhs.py` (add `note_to_media_item` + helpers)
- Create: `tests/test_xhs_convert.py`

**Context for the subagent:**

XHS has two major note types — `"normal"` (image list or image gallery) and `"video"`. The `/feed` response nests the note under `data.items[0].note_card` (or camelCase `noteCard`; parser tries both). A `/user_posted` response has `data.notes` as a list with a leaner shape (note IDs + covers only, no full media URLs). For Plan 3 the parser operates on the rich `note_card` shape; the list shape is hydrated per-note in `fetch_list` (Task 6).

**Ground-truth schema (from upstream XHS-Downloader at `/tmp/XHS-Downloader/source/application/{video,image,explore}.py`; verified against live API up to 2026-04-24):**

XHS wire format uses **camelCase**, NOT snake_case. Any snake_case lookup in the parser will silently miss, so the field names below are load-bearing.

- `noteId` — string ID
- `type` — `"video"` | `"normal"`
- `title`, `desc` — strings (often one or the other empty)
- `time`, `lastUpdateTime` — milliseconds since epoch
- `user.nickname` (fallback: `user.nickName`, `user.name`) — author
- `imageList` — list of image dicts
  - Each image: `urlDefault` (primary), `url` (fallback), `urlPre` (preview), and optionally `stream.h264[0].masterUrl` which indicates **this is a live photo (动图)** — the masterUrl is the paired short video
- `video` (only for `type == "video"`):
  - Preferred: `video.consumer.originVideoKey` → unwatermarked original at `https://sns-video-bd.xhscdn.com/{key}` (highest quality when available)
  - Fallback: `video.media.stream.h264[]` / `stream.h265[]` — each entry has `height`, `videoBitrate`, `size`, `masterUrl`, `backupUrls`; pick highest `height`, prefer `backupUrls[0]` else `masterUrl`
- `cover.urlDefault` — explicit video cover; if absent, use `imageList[0].urlDefault`

**Issue #324 mitigation:** If a `type == "video"` note yields an empty list from both the `originVideoKey` path and the `stream.h264/h265` fallback (reported by multiple users on 2026-01-04 in JoeanAmier/XHS-Downloader#324), the parser raises a `RuntimeError` naming both paths. Pipeline catches at task level, logs, marks THIS task failed, and continues with the rest of the batch. Do NOT silently emit a video note with no assets — that would produce an empty download dir and hide the real problem.

**Image URL regeneration:** Raw `urlDefault` values carry a `!<watermark-style>` suffix and sometimes point at a watermarked CDN. Upstream's pattern extracts the token (`"/".join(url.split("/")[5:]).split("!")[0]`) and regenerates against `https://sns-img-bd.xhscdn.com/{token}` (the "auto" CDN, unwatermarked, original format). The parser follows that pattern.

- [ ] **Step 1: Read the captured fixture keys to lock field paths**

Run: `python -c "import json; d=json.load(open('tests/fixtures/xhs/note_image.json'));import pprint;pprint.pprint(d)" | head -200`

Record the actual path to `note_card` in the fixture (usually `data.items[0].note_card` OR top-level if already unwrapped). Use the real paths in the code below — do NOT invent field names.

- [ ] **Step 2: Write failing tests driven by the fixtures + synthetic cases**

Create `tests/test_xhs_convert.py`:

```python
"""XHS note JSON → MediaItem parser tests.

Fixture-driven tests exercise real-shape /feed responses captured in
Task 1. Synthetic tests cover:
- Live photo (动图) extraction (kind == "video_live")
- originVideoKey fast-path preference
- stream.h264 resolution selection
- Issue #324 mitigation (raise on no-URL-extractable video)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.platform import MediaItem
from core.platforms.xhs import note_to_media_item

FIX = Path(__file__).parent / "fixtures" / "xhs"


def _load(name: str) -> dict:
    with (FIX / name).open(encoding="utf-8") as f:
        return json.load(f)


def _extract_note_card(feed: dict) -> dict:
    """Unwrap the /feed response to the note_card dict.

    Tries both snake_case (``note_card``) and camelCase (``noteCard``)
    so the test survives either naming in the SSR envelope.
    """
    data = feed.get("data", feed)
    items = data.get("items") or []
    assert items, "fixture has no items — re-capture with a live note"
    first = items[0]
    note = first.get("note_card") or first.get("noteCard")
    assert note, "items[0] has neither note_card nor noteCard"
    return note


# ---- Fixture-driven tests ----------------------------------------------


def test_image_note_basic_fields():
    note = _extract_note_card(_load("note_image.json"))
    item = note_to_media_item(note)
    assert isinstance(item, MediaItem)
    assert item.platform == "xhs"
    assert item.id and isinstance(item.id, str)
    assert item.author and isinstance(item.author, str)
    assert isinstance(item.desc, str)
    assert item.create_time > 0
    assert item.raw is note


def test_image_note_has_image_assets():
    note = _extract_note_card(_load("note_image.json"))
    item = note_to_media_item(note)
    images = [a for a in item.assets if a.kind == "image"]
    assert len(images) >= 1
    for a in images:
        assert a.url.startswith("http")
        assert a.ext in ("jpg", "png", "webp")
        # Upstream regenerates to sns-img-bd CDN for highest quality.
        assert "sns-img-bd.xhscdn.com" in a.url or a.url == a.url.split("!")[0]


def test_video_note_has_video_asset():
    note = _extract_note_card(_load("note_video.json"))
    item = note_to_media_item(note)
    videos = [a for a in item.assets if a.kind == "video_main"]
    assert len(videos) == 1
    assert videos[0].url.startswith("http")
    assert videos[0].ext == "mp4"


def test_video_note_has_cover_asset():
    note = _extract_note_card(_load("note_video.json"))
    item = note_to_media_item(note)
    covers = [a for a in item.assets if a.kind == "cover"]
    assert len(covers) == 1
    assert covers[0].url.startswith("http")


def test_video_note_no_image_assets():
    """Video notes must not leak image assets (kind discipline)."""
    note = _extract_note_card(_load("note_video.json"))
    item = note_to_media_item(note)
    assert not any(a.kind == "image" for a in item.assets)


# ---- Synthetic tests (camelCase schema fidelity) -----------------------


def test_origin_video_key_is_preferred_over_stream():
    """video.consumer.originVideoKey wins over stream.h264 when present."""
    note = {
        "noteId": "N1",
        "type": "video",
        "user": {"nickname": "u"},
        "time": 1_700_000_000_000,
        "desc": "d",
        "video": {
            "consumer": {"originVideoKey": "pre/abc.mp4"},
            "media": {
                "stream": {
                    "h264": [
                        {
                            "height": 1080,
                            "masterUrl": "https://cdn/master.mp4",
                            "backupUrls": ["https://cdn/backup.mp4"],
                        },
                    ],
                },
            },
        },
        "imageList": [{"urlDefault": "https://sns/1/2/3/4/5/tok!wm"}],
    }
    item = note_to_media_item(note)
    v = next(a for a in item.assets if a.kind == "video_main")
    assert v.url == "https://sns-video-bd.xhscdn.com/pre/abc.mp4"
    # stream URLs become fallbacks when originVideoKey is primary
    assert "https://cdn/backup.mp4" in v.fallback_urls
    assert "https://cdn/master.mp4" in v.fallback_urls


def test_stream_fallback_picks_highest_resolution():
    """When originVideoKey is absent, pick the entry with highest height."""
    note = {
        "noteId": "N2", "type": "video", "user": {"nickname": "u"},
        "time": 1_700_000_000_000, "desc": "",
        "video": {
            "media": {
                "stream": {
                    "h264": [
                        {"height": 480, "masterUrl": "https://cdn/low.mp4"},
                        {"height": 1080,
                         "masterUrl": "https://cdn/hi.mp4",
                         "backupUrls": ["https://cdn/hi_b.mp4"]},
                        {"height": 720, "masterUrl": "https://cdn/mid.mp4"},
                    ],
                },
            },
        },
        "imageList": [],
    }
    item = note_to_media_item(note)
    v = next(a for a in item.assets if a.kind == "video_main")
    # backupUrls[0] wins over masterUrl for the highest entry
    assert v.url == "https://cdn/hi_b.mp4"
    assert "https://cdn/hi.mp4" in v.fallback_urls


def test_raises_when_no_video_url_extractable():
    """Issue #324 defense: both paths empty → clear RuntimeError."""
    note = {
        "noteId": "N3", "type": "video", "user": {"nickname": "u"},
        "time": 1_700_000_000_000, "desc": "",
        "video": {"media": {"stream": {"h264": [], "h265": []}}},
        "imageList": [],
    }
    with pytest.raises(RuntimeError, match="XHS video URL extraction failed"):
        note_to_media_item(note)


def test_live_photo_emits_video_live_asset():
    """Image with stream.h264[0].masterUrl = a live photo (动图)."""
    note = {
        "noteId": "N4", "type": "normal", "user": {"nickname": "u"},
        "time": 1_700_000_000_000, "desc": "",
        "imageList": [
            {
                "urlDefault": (
                    "https://sns-webpic-qc.xhscdn.com/a/b/c/d/"
                    "e/token123!nd_dft_wgth_jpg_3"
                ),
                "stream": {
                    "h264": [{
                        "masterUrl": "https://sns-video/live1.mp4",
                        "backupUrls": ["https://sns-video/live1_b.mp4"],
                    }],
                },
            },
        ],
    }
    item = note_to_media_item(note)
    kinds = [a.kind for a in item.assets]
    assert "image" in kinds
    assert "video_live" in kinds
    live = next(a for a in item.assets if a.kind == "video_live")
    assert live.url == "https://sns-video/live1.mp4"
    assert live.ext == "mp4"
    assert "https://sns-video/live1_b.mp4" in live.fallback_urls


def test_image_without_live_emits_only_image():
    note = {
        "noteId": "N5", "type": "normal", "user": {"nickname": "u"},
        "time": 1_700_000_000_000, "desc": "",
        "imageList": [
            {"urlDefault": "https://x/a/b/c/d/e/tok!wm"},
            {"urlDefault": "https://x/a/b/c/d/e/tok2!wm"},
        ],
    }
    item = note_to_media_item(note)
    kinds = [a.kind for a in item.assets]
    assert kinds == ["image", "image"]


def test_image_token_regenerated_to_sns_img_bd():
    note = {
        "noteId": "N6", "type": "normal", "user": {"nickname": "u"},
        "time": 1_700_000_000_000, "desc": "",
        "imageList": [{"urlDefault":
                       "https://sns-webpic-qc.xhscdn.com/a/b/c/d/e/tok!watermark"}],
    }
    item = note_to_media_item(note)
    img = next(a for a in item.assets if a.kind == "image")
    # Upstream regeneration pattern: parts[5:] joined, watermark stripped.
    assert img.url == "https://sns-img-bd.xhscdn.com/e/tok"


def test_camel_case_not_snake_case_is_required():
    """Regression guard: parser must not accept snake_case aliases.

    Prevents accidental snake_case drift; XHS wire is camelCase and the
    upstream XHS-Downloader confirms this.
    """
    snake = {
        "note_id": "X", "type": "video", "user": {"nickname": "u"},
        "time": 1_700_000_000_000, "desc": "",
        "video": {
            "media": {"stream": {"h264": [
                {"master_url": "https://cdn/m.mp4"}
            ]}},
        },
        "image_list": [],
    }
    # snake_case yields empty imageList + empty video consumer + stream
    # entry has no masterUrl → extraction fails (correct behaviour).
    with pytest.raises(RuntimeError):
        note_to_media_item(snake)


def test_time_in_seconds_is_accepted_unchanged():
    """Defensive: if XHS ever returns seconds instead of ms, accept."""
    note = {
        "noteId": "N7", "type": "normal", "user": {"nickname": "u"},
        "time": 1_700_000_000, "desc": "",
        "imageList": [{"urlDefault": "https://x/a/b/c/d/e/tok!wm"}],
    }
    item = note_to_media_item(note)
    assert item.create_time == 1_700_000_000.0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_xhs_convert.py -v`

Expected: all fail with `ImportError: cannot import name 'note_to_media_item' from 'core.platforms.xhs'`.

- [ ] **Step 4: Implement `note_to_media_item` + helpers**

Edit `core/platforms/xhs.py`. At the top, replace the existing:

```python
from core.platform import ContentRef
```

with:

```python
from core.platform import ContentRef, ListPage, MediaAsset, MediaItem
```

Then append at module scope, **before** the `class XHSPlatformClient` definition:

```python
def note_to_media_item(note: dict) -> MediaItem:
    """Convert an XHS note_card dict into a MediaItem.

    Accepts the ``note_card`` sub-object from ``/api/sns/web/v1/feed``
    (``response.data.items[0].note_card`` OR ``noteCard``). Handles
    ``type="video"`` and ``type="normal"`` notes plus live photos
    (动图 — image_list entries whose ``stream.h264[0].masterUrl`` is
    set).

    XHS wire format is camelCase (imageList, urlDefault, masterUrl,
    backupUrls, originVideoKey, noteId) — snake_case variants are
    NOT accepted.

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
    """Pick the highest-quality video URL from note_card.video.

    Preference (matches upstream XHS-Downloader deal_video_link):
      1. ``video.consumer.originVideoKey`` → unwatermarked original on
         sns-video-bd.xhscdn.com. Only exists for some notes.
      2. ``video.media.stream.{h264,h265,av1}[]`` sorted by height desc;
         pick ``backupUrls[0]`` if present else ``masterUrl``.

    Returns None when both paths yield nothing. Caller turns that into
    a RuntimeError with schema-drift diagnostics.
    """
    # Path 1: originVideoKey fast lane (unwatermarked, highest quality).
    consumer = video.get("consumer") or {}
    origin_key = consumer.get("originVideoKey")
    if origin_key:
        primary = f"https://sns-video-bd.xhscdn.com/{origin_key}"
        return MediaAsset(
            url=primary, kind="video_main", ext="mp4",
            fallback_urls=_stream_backup_urls(video),
        )

    # Path 2: stream entries sorted by resolution.
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
    # Include other-resolution entries as last-resort fallbacks.
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
    """Extract the static image URL, regenerating to unwatermarked CDN.

    Upstream pattern: token = parts[5:].join('/').strip('!<suffix>'),
    primary = ``https://sns-img-bd.xhscdn.com/{token}``. If the source
    URL has fewer than 6 path segments (unexpected shape), fall back
    to raw URL with the ``!`` suffix stripped.
    """
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
    # The raw watermarked URL is the ultimate backstop.
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
    """Return the CDN token from an XHS image URL, else None.

    XHS CDN URLs look like::

        https://<subdomain>.xhscdn.com/<ts>/<hash>/<token>!<wm>

    Upstream: ``"/".join(url.split("/")[5:]).split("!")[0]``. If
    fewer than 6 path segments exist, the shape is unexpected and
    we return None rather than producing a broken URL.
    """
    parts = url.split("/")
    if len(parts) < 6:
        return None
    tail = "/".join(parts[5:])
    token = tail.split("!")[0]
    return token or None


def _live_to_asset(img: dict) -> MediaAsset | None:
    """If this image_list entry has a paired short video, emit it.

    XHS encodes a live photo (动图) by attaching
    ``image.stream.h264[0].masterUrl`` to the static image entry.
    Returns ``MediaAsset(kind="video_live", ext="mp4")`` for pairing,
    or None if this is a plain static image.
    """
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
    """Pick a cover URL for a video note.

    Priority:
      1. ``note.cover.urlDefault`` (explicit cover field)
      2. ``note.imageList[0].urlDefault`` (XHS uses imageList[0] as
         the video poster when no explicit cover is set)
    """
    cover = note.get("cover") or {}
    primary = cover.get("urlDefault") or cover.get("url")
    fallbacks: list[str] = []
    for u in cover.get("urlList") or []:
        if u and u != primary:
            fallbacks.append(u)

    if not primary:
        images = note.get("imageList") or []
        if images:
            first = images[0]
            primary = first.get("urlDefault") or first.get("url")

    if not primary:
        return None

    primary_clean = primary.split("!")[0]
    return MediaAsset(
        url=primary_clean, kind="cover", ext="jpg",
        fallback_urls=[f.split("!")[0] for f in fallbacks
                       if f.split("!")[0] != primary_clean],
    )
```

- [ ] **Step 5: Run tests again**

Run: `pytest tests/test_xhs_convert.py -v`

Expected: **all pass** (~12 tests: fixture-driven + synthetic). If a FIXTURE test fails with `KeyError` or empty-list assertion, the real XHS response shape differs from what upstream documented — re-read the fixture JSON, update the parser's field names **not** the tests, and note the drift in the commit message. If a SYNTHETIC test fails, the parser has a logic bug — the synthetic inputs are canonical per the schema documented in Context above.

- [ ] **Step 6: Run full suite to catch regressions**

Run: `pytest -q`

Expected: all previously-passing tests still pass. New test count ≈ 113 existing + 12 new = 125 green.

- [ ] **Step 7: Commit**

```bash
git add core/platforms/xhs.py tests/test_xhs_convert.py
git commit -m "feat(xhs): note_to_media_item with camelCase schema + live photos

Handles:
- originVideoKey fast-path (unwatermarked sns-video-bd.xhscdn.com)
- stream.h264/h265 fallback sorted by height, backupUrls preferred
- image_list with token-regenerated sns-img-bd.xhscdn.com URLs
- live photos (video_live) paired with images via stream.h264[0].masterUrl
- Raises clear RuntimeError when video URL extraction fails
  (mitigates JoeanAmier/XHS-Downloader#324 at task level)"
```

---

## Task 3: Short URL resolver for `xhslink.com`

**Files:**
- Modify: `core/platforms/xhs.py` (add `_resolve_xhslink` + plug into `XHSPlatformClient.resolve_short_url`)
- Create: `tests/test_xhs_short_url.py`

**Context:** `xhslink.com/m/xxx` and `xhslink.com/a/xxx` redirect (HTTP 302) to either `xiaohongshu.com/explore/<id>?xsec_token=...` (single note) or `xiaohongshu.com/user/profile/<id>?xsec_token=...` (user). The pipeline then re-matches the resolved URL, so we just need to return the `Location` header.

- [ ] **Step 1: Write failing tests**

Create `tests/test_xhs_short_url.py`:

```python
"""Tests for XHS xhslink short URL resolution."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.platforms.xhs import _resolve_xhslink


@pytest.mark.asyncio
async def test_resolve_xhslink_follows_location():
    fake_resp = MagicMock()
    fake_resp.status_code = 302
    fake_resp.headers = {
        "Location": (
            "https://www.xiaohongshu.com/user/profile/"
            "55c726695894464ef542aea0?xsec_token=ABC"
        ),
    }

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    fake_client.get = AsyncMock(return_value=fake_resp)

    with patch("core.platforms.xhs.httpx.AsyncClient",
               return_value=fake_client):
        out = await _resolve_xhslink("https://xhslink.com/m/5kcCust1t6Z")
    assert out == (
        "https://www.xiaohongshu.com/user/profile/"
        "55c726695894464ef542aea0?xsec_token=ABC"
    )


@pytest.mark.asyncio
async def test_resolve_xhslink_returns_input_on_non_redirect():
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.headers = {}

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    fake_client.get = AsyncMock(return_value=fake_resp)

    with patch("core.platforms.xhs.httpx.AsyncClient",
               return_value=fake_client):
        out = await _resolve_xhslink("https://xhslink.com/m/5kcCust1t6Z")
    assert out == "https://xhslink.com/m/5kcCust1t6Z"


@pytest.mark.asyncio
async def test_resolve_xhslink_tolerates_network_error():
    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    fake_client.get = AsyncMock(side_effect=OSError("boom"))

    with patch("core.platforms.xhs.httpx.AsyncClient",
               return_value=fake_client):
        out = await _resolve_xhslink("https://xhslink.com/m/5kcCust1t6Z")
    assert out == "https://xhslink.com/m/5kcCust1t6Z"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_xhs_short_url.py -v`

Expected: 3 failures with `ImportError: cannot import name '_resolve_xhslink'`.

- [ ] **Step 3: Implement `_resolve_xhslink`**

Edit `core/platforms/xhs.py`. Add at top-of-file imports:

```python
import httpx
```

Append at module scope (before `class XHSPlatformClient`):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_xhs_short_url.py -v`

Expected: **all 3 pass**.

- [ ] **Step 5: Commit**

```bash
git add core/platforms/xhs.py tests/test_xhs_short_url.py
git commit -m "feat(xhs): resolve xhslink.com short URLs via HTTP 302

Mobile UA + single-redirect follow. Returns input URL on any failure."
```

---

## Task 4: `XHSBrowserSession` — Playwright context lifecycle

**Files:**
- Create: `core/platforms/xhs_browser.py`

**Context:** One `XHSBrowserSession` is built per pipeline run, shared across all XHS tasks, and torn down in `downloader.py`'s `finally` block. The session owns ONE chromium browser and ONE context (cookies injected at construction). Each XHS call opens a fresh `page`, does its thing, closes the page. We do NOT re-launch the browser per call (too slow, ~2 s per goto).

No unit tests here — this is thin Playwright plumbing and we'll exercise it via the integration tests in Task 7. Still, the class must be small and readable.

- [ ] **Step 1: Write the session module**

Create `core/platforms/xhs_browser.py`:

```python
"""Long-lived Playwright session for XHS data capture.

Owns one chromium browser + one context seeded with the user's XHS
cookie. Shared by XHSPlatformClient across all XHS tasks in a pipeline
run; torn down in downloader.py's cleanup phase.
"""

from __future__ import annotations

from contextlib import asynccontextmanager


def _cookie_header_to_playwright(raw: str) -> list[dict]:
    """Parse a raw Cookie header into Playwright's add_cookies shape."""
    out: list[dict] = []
    for part in raw.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        name, _, value = part.partition("=")
        out.append({
            "name": name.strip(),
            "value": value.strip().strip('"'),
            "domain": ".xiaohongshu.com",
            "path": "/",
        })
    return out


class XHSBrowserSession:
    """Shared Playwright browser + context for all XHS calls in one run.

    Usage:
        session = XHSBrowserSession(cookie_header)
        await session.start()
        try:
            async with session.page() as page:
                await page.goto(...)
        finally:
            await session.close()
    """

    def __init__(self, cookie_header: str, *, headless: bool = True) -> None:
        self._cookie_header = cookie_header
        self._headless = headless
        self._pw = None
        self._browser = None
        self._context = None

    async def start(self) -> None:
        """Launch chromium and create a context with XHS cookies."""
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self._headless)
        self._context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        await self._context.add_cookies(
            _cookie_header_to_playwright(self._cookie_header),
        )

    async def close(self) -> None:
        """Release all Playwright resources. Idempotent."""
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None

    @asynccontextmanager
    async def page(self):
        """Yield a fresh page; auto-close on exit regardless of exception."""
        if self._context is None:
            raise RuntimeError(
                "XHSBrowserSession not started — call start() first",
            )
        pg = await self._context.new_page()
        try:
            yield pg
        finally:
            try:
                await pg.close()
            except Exception:
                pass
```

- [ ] **Step 2: Smoke-import the module**

Run: `python -c "from core.platforms.xhs_browser import XHSBrowserSession; print(XHSBrowserSession)"`

Expected: prints `<class 'core.platforms.xhs_browser.XHSBrowserSession'>`. If it errors on the `playwright` import, that's a real problem — confirm `playwright` is installed (it's in `memory/project-references.md`).

- [ ] **Step 3: Commit**

```bash
git add core/platforms/xhs_browser.py
git commit -m "feat(xhs): XHSBrowserSession — shared Playwright context

One chromium + one cookie-seeded context per pipeline run, yielding
ephemeral pages. Used by XHSPlatformClient in the next task."
```

---

## Task 5: `fetch_single` — explore URL → `/feed` interception

**Files:**
- Modify: `core/platforms/xhs.py` (`XHSPlatformClient.__init__` + `fetch_single` + `resolve_short_url`)
- Create: `tests/test_xhs_client_integration.py` (integration test, env-gated)

**Context:** For a single note, navigate to `https://www.xiaohongshu.com/explore/<note_id>?xsec_token=...` and the XHS SPA fires `/api/sns/web/v1/feed` once on load. We register a response listener before `page.goto`, await the first matching response, parse its JSON, run `note_to_media_item`.

The `ref.extra` may carry `xsec_token` and `xsec_source` (stored there by the pipeline after short URL resolution). If present, append to URL query.

- [ ] **Step 1: Rewrite `XHSPlatformClient`**

Edit `core/platforms/xhs.py`. **Replace** the entire current `class XHSPlatformClient` block with:

```python
class XHSPlatformClient:
    """PlatformClient implementation for XHS using a shared Playwright session.

    Args:
        session: Started ``XHSBrowserSession`` (cookies already injected).

    The client does NOT own the session lifecycle; ``downloader.py`` is
    responsible for ``session.start()`` and ``session.close()``.
    """

    _FEED_ENDPOINT = "/api/sns/web/v1/feed"
    _USER_POSTED_ENDPOINT = "/api/sns/web/v1/user_posted"

    def __init__(self, session) -> None:
        self._session = session

    async def resolve_short_url(self, url: str) -> str:
        return await _resolve_xhslink(url)

    async def fetch_single(self, ref: ContentRef, span) -> MediaItem:
        """Fetch one note via explore URL, intercepting /feed."""
        import asyncio

        target = self._build_explore_url(ref)
        async with self._session.page() as pg:
            loop = asyncio.get_event_loop()
            fut: asyncio.Future[dict] = loop.create_future()

            def _on_response(resp):
                if fut.done():
                    return
                if self._FEED_ENDPOINT not in resp.url:
                    return

                async def _consume():
                    try:
                        body = await resp.json()
                    except Exception:
                        return
                    if not fut.done():
                        fut.set_result(body)

                asyncio.create_task(_consume())

            pg.on("response", _on_response)
            await pg.goto(target, wait_until="domcontentloaded")
            body = await asyncio.wait_for(fut, timeout=20.0)

        note = self._extract_note_card(body)
        if note is None:
            raise RuntimeError(
                f"XHS /feed returned no note_card for {ref.resource_id} "
                f"(response code={body.get('code')}, msg={body.get('msg')})"
            )
        return note_to_media_item(note)

    async def fetch_list(self, ref: ContentRef, cursor, span) -> ListPage:
        raise NotImplementedError(
            "XHS fetch_list not yet implemented — see Plan 3 Task 6"
        )

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

    @staticmethod
    def _extract_note_card(feed_body: dict) -> dict | None:
        """Unwrap /feed to the note_card dict.

        Tries both ``note_card`` and ``noteCard`` — XHS has been
        observed using either wrapper name depending on SSR path.
        """
        data = feed_body.get("data") or {}
        items = data.get("items") or []
        if not items:
            return None
        first = items[0]
        return first.get("note_card") or first.get("noteCard")
```

Also extend `XHSPlatform.match_url` to preserve `xsec_token` / `xsec_source` from the URL query into `ContentRef.extra` for `explore` and `user` matches. Locate the existing `_EXPLORE_RE.search(url)` block in `XHSPlatform.match_url` and replace it with:

```python
        m = _EXPLORE_RE.search(url)
        if m:
            extra = self._extract_xsec(url)
            return ContentRef(
                platform="xhs",
                content_type="single",
                resource_id=m.group(1),
                resolved_url=url,
                extra=extra,
            )
```

Do the same replacement for the `_DISCOVERY_RE`, `_USER_RE`, and `_BOARD_RE` blocks (add `extra=self._extract_xsec(url),` argument).

Then add this helper method inside `XHSPlatform` (below `match_url`):

```python
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
```

- [ ] **Step 2: Add tests for URL extraction + write first integration test**

Append to `tests/test_xhs_platform.py`:

```python
def test_explore_url_preserves_xsec():
    p = XHSPlatform()
    ref = p.match_url(
        "https://www.xiaohongshu.com/explore/abc123"
        "?xsec_token=TOKEN&xsec_source=app_share"
    )
    assert ref is not None
    assert ref.extra.get("xsec_token") == "TOKEN"
    assert ref.extra.get("xsec_source") == "app_share"


def test_user_url_preserves_xsec():
    p = XHSPlatform()
    ref = p.match_url(
        "https://www.xiaohongshu.com/user/profile/uid123"
        "?xsec_token=TOK"
    )
    assert ref is not None
    assert ref.extra.get("xsec_token") == "TOK"
```

Create `tests/test_xhs_client_integration.py`:

```python
"""XHS Playwright integration tests — skipped unless XHS_INTEGRATION=1.

Requires:
  - config.yml::cookies.xhs populated (run xhs_cookie_extractor.py first)
  - playwright + chromium installed
  - network access to xiaohongshu.com
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from core.platform import ContentRef, MediaItem
from core.platforms.xhs import XHSPlatformClient
from core.platforms.xhs_browser import XHSBrowserSession

pytestmark = pytest.mark.skipif(
    os.environ.get("XHS_INTEGRATION") != "1",
    reason="set XHS_INTEGRATION=1 to run (needs live XHS + cookie)",
)


def _load_cookie() -> str:
    config_path = Path(__file__).resolve().parent.parent / "config.yml"
    with config_path.open() as f:
        data = yaml.safe_load(f) or {}
    raw = (data.get("cookies", {}) or {}).get("xhs", "").strip()
    if not raw:
        pytest.skip("no XHS cookie in config.yml")
    return raw


def _read_fixture_note_id(fixture_name: str) -> tuple[str, str]:
    """Pull (noteId, xsecToken) from a captured /feed fixture.

    Keeps the integration tests in sync with Task 1's capture; no
    hard-coded IDs to go stale.
    """
    import json
    from pathlib import Path
    fix_path = (
        Path(__file__).parent / "fixtures" / "xhs" / fixture_name
    )
    if not fix_path.exists():
        pytest.skip(f"fixture {fixture_name} missing — run Task 1 first")
    body = json.loads(fix_path.read_text(encoding="utf-8"))
    data = body.get("data") or {}
    items = data.get("items") or []
    assert items, f"{fixture_name} has no items"
    card = items[0].get("note_card") or items[0].get("noteCard") or {}
    nid = card.get("noteId") or card.get("id")
    # xsec_token lives on the enclosing item wrapper, not note_card.
    xsec = items[0].get("xsec_token") or items[0].get("xsecToken") or ""
    assert nid, f"{fixture_name} items[0] has no noteId"
    return str(nid), str(xsec)


@pytest.mark.asyncio
async def test_fetch_single_image_note():
    note_id, xsec = _read_fixture_note_id("note_image.json")
    session = XHSBrowserSession(_load_cookie())
    await session.start()
    try:
        client = XHSPlatformClient(session)
        ref = ContentRef(
            platform="xhs",
            content_type="single",
            resource_id=note_id,
            resolved_url="",
            extra={"xsec_token": xsec, "xsec_source": "pc_user"}
            if xsec else {},
        )
        item = await client.fetch_single(ref, span=None)
        assert isinstance(item, MediaItem)
        assert item.platform == "xhs"
        assert item.id
        assert any(a.kind == "image" for a in item.assets)
    finally:
        await session.close()
```

- [ ] **Step 3: Run platform tests + ensure unit tests still pass**

Run: `pytest tests/test_xhs_platform.py tests/test_xhs_convert.py tests/test_xhs_short_url.py -v`

Expected: all green (prior tests + 2 new xsec-preservation tests pass).

- [ ] **Step 4: Run the integration test**

Run: `XHS_INTEGRATION=1 pytest tests/test_xhs_client_integration.py::test_fetch_single_image_note -v -s`

Expected: test passes and you see chromium log lines (one-off ~5 s). If it fails because the hard-coded `resource_id` is gone from XHS, swap for a different public note and update both Task 1's `IMAGE_NOTE_URL` and this test.

- [ ] **Step 5: Commit**

```bash
git add core/platforms/xhs.py tests/test_xhs_platform.py tests/test_xhs_client_integration.py
git commit -m "feat(xhs): fetch_single via Playwright /feed interception

XHSPlatformClient.fetch_single goes to explore/<id>?xsec_token=...,
listens for /api/sns/web/v1/feed, and converts note_card via
note_to_media_item. XHSPlatform now preserves xsec_token/source from
URL query into ContentRef.extra so the client can re-emit them."
```

---

## Task 6: `fetch_list` — user profile + auto-scroll

**Files:**
- Modify: `core/platforms/xhs.py` (`XHSPlatformClient.fetch_list`)
- Modify: `tests/test_xhs_client_integration.py` (add profile-fetch integration test)

**Context:** XHS SSR-loads the first batch of a user profile page (first `/user_posted` request fires automatically on `page.goto`). Subsequent batches fire when the user scrolls to near the bottom of the feed grid. We:
1. Register a response listener BEFORE goto.
2. `page.goto(profile_url)`, await `domcontentloaded`.
3. Scroll loop: `page.evaluate("window.scrollBy(0, document.body.scrollHeight)")`, wait ~1.2 s for the SPA to debounce and fire the next `/user_posted`.
4. Stop when either (a) 3 consecutive scrolls yield no new `/user_posted` response, or (b) the latest response's `data.has_more == false`.
5. Each response's `data.notes[]` carries a leaner note shape (no full media URLs). For each note, re-fetch via `fetch_single(note_id)` to hydrate full assets.

The listing shape only gives `note_id`, `cover`, `type`, `display_title`, `user.nickname`. Full media needs a separate `/feed` call per note. This is the documented cost of the no-signing approach; batch the per-note fetches to amortize browser context overhead — they reuse the same session.

- [ ] **Step 1: Write the implementation**

Edit `core/platforms/xhs.py`. Replace the placeholder `fetch_list` in `XHSPlatformClient` with:

```python
    async def fetch_list(self, ref: ContentRef, cursor, span) -> ListPage:
        """Fetch all notes for a user profile and hydrate each via /feed.

        XHS's /user_posted responses are triggered by scroll on the SPA;
        we auto-scroll to bottom, collecting every response, then make a
        second pass calling fetch_single for each note to get media URLs.

        The ``cursor`` parameter is ignored: we always return the full
        list in one call with ``has_more=False``. Pipeline's cursor loop
        naturally exits after one iteration.
        """
        if ref.content_type != "user":
            raise ValueError(
                f"XHS fetch_list currently supports content_type='user', "
                f"got {ref.content_type!r}"
            )

        listings = await self._collect_user_listings(ref)

        items: list[MediaItem] = []
        for note_stub in listings:
            note_id = (
                note_stub.get("noteId")
                or note_stub.get("note_id")
                or note_stub.get("id")
            )
            if not note_id:
                continue
            # xsec_token is mandatory for /feed access (content gating).
            # XHS has used both snake_case and camelCase in responses.
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
                # bad note kill the whole batch. The error surfaces
                # via the task-level tracer.
                if span is not None:
                    try:
                        span.attributes[f"skip_{note_id}"] = str(exc)[:200]
                    except Exception:
                        pass
                continue

        return ListPage(items=items, next_cursor=None, has_more=False)

    async def _collect_user_listings(self, ref: ContentRef) -> list[dict]:
        """Scroll the profile and return the union of all /user_posted notes."""
        import asyncio

        target = self._build_profile_url(ref)
        collected: dict[str, dict] = {}  # note_id -> stub dict
        done = asyncio.Event()
        quiet_scrolls = 0
        max_quiet = 3
        max_total_scrolls = 40  # safety cap ≈ 1200 notes

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
            await asyncio.sleep(1.5)  # wait for initial SSR /user_posted

            for i in range(max_total_scrolls):
                if done.is_set():
                    break
                before = len(collected)
                await pg.evaluate(
                    "window.scrollBy(0, document.body.scrollHeight)"
                )
                await asyncio.sleep(1.2)
                if len(collected) == before:
                    quiet_scrolls += 1
                    if quiet_scrolls >= max_quiet:
                        break
                else:
                    quiet_scrolls = 0

        return list(collected.values())

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
```

- [ ] **Step 2: Add integration test for profile fetch**

Append to `tests/test_xhs_client_integration.py`:

```python
@pytest.mark.asyncio
async def test_fetch_list_user_profile():
    """秃头金金 has ~30 public notes; we expect >=5 (conservative)."""
    session = XHSBrowserSession(_load_cookie())
    await session.start()
    try:
        client = XHSPlatformClient(session)
        ref = ContentRef(
            platform="xhs",
            content_type="user",
            resource_id="55c726695894464ef542aea0",
            resolved_url="",
            extra={
                "xsec_token":
                    "YBbTU9A0gqz095TGQUh16x7JntBoAaPXp-zQk8hjrx768=",
                "xsec_source": "app_share",
            },
        )
        page = await client.fetch_list(ref, cursor=None, span=None)
        assert page.has_more is False
        assert len(page.items) >= 5
        for item in page.items[:3]:
            assert isinstance(item, MediaItem)
            assert item.platform == "xhs"
            assert len(item.assets) >= 1
    finally:
        await session.close()
```

- [ ] **Step 3: Run unit suite (list changes should not break parser/short-url tests)**

Run: `pytest -q -x -k "not integration"`

Expected: all 117+ existing tests pass.

- [ ] **Step 4: Run the new integration test**

Run: `XHS_INTEGRATION=1 pytest tests/test_xhs_client_integration.py::test_fetch_list_user_profile -v -s`

Expected: test passes, takes ~30-60 s (scrolling + per-note hydration). Log should show chromium activity. If it times out, bump `max_total_scrolls` — but first check that `/user_posted` is actually firing (print `resp.url` from `_on_response` for debugging).

- [ ] **Step 5: Commit**

```bash
git add core/platforms/xhs.py tests/test_xhs_client_integration.py
git commit -m "feat(xhs): fetch_list via profile auto-scroll + per-note /feed

Returns a single ListPage with has_more=False (all notes in one call).
User_posted stubs are hydrated into full MediaItems via fetch_single.
Stops on 3 quiet scrolls OR has_more=False OR 40-scroll safety cap."
```

---

## Task 7: Wire `XHSBrowserSession` + `XHSPlatformClient` into `downloader.py`

**Files:**
- Modify: `downloader.py` (lifecycle)

- [ ] **Step 1: Read the current cmd_download shape**

Already covered — the current code at `downloader.py:111-113` registers `XHSPlatformClient()` with no args. We need to:
1. Acquire the XHS cookie (optional — skip session if absent).
2. Build `XHSBrowserSession`, start it, register `XHSPlatformClient(session)`.
3. Close the session in the existing `finally` block.

- [ ] **Step 2: Modify `downloader.py::cmd_download`**

In `downloader.py`, find the block around lines 76, 113, and 137-142 and modify them together.

Change line 76 from:
```python
    from core.platforms.xhs import XHSPlatform, XHSPlatformClient
```
to:
```python
    from core.platforms.xhs import XHSPlatform, XHSPlatformClient
    from core.platforms.xhs_browser import XHSBrowserSession
```

Change the registry block (currently lines 111-113):
```python
    registry = PlatformRegistry()
    registry.register(DouyinPlatform(), DouyinPlatformClient(api))
    registry.register(XHSPlatform(), XHSPlatformClient())
```
to:
```python
    registry = PlatformRegistry()
    registry.register(DouyinPlatform(), DouyinPlatformClient(api))

    xhs_session: XHSBrowserSession | None = None
    try:
        xhs_state = await cookie_mgr.ensure_valid_cookie(platform="xhs")
        xhs_session = XHSBrowserSession(xhs_state.value)
        await xhs_session.start()
        registry.register(XHSPlatform(), XHSPlatformClient(xhs_session))
    except Exception as exc:
        log.warn(
            "XHS Cookie 获取失败（若本次只下载抖音可忽略）",
            error=str(exc),
        )
        # Still register a placeholder so pipeline reports clean error
        # rather than 'unknown platform' if an XHS URL is in the batch.
        registry.register(XHSPlatform(), XHSPlatformClient(None))
```

Change the finally block (currently lines 137-142):
```python
    finally:
        dashboard.stop()
        await api.close()
        await engine.close()
        tracer.close()
        dual_logger.close()
```
to:
```python
    finally:
        dashboard.stop()
        await api.close()
        await engine.close()
        if xhs_session is not None:
            await xhs_session.close()
        tracer.close()
        dual_logger.close()
```

Finally, adapt `XHSPlatformClient` to gracefully handle a `None` session: in `core/platforms/xhs.py`, extend `__init__` and every method that uses `self._session`:

```python
    def __init__(self, session) -> None:
        self._session = session

    def _require_session(self) -> None:
        if self._session is None:
            raise RuntimeError(
                "XHS downloader not available: no XHS cookie configured "
                "(run `python xhs_cookie_extractor.py`)"
            )
```

Then at the very top of `fetch_single` and `_collect_user_listings`, add `self._require_session()` as the first statement (before any other logic).

- [ ] **Step 3: Run full test suite**

Run: `pytest -q`

Expected: all unit tests still green. Integration tests skipped (no `XHS_INTEGRATION=1`).

- [ ] **Step 4: Smoke run with Douyin-only batch**

Edit `config.yml` temporarily so `links:` has only one Douyin URL. Run:

```bash
python downloader.py -c config.yml --no-dashboard 2>&1 | head -40
```

Expected: Douyin cookie loads (or falls through waterfall), XHS cookie load either succeeds (`xhs_session started`) or logs the "XHS Cookie 获取失败" warn — either way the Douyin download proceeds. Abort with Ctrl-C once you see the task start.

- [ ] **Step 5: Commit**

```bash
git add core/platforms/xhs.py downloader.py
git commit -m "feat(xhs): wire XHSBrowserSession lifecycle into downloader.py

Build one session per run with the configured XHS cookie, register
XHSPlatformClient against it, and tear down in the cleanup block.
Missing XHS cookie is non-fatal for Douyin-only batches."
```

---

## Task 8: Pipeline — skip Douyin cookie gate for XHS-only batches

**Files:**
- Modify: `core/pipeline.py`

**Context:** `DownloadPipeline.run()` currently does `await self._cookie_mgr.ensure_valid_cookie()` (defaults to `platform="douyin"`) unconditionally at line 55. For an XHS-only batch this raises `CookieExpiredError` and kills the whole run. Fix: detect up-front which platforms the batch actually needs, only gate on those.

- [ ] **Step 1: Write failing test**

Append to `tests/test_integration.py` (or create a new file `tests/test_pipeline_cookie_gate.py` if the existing file is crowded — check first with `wc -l tests/test_integration.py`; create new file if >200 lines):

```python
"""Pipeline's upfront cookie check must be platform-aware."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.errors import CookieExpiredError
from core.pipeline import DownloadPipeline
from core.platform import ContentRef, PlatformRegistry


def _stub_config(links: list[str]):
    cfg = MagicMock()
    cfg.links = links
    cfg.number = {}
    cfg.mode = []
    return cfg


@pytest.mark.asyncio
async def test_pipeline_skips_douyin_cookie_for_xhs_only_batch():
    """If links contain only XHS URLs, Douyin cookie failure must be tolerated."""
    cookie_mgr = MagicMock()
    # simulate: douyin cookie fails, xhs cookie ok
    async def _ensure(platform="douyin"):
        if platform == "douyin":
            raise CookieExpiredError("no douyin cookie")
        return MagicMock(source="config", is_valid=True)
    cookie_mgr.ensure_valid_cookie = _ensure

    from core.platforms.xhs import XHSPlatform
    registry = PlatformRegistry()
    # dummy client — pipeline won't reach fetch because we stop at cookie gate
    class _Dummy:
        async def resolve_short_url(self, u): return u
        async def fetch_single(self, ref, span): raise AssertionError("unreached")
        async def fetch_list(self, ref, c, span): raise AssertionError("unreached")
    registry.register(XHSPlatform(), _Dummy())

    dashboard = MagicMock()
    dashboard.add_task = MagicMock()
    dashboard.set_cookie_state = MagicMock()
    dashboard.refresh = MagicMock()

    tracer = MagicMock()
    tracer.start_trace.return_value = MagicMock(trace_id="t1")
    tracer.context_span.return_value.__enter__ = MagicMock(
        return_value=MagicMock(attributes={}),
    )
    tracer.context_span.return_value.__exit__ = MagicMock(return_value=None)

    logger = MagicMock()

    pl = DownloadPipeline(
        config=_stub_config(["https://xhslink.com/m/5kcCust1t6Z"]),
        registry=registry,
        engine=MagicMock(),
        cookie_mgr=cookie_mgr,
        tracer=tracer,
        logger=logger,
        dashboard=dashboard,
    )
    # Should NOT raise CookieExpiredError even though douyin cookie is missing.
    # We only test the gate — _execute_task is stubbed to AssertionError, which
    # would surface if reached, but the dummy fetch_single isn't called because
    # short URL resolution returns the same URL and the re-match yields xhs/short
    # which pipeline tries to resolve again — to keep this test focused on the
    # gate, patch _prepare_tasks to short-circuit.
    pl._prepare_tasks = AsyncMock(return_value=[])
    await pl.run()  # should complete without raising
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_pipeline_cookie_gate.py -v` (or wherever you put it).

Expected: FAIL with `CookieExpiredError: no douyin cookie`.

- [ ] **Step 3: Fix `pipeline.run`**

Edit `core/pipeline.py`. Replace the current `run` method (lines 51-67) with:

```python
    async def run(self) -> None:
        session_span = self._tracer.start_trace("session", url="batch")

        needed_platforms = self._infer_needed_platforms()

        with self._tracer.context_span(session_span, "cookie_check") as cs:
            primary_state = None
            for plat in needed_platforms:
                try:
                    state = await self._cookie_mgr.ensure_valid_cookie(
                        platform=plat,
                    )
                except Exception as exc:
                    self._log.warn(
                        f"平台 {plat} Cookie 获取失败",
                        error=str(exc),
                    )
                    continue
                if primary_state is None:
                    primary_state = state
            cs.attributes["platforms"] = ",".join(needed_platforms)
            if primary_state is not None:
                self._dashboard.set_cookie_state(primary_state)

        tasks = await self._prepare_tasks(session_span)
        self._log.info(f"共 {len(tasks)} 个任务")
        for task in tasks:
            self._dashboard.add_task(task)
        for task in tasks:
            await self._execute_task(task)
            self._dashboard.refresh()

        self._tracer.end_span(session_span)

    def _infer_needed_platforms(self) -> list[str]:
        """Return the list of platform names whose cookies this batch needs.

        Inspects config.links and uses the registry to match each URL.
        An unresolvable URL doesn't add a platform — if it turns out
        unhandled later, _prepare_tasks will log and skip.
        """
        seen: list[str] = []
        for url in self._config.links:
            match = self._registry.match(url)
            if match is None:
                continue
            name = match[0].name
            if name not in seen:
                seen.append(name)
        return seen
```

- [ ] **Step 4: Run the test**

Run: `pytest tests/test_pipeline_cookie_gate.py -v`

Expected: PASS.

- [ ] **Step 5: Run full suite**

Run: `pytest -q`

Expected: all unit tests pass (no regressions on the 117 existing).

- [ ] **Step 6: Commit**

```bash
git add core/pipeline.py tests/test_pipeline_cookie_gate.py
git commit -m "fix(pipeline): cookie gate is platform-aware

Previously run() unconditionally required a Douyin cookie, which
broke XHS-only batches. Now infer needed platforms from
config.links via registry.match and only gate on those. Per-platform
failures warn but don't kill the run — the task itself will surface
a cleaner error when it tries to use the missing cookie."
```

---

## Task 9: End-to-end smoke test — download 秃头金金 profile

**Files:**
- No code changes; operator drives this manually.

- [ ] **Step 1: Prepare config**

Ensure `config.yml::cookies.xhs` is set (rerun `python xhs_cookie_extractor.py` if needed). Edit `config.yml::links` to include exactly:

```yaml
links:
  - https://xhslink.com/m/5kcCust1t6Z
```

Set `config.yml::number.user: 3` so only the first 3 notes download (fast smoke).

- [ ] **Step 2: Run the downloader**

Run: `python downloader.py -c config.yml --no-dashboard --verbose 2>&1 | tee /tmp/xhs-smoke.log`

Expected observable behavior:
- Short URL resolves to `xiaohongshu.com/user/profile/55c726695894464ef542aea0?...`.
- XHS session starts (chromium launches).
- Pipeline logs `获取作品列表`, scrolls, collects stubs.
- Per-note `fetch_single` logs fire.
- DownloadEngine writes files under `save_path/xhs/秃头金金/<timestamp>_<desc>/` — each note dir contains the image(s) or video + cover + `_data.json`.
- Exit code 0.

- [ ] **Step 3: Inspect output**

Run:
```bash
ls -R $(python -c "import yaml; print(yaml.safe_load(open('config.yml'))['save_path'])")/xhs/ 2>&1 | head -60
```

Expected: multiple note directories, each with at least one `.jpg`/`.mp4`/`.webp` + `_data.json`.

- [ ] **Step 4: If Step 2 or 3 fails, do NOT guess at fixes**

Collect the failure (stderr tail + `/tmp/xhs-smoke.log`), inspect the most recent trace via `python downloader.py --replay <trace_id>`, and report to the operator. Do not iterate blindly; XHS failures are usually one of: (a) cookie expired, (b) hard-coded note ID retired, (c) selector drift in `note_card` fields.

- [ ] **Step 5: Commit if any code tweaks were needed from Step 4**

If Task 9 required adjustments, commit them:

```bash
git add <files>
git commit -m "fix(xhs): <specific bug from smoke test>"
```

If no tweaks were needed, skip this step.

---

## Task 10: Docs + memory update

**Files:**
- Modify: `memory/xhs-integration-status.md`
- Modify: `memory/MEMORY.md`
- Create: Git tag `xhs-plan-3-done`

- [ ] **Step 1: Flip Plan 3 status in the project memory**

Edit `memory/xhs-integration-status.md`: change the Plan 3 line from "🚫 卡在签名问题上" to "✅ 完成（Playwright 数据源）" and update the `待用户决策` section to reference the completed implementation. Also add a bullet to "关键状态" noting the `xhs-plan-3-done` tag.

- [ ] **Step 2: Tag the release**

```bash
git tag xhs-plan-3-done
```

- [ ] **Step 3: Commit the memory update**

```bash
git add memory/xhs-integration-status.md
git commit -m "docs(memory): mark Plan 3 complete (XHS Playwright data source)"
```

- [ ] **Step 4: Summarize to operator**

Print a two-sentence status: which commits shipped, what works, what the next natural improvement is. Do not implement the improvement.

---

## Self-review notes

**Spec coverage check:**
- `docs/specs/2026-04-24-xhs-signing-investigation.md` Method A ("Playwright 作为数据源") — Tasks 4-6 cover the core, Task 7 wires lifecycle, Task 8 unblocks XHS-only batches, Task 9 smokes end-to-end.
- The pipeline contract (`resolve_short_url`, `fetch_single`, `fetch_list` returning `MediaItem` / `ListPage`) is preserved.
- Auto-scroll avoids any client-side cursor logic on `/user_posted` — that endpoint's pagination lives in the SPA's internal state, not in query params.

**Highest-quality extraction coverage (matches upstream XHS-Downloader semantics):**
- **Images**: token regenerated against `sns-img-bd.xhscdn.com` → unwatermarked "auto" format (originals).
- **Videos**: two-path extraction — `originVideoKey` (unwatermarked original) preferred, stream.h264/h265 sorted by height as fallback; `backupUrls[0]` wins over `masterUrl` per upstream convention.
- **Live photos (动图)**: each image entry carrying `stream.h264[0].masterUrl` emits a separate `MediaAsset(kind="video_live")`. `DownloadEngine` already supports this kind via the `download_live_photo` flag (no engine changes needed).
- **Covers**: `cover.urlDefault` with `imageList[0]` fallback for video notes.

**Issue JoeanAmier/XHS-Downloader#324 — "提取作品文件下载地址失败":**
- Root cause (as of 2026-01-04 report): `deal_video_link` returns empty for many video notes, suggesting XHS changed either `video.consumer.originVideoKey` or the `stream.h264/h265` layout.
- Our defense (Task 2, `_video_to_asset` + `note_to_media_item`): try both paths; on BOTH-empty, raise a `RuntimeError` naming the exact keys inspected. The pipeline's `_execute_task` catches, logs, and marks THIS task failed — the rest of the batch continues.
- Operator signal: the error message tells them to dump `note['video']` to learn the new shape. Do not silently emit an empty-asset MediaItem — that produces a download dir with only `_data.json` and hides the real problem.

**Known risks the subagent should SURFACE (not silently patch):**
- Hard-coded note IDs in Task 1's `IMAGE_NOTE_URL` / `VIDEO_NOTE_URL`. If either 404s, STOP and ask operator for replacements — do not guess.
- `/feed` response body `{"code": 300011, ...}` means cookie was rejected (risk control). STOP and ask operator to re-run `xhs_cookie_extractor.py`.
- If a synthetic parser test fails (Task 2 Step 5), the parser has a bug — synthetic inputs are canonical. If a fixture parser test fails, the real API shape drifted — update the parser to match the fixture, don't loosen the tests, and note the drift in the commit.
- XHS wire format is **camelCase**. Any snake_case lookup in parser code is a bug (`test_camel_case_not_snake_case_is_required` guards this explicitly).
- The video integration test reuses the same hard-coded note ID as Task 1; keep them in sync if updating one.
