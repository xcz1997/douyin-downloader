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
        # /user_posted is triggered on scroll (first page is SSR'd into
        # HTML); /feed fires on load. Scroll only when we're hunting
        # for /user_posted.
        if "/user_posted" in endpoint_substr:
            for _ in range(3):
                if fut.done():
                    break
                await page.evaluate(
                    "window.scrollTo(0, document.body.scrollHeight)"
                )
                await asyncio.sleep(1.2)
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


async def _capture_state_feed(context, goto_url: str, note_id: str,
                              out_path: Path) -> dict:
    """Read window.__INITIAL_STATE__.note.noteDetailMap[note_id] after nav.

    XHS moved the single-note payload entirely to SSR — the browser no
    longer fetches /feed client-side. The SSR state object mirrors what
    /feed used to return; Task 5's runtime fetch_single will use the
    same mechanism.

    Note: the raw state tree contains Vue 3 reactive refs (cycles +
    `_rawValue`/`_value` wrappers), so we JSON.stringify inside the
    page with a cycle-safe replacer and parse on the Python side.
    """
    page = await context.new_page()
    try:
        await page.goto(goto_url, wait_until="domcontentloaded")
        # Give the SSR hydration a beat to settle; usually instant.
        await asyncio.sleep(0.5)
        entry_str = await page.evaluate(
            """(noteId) => {
                const state = window.__INITIAL_STATE__;
                const ndm = state && state.note && state.note.noteDetailMap;
                if (!ndm) return null;
                const entry = ndm[noteId];
                if (!entry) return null;
                const seen = new WeakSet();
                return JSON.stringify(entry, (key, value) => {
                    if (typeof value === 'function') return undefined;
                    if (typeof value === 'object' && value !== null) {
                        if (seen.has(value)) return undefined;
                        seen.add(value);
                    }
                    return value;
                });
            }""",
            note_id,
        )
    finally:
        await page.close()

    if not entry_str:
        raise SystemExit(
            f"window.__INITIAL_STATE__.note.noteDetailMap[{note_id!r}] "
            f"is empty at {goto_url} — SSR state missing; "
            f"check cookie / risk control."
        )
    entry = json.loads(entry_str)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(entry, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {out_path.relative_to(ROOT)}")
    return entry


def _pick_note(user_posted: dict, target_type: str) -> tuple[str, str]:
    data = user_posted.get("data") or {}
    notes = data.get("notes") or []
    if not notes:
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

            await _capture_state_feed(
                context,
                f"https://www.xiaohongshu.com/explore/{img_id}"
                f"?xsec_token={img_xsec}&xsec_source=pc_user",
                img_id,
                FIXTURE_DIR / "note_image.json",
            )
            await _capture_state_feed(
                context,
                f"https://www.xiaohongshu.com/explore/{vid_id}"
                f"?xsec_token={vid_xsec}&xsec_source=pc_user",
                vid_id,
                FIXTURE_DIR / "note_video.json",
            )
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
