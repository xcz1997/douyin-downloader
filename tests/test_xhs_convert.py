"""XHS note JSON → MediaItem parser tests.

Fixture-driven tests exercise real-shape note objects captured in
Task 1 from window.__INITIAL_STATE__.note.noteDetailMap. Synthetic
tests cover:
- Live photo (动图) extraction (kind == "video_live")
- originVideoKey fast-path preference
- stream.h264 resolution selection
- Issue #324 mitigation (raise on no-URL-extractable video)
- camelCase vs snake_case drift guard
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


def _extract_note(fixture_body: dict) -> dict:
    """Pull the rich note dict from a single-note SSR fixture.

    Fixtures are captured from window.__INITIAL_STATE__.note.noteDetailMap[<id>]
    which has exactly four keys: comments, currentTime, note, widgets.
    The 'note' value is what the parser consumes.
    """
    note = fixture_body.get("note")
    assert note, (
        "fixture has no 'note' key — re-capture with the SSR "
        "extractor (window.__INITIAL_STATE__.note.noteDetailMap)."
    )
    return note


# ---- Fixture-driven tests ----------------------------------------------


def test_image_note_basic_fields():
    note = _extract_note(_load("note_image.json"))
    item = note_to_media_item(note)
    assert isinstance(item, MediaItem)
    assert item.platform == "xhs"
    assert item.id and isinstance(item.id, str)
    assert item.author and isinstance(item.author, str)
    assert isinstance(item.desc, str)
    assert item.create_time > 0
    assert item.raw is note


def test_image_note_has_image_assets():
    note = _extract_note(_load("note_image.json"))
    item = note_to_media_item(note)
    images = [a for a in item.assets if a.kind == "image"]
    assert len(images) >= 1
    for a in images:
        assert a.url.startswith("http")
        assert a.ext in ("jpg", "png", "webp")
        # Upstream regenerates to sns-img-bd CDN for highest quality.
        assert "sns-img-bd.xhscdn.com" in a.url or a.url == a.url.split("!")[0]


def test_image_note_has_live_photos():
    """秃头金金 fixture has multiple live photos at imageList[1] / [2]."""
    note = _extract_note(_load("note_image.json"))
    item = note_to_media_item(note)
    lives = [a for a in item.assets if a.kind == "video_live"]
    assert len(lives) >= 1, (
        "fixture is known to contain live photos; parser missed them"
    )
    for a in lives:
        assert a.url.startswith("http")
        assert a.ext == "mp4"


def test_video_note_has_video_asset():
    note = _extract_note(_load("note_video.json"))
    item = note_to_media_item(note)
    videos = [a for a in item.assets if a.kind == "video_main"]
    assert len(videos) == 1
    assert videos[0].url.startswith("http")
    assert videos[0].ext == "mp4"


def test_video_note_has_cover_asset():
    note = _extract_note(_load("note_video.json"))
    item = note_to_media_item(note)
    covers = [a for a in item.assets if a.kind == "cover"]
    assert len(covers) == 1
    assert covers[0].url.startswith("http")


def test_video_note_no_image_assets():
    """Video notes must not leak image assets (kind discipline)."""
    note = _extract_note(_load("note_video.json"))
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
        "imageList": [{"urlDefault": "http://host/ts/hash/tok!wm"}],
    }
    item = note_to_media_item(note)
    v = next(a for a in item.assets if a.kind == "video_main")
    assert v.url == "https://sns-video-bd.xhscdn.com/pre/abc.mp4"
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
            {"urlDefault": "http://host/ts/hash/tok!wm"},
            {"urlDefault": "http://host/ts/hash/tok2!wm"},
        ],
    }
    item = note_to_media_item(note)
    kinds = [a.kind for a in item.assets]
    assert kinds == ["image", "image"]


def test_image_token_regenerated_to_sns_img_bd():
    """Real XHS image URLs are 6 segments: http://host/ts/hash/token!wm.

    Token is everything after the 5th '/' (i.e. parts[5:] joined),
    with the watermark suffix stripped at '!'. Output goes onto
    sns-img-bd.xhscdn.com directly under the bare token.
    """
    note = {
        "noteId": "N6", "type": "normal", "user": {"nickname": "u"},
        "time": 1_700_000_000_000, "desc": "",
        "imageList": [{
            "urlDefault":
            "http://sns-webpic-qc.xhscdn.com/ts123/hashabc/tok456!watermark",
        }],
    }
    item = note_to_media_item(note)
    img = next(a for a in item.assets if a.kind == "image")
    assert img.url == "https://sns-img-bd.xhscdn.com/tok456"


def test_camel_case_not_snake_case_is_required():
    """Regression guard: parser must not accept snake_case aliases.

    Prevents accidental snake_case drift; XHS SSR wire is camelCase.
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
