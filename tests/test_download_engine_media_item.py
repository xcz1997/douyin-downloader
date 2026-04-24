# tests/test_download_engine_media_item.py
"""DownloadEngine consumes MediaItem (platform-agnostic)."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.downloader_engine import DownloadEngine
from core.models import DownloadResult
from core.platform import MediaAsset, MediaItem


def _make_item() -> MediaItem:
    return MediaItem(
        platform="douyin",
        id="1",
        author="alice",
        desc="hello world",
        create_time=1700000000.0,
        assets=[
            MediaAsset(url="https://v/a.mp4", kind="video_main", ext="mp4"),
            MediaAsset(url="https://m/a.mp3", kind="music", ext="mp3"),
            MediaAsset(url="https://c/a.jpg", kind="cover", ext="jpg"),
        ],
        raw={"aweme_id": "1"},
    )


@pytest.fixture
def tmp_engine(tmp_path):
    tracer = MagicMock()
    tracer.add_event = MagicMock()
    logger = MagicMock()
    engine = DownloadEngine(
        save_path=tmp_path,
        tracer=tracer,
        logger=logger,
        concurrency=2,
    )
    engine.download_file = AsyncMock(return_value=(True, 1024))
    return engine, tmp_path


@pytest.mark.asyncio
async def test_download_media_video(tmp_engine):
    engine, root = tmp_engine
    span = MagicMock()
    result = await engine.download_media(_make_item(), span)

    assert isinstance(result, DownloadResult)
    assert result.success is True
    assert result.files_written == 4  # video + music + cover + data.json

    # Directory layout: save_path/douyin/alice/<ts>_hello world/
    subdirs = list(root.iterdir())
    assert len(subdirs) == 1
    assert subdirs[0].name == "douyin"
    alice = subdirs[0] / "alice"
    assert alice.exists()
    post_dirs = list(alice.iterdir())
    assert len(post_dirs) == 1
    post = post_dirs[0]
    assert post.name.endswith("_hello world")

    data_json = list(post.glob("*_data.json"))
    assert len(data_json) == 1
    assert json.loads(data_json[0].read_text(encoding="utf-8")) == {
        "aweme_id": "1"
    }


@pytest.mark.asyncio
async def test_image_item_live_photo(tmp_engine):
    engine, root = tmp_engine
    span = MagicMock()
    item = MediaItem(
        platform="xhs",
        id="note1",
        author="bob",
        desc="",
        create_time=1700000000.0,
        assets=[
            MediaAsset(
                url="https://i/1.jpg", kind="image", ext="jpg",
                suggested_filename="image_1",
            ),
            MediaAsset(
                url="https://v/1.mp4", kind="video_live", ext="mp4",
                suggested_filename="image_1_live",
            ),
            MediaAsset(
                url="https://i/2.jpg", kind="image", ext="jpg",
                suggested_filename="image_2",
            ),
        ],
        raw={"note_id": "note1"},
    )
    result = await engine.download_media(item, span)
    assert result.success is True
    # 3 assets + 1 json
    assert result.files_written == 4

    # Filenames reflect suggested_filename
    calls = [c.args for c in engine.download_file.await_args_list]
    paths = [c[1] for c in calls]  # arg 1 is `path`
    names = sorted(p.name for p in paths)
    assert "image_1.jpg" in names
    assert "image_1_live.mp4" in names
    assert "image_2.jpg" in names


@pytest.mark.asyncio
async def test_flags_skip_music_cover(tmp_path):
    tracer = MagicMock(); tracer.add_event = MagicMock()
    logger = MagicMock()
    engine = DownloadEngine(
        save_path=tmp_path, tracer=tracer, logger=logger,
        concurrency=2,
        download_music=False, download_cover=False, download_json=False,
    )
    engine.download_file = AsyncMock(return_value=(True, 100))
    span = MagicMock()

    result = await engine.download_media(_make_item(), span)
    # Only video, no music / no cover / no json
    assert result.files_written == 1
