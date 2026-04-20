from core.errors import (
    DouyinError, RetryableError, RateLimitError, NetworkError,
    CookieExpiredError, ConfigError,
    SkippableError, ContentNotFoundError, DownloadFileError,
)


def test_retryable_is_douyin_error():
    assert issubclass(RetryableError, DouyinError)


def test_rate_limit_is_retryable():
    assert issubclass(RateLimitError, RetryableError)
    e = RateLimitError("429 too many requests")
    assert isinstance(e, RetryableError)
    assert isinstance(e, DouyinError)


def test_network_error_is_retryable():
    assert issubclass(NetworkError, RetryableError)


def test_cookie_expired_is_not_retryable():
    e = CookieExpiredError("expired")
    assert isinstance(e, DouyinError)
    assert not isinstance(e, RetryableError)


def test_skippable_hierarchy():
    assert issubclass(ContentNotFoundError, SkippableError)
    assert issubclass(DownloadFileError, SkippableError)
    assert issubclass(SkippableError, DouyinError)
    assert not issubclass(SkippableError, RetryableError)


def test_config_error():
    e = ConfigError("missing links")
    assert isinstance(e, DouyinError)
    assert not isinstance(e, RetryableError)
    assert not isinstance(e, SkippableError)
