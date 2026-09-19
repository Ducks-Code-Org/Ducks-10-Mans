import asyncio
import os
import sys

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.riot_api import (
    RiotApiInconclusive,
    _get_rate_lock,
    _henrik_get_json,
    _rate_slots,
    _reserve_rate_slot,
    get_account_by_riot_id,
    riot_account_exists_async,
    verify_riot_account_async,
)


async def test_rate_limiter_slots():
    """Sliding-window limiter should stay at/below 30 slots per minute."""
    for _ in range(35):
        try:
            # priority=True may poll up to 8s once the window is full;
            # cut it short so the self-check stays fast.
            await asyncio.wait_for(_reserve_rate_slot(priority=True), timeout=0.05)
        except (asyncio.TimeoutError, RiotApiInconclusive):
            pass
    async with _get_rate_lock():
        slots = len(_rate_slots)
    _rate_slots.clear()  # don't starve the live-API checks below
    assert slots <= 30, "rate limiter exceeded 30 req/min"


async def test_verify_missing_account_returns_false():
    async with aiohttp.ClientSession() as session:
        ok, reason = await verify_riot_account_async(
            session, "definitely-not-a-real-riot-id-xyz", "tag"
        )
    # 404 (no key) vs 401/403 (bad/missing key) are both valid outcomes;
    # the account must never be reported as verified either way.
    assert ok is False
    assert "not found" in reason.lower() or "api key" in reason.lower()


async def test_account_exists_missing():
    async with aiohttp.ClientSession() as session:
        result = await riot_account_exists_async(
            session, "definitely-not-a-real-riot-id-xyz", "tag"
        )
    # False (404) or None (401/403 without a key); never True for a bogus ID.
    assert result is not True


async def test_429_is_not_retried():
    """A 429 must surface after exactly ONE HTTP request (no retries)."""
    calls = {"count": 0}

    class _FakeResp:
        def __init__(self):
            self.status = 429
            self.headers = {}

        async def __aenter__(self):
            calls["count"] += 1
            return self

        async def __aexit__(self, *a):
            return False

    class _FakeSession:
        def get(self, url, **kw):
            return _FakeResp()

    import services.riot_api as ra

    orig_headers = ra._headers
    ra._headers = lambda: {}
    try:
        await ra._henrik_get_json(_FakeSession(), "https://x/y")
    except RiotApiInconclusive:
        pass
    else:
        raise AssertionError("429 must surface as RiotApiInconclusive")
    assert (
        calls["count"] == 1
    ), f"429 must not be retried (default retries=0); made {calls['count']} calls"
    ra._headers = orig_headers

    # The account lookup helper inherits the no-retry default too.
    calls["count"] = 0
    ra._headers = lambda: {}
    try:
        await get_account_by_riot_id(_FakeSession(), "a", "b")
    except RiotApiInconclusive:
        pass
    assert calls["count"] == 1, "get_account_by_riot_id must not retry 429s"
    ra._headers = orig_headers

    # Explicit opt-in still retries (the escape hatch is preserved).
    calls["count"] = 0
    try:
        await ra._henrik_get_json(_FakeSession(), "https://x/y", retries=2)
    except RiotApiInconclusive:
        pass
    assert calls["count"] == 3, "retries=2 must mean 3 total attempts"


async def _main():
    await test_rate_limiter_slots()
    await test_verify_missing_account_returns_false()
    await test_account_exists_missing()
    await test_429_is_not_retried()


def demo():
    asyncio.run(_main())
    print("all riot_api service self-checks passed")


if __name__ == "__main__":
    demo()
