import asyncio
import os
import sys

import aiohttp

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from riot_api import (
    RiotApiInconclusive,
    _get_rate_lock,
    _rate_slots,
    _reserve_rate_slot,
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


async def _main():
    await test_rate_limiter_slots()
    await test_verify_missing_account_returns_false()
    await test_account_exists_missing()


def demo():
    asyncio.run(_main())
    print("all riot_api service self-checks passed")


if __name__ == "__main__":
    demo()
