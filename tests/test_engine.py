import pytest
from pathlib import Path
from core.downloader_engine import DownloadEngine
from core.tracer import Tracer
from core.logger import DualLogger
from core.models import TraceSpan


@pytest.fixture
def engine_deps(tmp_path):
    tracer = Tracer(log_dir=tmp_path / "logs", session_id="test")
    dl = DualLogger(log_dir=tmp_path / "logs", console_level="ERROR")
    log = dl.get("test")
    save_path = tmp_path / "downloads"
    save_path.mkdir()
    return save_path, tracer, log, dl


def _make_parent_span():
    return TraceSpan(
        trace_id="t_test", span_id="s_test", parent_id=None,
        name="test", start_time=0,
    )


@pytest.mark.asyncio
async def test_download_file_skip_existing(engine_deps):
    save_path, tracer, log, dl = engine_deps
    engine = DownloadEngine(save_path=save_path, tracer=tracer, logger=log)
    target = save_path / "existing.jpg"
    target.write_text("fake image")
    parent = _make_parent_span()

    result = await engine.download_file(
        url="https://example.com/img.jpg",
        path=target,
        parent_span=parent,
    )
    assert result is True
    tracer.close()
    dl.close()


@pytest.mark.asyncio
async def test_build_save_dir(engine_deps):
    save_path, tracer, log, dl = engine_deps
    engine = DownloadEngine(save_path=save_path, tracer=tracer, logger=log)
    aweme = {
        "author": {"nickname": "testuser"},
        "desc": "test video",
        "create_time": 1700000000,
    }
    dir_path = engine._build_save_dir(aweme)
    assert "testuser" in str(dir_path)
    assert dir_path.exists()
    tracer.close()
    dl.close()


def test_get_best_image_url_prefers_download_url_list(engine_deps):
    save_path, tracer, log, dl = engine_deps
    engine = DownloadEngine(save_path=save_path, tracer=tracer, logger=log)
    img = {
        "url_list": [
            "https://cdn.com/img~tplv-dy-aweme-images:q75.webp?sig=a",
            "https://cdn2.com/img~tplv-dy-aweme-images:q75.jpeg?sig=b",
        ],
        "download_url_list": [
            "https://cdn.com/img~tplv-dy-water-v2:2160:2880.webp?sig=c",
            "https://cdn2.com/img~tplv-dy-water-v2:2160:2880.jpeg?sig=d",
        ],
    }
    best, fallbacks, ext = engine._get_best_image_url(img)
    assert "download" not in best or "water" in best
    assert best == "https://cdn.com/img~tplv-dy-water-v2:2160:2880.webp?sig=c"
    assert ext == "webp"
    assert len(fallbacks) == 3
    tracer.close()
    dl.close()


def test_get_best_image_url_falls_back_to_url_list(engine_deps):
    save_path, tracer, log, dl = engine_deps
    engine = DownloadEngine(save_path=save_path, tracer=tracer, logger=log)
    img = {
        "url_list": [
            "https://cdn.com/img.jpeg?sig=a",
        ],
    }
    best, fallbacks, ext = engine._get_best_image_url(img)
    assert best == "https://cdn.com/img.jpeg?sig=a"
    assert ext == "jpg"
    assert fallbacks == []
    tracer.close()
    dl.close()


def test_get_video_url_prefers_bit_rate(engine_deps):
    save_path, tracer, log, dl = engine_deps
    engine = DownloadEngine(save_path=save_path, tracer=tracer, logger=log)
    aweme = {
        "video": {
            "play_addr": {"url_list": ["https://cdn.com/720p.mp4"]},
            "bit_rate": [
                {"bit_rate": 500000, "gear_name": "normal_720", "play_addr": {"url_list": ["https://cdn.com/720p_br.mp4"]}},
                {"bit_rate": 1500000, "gear_name": "normal_1080", "play_addr": {"url_list": ["https://cdn.com/1080p_br.mp4"]}},
                {"bit_rate": 300000, "gear_name": "lower_540", "play_addr": {"url_list": ["https://cdn.com/540p_br.mp4"]}},
            ],
        }
    }
    url = engine._get_video_url(aweme)
    assert "1080p_br" in url
    tracer.close()
    dl.close()


def test_get_video_url_720_to_1080_fallback(engine_deps):
    save_path, tracer, log, dl = engine_deps
    engine = DownloadEngine(save_path=save_path, tracer=tracer, logger=log)
    aweme = {
        "video": {
            "play_addr": {"url_list": ["https://cdn.com/video_720p_abc.mp4"]},
        }
    }
    url = engine._get_video_url(aweme)
    assert "1080p" in url
    assert "720p" not in url
    tracer.close()
    dl.close()


def test_get_video_fallbacks_sorted_by_bitrate(engine_deps):
    save_path, tracer, log, dl = engine_deps
    engine = DownloadEngine(save_path=save_path, tracer=tracer, logger=log)
    aweme = {
        "video": {
            "play_addr": {"url_list": ["https://cdn.com/play.mp4"]},
            "bit_rate": [
                {"bit_rate": 300000, "play_addr": {"url_list": ["https://cdn.com/low.mp4"]}},
                {"bit_rate": 1500000, "play_addr": {"url_list": ["https://cdn.com/high.mp4"]}},
            ],
        }
    }
    fallbacks = engine._get_video_fallbacks(aweme)
    assert fallbacks[0] == "https://cdn.com/high.mp4"
    assert fallbacks[1] == "https://cdn.com/low.mp4"
    tracer.close()
    dl.close()
