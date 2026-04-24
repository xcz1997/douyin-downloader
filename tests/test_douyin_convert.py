# tests/test_douyin_convert.py
from core.platform import MediaItem
from core.platforms.douyin import aweme_to_media_item


def test_video_post():
    aweme = {
        "aweme_id": "123",
        "desc": "hello",
        "create_time": 1700000000,
        "author": {"nickname": "alice"},
        "video": {
            "bit_rate": [
                {
                    "bit_rate": 1000000,
                    "play_addr": {"url_list": ["https://low/a.mp4"]},
                },
                {
                    "bit_rate": 2000000,
                    "play_addr": {"url_list": ["https://hi/a.mp4"]},
                },
            ],
            "play_addr": {"url_list": ["https://fall/a.mp4"]},
            "cover": {"url_list": ["https://cov/c.jpg"]},
        },
        "music": {"play_url": {"url_list": ["https://m/x.mp3"]}},
    }
    item = aweme_to_media_item(aweme)
    assert isinstance(item, MediaItem)
    assert item.platform == "douyin"
    assert item.id == "123"
    assert item.author == "alice"
    assert item.desc == "hello"
    assert item.create_time == 1700000000

    kinds = [a.kind for a in item.assets]
    assert "video_main" in kinds
    assert "cover" in kinds
    assert "music" in kinds

    video_asset = next(a for a in item.assets if a.kind == "video_main")
    assert video_asset.url == "https://hi/a.mp4"
    assert "https://low/a.mp4" in video_asset.fallback_urls
    assert "https://fall/a.mp4" in video_asset.fallback_urls


def test_image_post():
    aweme = {
        "aweme_id": "999",
        "desc": "图集",
        "create_time": 1700000000,
        "author": {"nickname": "bob"},
        "images": [
            {
                "download_url_list": ["https://d/1.webp"],
                "url_list": ["https://u/1.jpg"],
            },
            {
                "url_list": ["https://u/2.jpg"],
            },
        ],
        "video": {"cover": {"url_list": ["https://cov/c.jpg"]}},
    }
    item = aweme_to_media_item(aweme)
    image_assets = [a for a in item.assets if a.kind == "image"]
    assert len(image_assets) == 2
    assert image_assets[0].url == "https://d/1.webp"
    assert image_assets[0].ext == "webp"
    assert image_assets[1].url == "https://u/2.jpg"
    assert image_assets[1].ext == "jpg"


def test_missing_music():
    aweme = {
        "aweme_id": "1",
        "desc": "",
        "create_time": 0,
        "author": {"nickname": "x"},
        "video": {
            "play_addr": {"url_list": ["https://v/1.mp4"]},
        },
    }
    item = aweme_to_media_item(aweme)
    assert not any(a.kind == "music" for a in item.assets)


def test_cover_fallbacks():
    aweme = {
        "aweme_id": "1",
        "desc": "",
        "create_time": 0,
        "author": {"nickname": "x"},
        "video": {
            "play_addr": {"url_list": ["https://v/1.mp4"]},
            "origin_cover": {"url_list": ["https://cov/o.jpg"]},
            "cover": {"url_list": ["https://cov/c.jpg"]},
            "dynamic_cover": {"url_list": ["https://cov/d.jpg"]},
        },
    }
    item = aweme_to_media_item(aweme)
    covers = [a for a in item.assets if a.kind == "cover"]
    assert len(covers) == 1
    assert covers[0].url == "https://cov/o.jpg"
    assert "https://cov/c.jpg" in covers[0].fallback_urls
    assert "https://cov/d.jpg" in covers[0].fallback_urls


def test_raw_preserved():
    aweme = {
        "aweme_id": "1", "desc": "", "create_time": 0,
        "author": {"nickname": "x"},
        "video": {"play_addr": {"url_list": ["https://v/1.mp4"]}},
    }
    item = aweme_to_media_item(aweme)
    assert item.raw == aweme
