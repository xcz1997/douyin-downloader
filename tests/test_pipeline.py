import pytest
from core.pipeline import DownloadPipeline


def test_is_short_url():
    assert DownloadPipeline.is_short_url("https://v.douyin.com/abc123/")
    assert DownloadPipeline.is_short_url("https://v.douyin.com/cGYAzzSDbRQ/")
    assert not DownloadPipeline.is_short_url("https://www.douyin.com/video/123")
    assert not DownloadPipeline.is_short_url("https://example.com")


def test_detect_content_type_video():
    assert DownloadPipeline.detect_content_type("https://www.douyin.com/video/7123456789") == "video"


def test_detect_content_type_image():
    assert DownloadPipeline.detect_content_type("https://www.douyin.com/note/7123456789") == "image"


def test_detect_content_type_user():
    assert DownloadPipeline.detect_content_type("https://www.douyin.com/user/MS4wLjABAAAAtest") == "user"
    assert DownloadPipeline.detect_content_type("https://www.iesdouyin.com/share/user/MS4wLjABAAAAtest?foo=bar") == "user"


def test_detect_content_type_user_from_sec_uid():
    url = "https://www.iesdouyin.com/share/user/MS4wLjABAAAAFS1IZt1jb_H84EdcGwdnRJCs?iid=xxx"
    assert DownloadPipeline.detect_content_type(url) == "user"


def test_extract_video_id():
    assert DownloadPipeline.extract_id("https://www.douyin.com/video/7123456789", "video") == "7123456789"


def test_extract_note_id():
    assert DownloadPipeline.extract_id("https://www.douyin.com/note/7123456789", "image") == "7123456789"


def test_extract_user_id():
    uid = DownloadPipeline.extract_id(
        "https://www.iesdouyin.com/share/user/MS4wLjABAAAAtest?foo=bar", "user"
    )
    assert uid == "MS4wLjABAAAAtest"


def test_extract_user_id_from_sec_uid_param():
    url = "https://www.iesdouyin.com/share/user/MS4wLjABAAAAFS1IZt1jb_H84EdcGwdnRJCsno7pzzwfVNEyxgBV4Dz7z4Rey9GA7qfA7VghpB0h?iid=xxx"
    uid = DownloadPipeline.extract_id(url, "user")
    assert uid == "MS4wLjABAAAAFS1IZt1jb_H84EdcGwdnRJCsno7pzzwfVNEyxgBV4Dz7z4Rey9GA7qfA7VghpB0h"
