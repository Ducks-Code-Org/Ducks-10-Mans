import asyncio
import os
import sys
from datetime import datetime

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from riot_api import (
    RiotApiInconclusive,
    _henrik_get_json,
    get_recent_matches_async,
    verify_riot_account_async,
    riot_account_exists_async,
    _get_rate_lock,
)


def test_rate_limiter_slots():
    """Sliding-window limiter should stay at/below 30 slots per minute."""

    async def run():
        got = []
        for _ in range(35):
            try:
                await _henrik_get_json(None, "http://example.invalid", retries=0)
            except Exception:
                pass
            async with _get_rate_lock():
                got.append(len(_rate_slots))
        return max(got)

    assert asyncio.run(run()) <= 30, "rate limiter exceeded 30 req/min"


def test_verify_missing_account_returns_false():
    async def run():
        async with aiohttp.ClientSession() as session:
            return await verify_riot_account_async(
                session, "definitely-not-a-real-riot-id-xyz", "tag"
            )

    ok, reason = asyncio.run(run())
    # 404 (no key) vs 401/403 (bad/missing key) are both valid outcomes;
    # the account must never be reported as verified either way.
    assert ok is False
    assert "not found" in reason.lower() or "api key" in reason.lower()


def test_account_exists_missing():
    async def run():
        async with aiohttp.ClientSession() as session:
            return await riot_account_exists_async(
                session, "definitely-not-a-real-riot-id-xyz", "tag"
            )

    result = asyncio.run(run())
    # False (404) or None (401/403 without a key); never True for a bogus ID.
    assert result is not True


def demo():
    test_verify_missing_account_returns_false()
    test_account_exists_missing()
    print("all riot_api service self-checks passed")


if __name__ == "__main__":
    demo()