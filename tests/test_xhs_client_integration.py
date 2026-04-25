"""XHS Playwright integration tests — skipped unless XHS_INTEGRATION=1.

Requires:
  - config.yml::cookies.xhs populated (run xhs_cookie_extractor.py first)
  - playwright + chromium installed
  - network access to xiaohongshu.com
"""
from __future__ import annotations

import json
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
    """Pull (noteId, xsecToken) from a captured single-note SSR fixture.

    Fixture top-level shape: {comments, currentTime, note, widgets}.
    The note dict has noteId and xsecToken (camelCase).
    """
    fix_path = (
        Path(__file__).parent / "fixtures" / "xhs" / fixture_name
    )
    if not fix_path.exists():
        pytest.skip(f"fixture {fixture_name} missing — run Task 1 first")
    body = json.loads(fix_path.read_text(encoding="utf-8"))
    note = body.get("note") or {}
    nid = note.get("noteId") or note.get("id")
    xsec = note.get("xsecToken") or note.get("xsec_token") or ""
    assert nid, f"{fixture_name} note has no noteId"
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
