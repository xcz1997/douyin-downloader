import asyncio
import time
import pytest
from unittest.mock import MagicMock
from core.api_client import DouyinAPIClient, RateLimiter
from core.models import CookieState, TraceSpan
from core.tracer import Tracer
from core.logger import DualLogger


def _make_cookie():
    return CookieState(value="ttwid=abc", source="test", obtained_at=0)


@pytest.mark.asyncio
async def test_rate_limiter_spacing():
    limiter = RateLimiter(max_per_second=10.0)
    t0 = time.time()
    await limiter.acquire()
    await limiter.acquire()
    elapsed = time.time() - t0
    assert elapsed >= 0.09


@pytest.mark.asyncio
async def test_client_creation(tmp_path):
    tracer = Tracer(log_dir=tmp_path, session_id="test")
    dl = DualLogger(log_dir=tmp_path, console_level="ERROR")
    log = dl.get("test")
    client = DouyinAPIClient(
        cookie_state=_make_cookie(),
        tracer=tracer,
        logger=log,
    )
    assert client is not None
    await client.close()
    tracer.close()
    dl.close()
