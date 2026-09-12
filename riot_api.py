# riot_api.py

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any, Dict, Optional
from urllib.parse import quote

import aiohttp

from globals import API_KEY

# Base API
HENRIK_BASE = "https://api.henrikdev.xyz/valorant"

# HenrikDev guidelines: a Basic API key allows at most 30 requests per minute.
# All async helpers below share one sliding-window limiter so the bot as a
# whole stays under that budget no matter how many checks run in parallel.
HENRIK_RATE_LIMIT = 30
HENRIK_RATE_WINDOW = 60.0  # seconds
# Slots kept free for interactive callers (signup verification, identity
# refresh). Background work (e.g. the invalid-Riot-ID purge) may only use the
# remaining budget, so a burst of purge requests can never starve a live
# signup command.
INTERACTIVE_RESERVE = 8
# Interactive callers give up (inconclusive) instead of blocking if they
# cannot get a rate-limit slot within this many seconds.
INTERACTIVE_MAX_WAIT = 8.0

_rate_lock: asyncio.Lock | None = None
_rate_slots: deque[float] = deque()


def _get_rate_lock() -> asyncio.Lock:
    # Lazily created so the lock binds to the bot's running event loop
    # rather than whichever loop (if any) existed at import time.
    global _rate_lock  # noqa: W0603
    if _rate_lock is None:
        _rate_lock = asyncio.Lock()
    return _rate_lock


async def _reserve_rate_slot(*, priority: bool = False) -> None:
    """Wait until a request slot is free in the sliding window, then take it.

    Interactive (priority) callers may use the full window and raise
    RiotApiInconclusive rather than wait longer than INTERACTIVE_MAX_WAIT.
    Background callers are capped at HENRIK_RATE_LIMIT - INTERACTIVE_RESERVE
    slots and simply wait — they never error out.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + (INTERACTIVE_MAX_WAIT if priority else float("inf"))
    while True:
        async with _get_rate_lock():
            now = loop.time()
            while _rate_slots and now - _rate_slots[0] >= HENRIK_RATE_WINDOW:
                _rate_slots.popleft()
            limit_for_caller = (
                HENRIK_RATE_LIMIT
                if priority
                else HENRIK_RATE_LIMIT - INTERACTIVE_RESERVE
            )
            if len(_rate_slots) < limit_for_caller:
                _rate_slots.append(now)
                return
            # Window (or background budget) is full: wait until the oldest
            # slot falls out of it.
            wait_for = HENRIK_RATE_WINDOW - (now - _rate_slots[0])
        if loop.time() >= deadline:
            raise RiotApiInconclusive(
                "interactive caller could not get a rate-limit slot in time"
            )
        if priority:
            # Poll briefly so the deadline is honored even if the oldest slot
            # is a long way from expiring.
            await asyncio.sleep(min(max(wait_for, 0.01), 1.0))
        else:
            await asyncio.sleep(max(wait_for, 0.01) + 0.01)


class RiotApiInconclusive(RuntimeError):
    """The Riot/Henrik API could not give a definitive answer.

    Raised when a request stays rate limited (429) after retries or the API
    returns an unexpected status. Callers must treat this as "verification
    skipped" — it must never purge data or block a signup.
    """


def _retry_delay(headers, attempt: int) -> float:
    """Backoff before a 429 retry, honoring Retry-After when provided.

    The delay is capped so interactive paths (signup) stay responsive; if the
    limit persists after retries the caller gets RiotApiInconclusive.
    """
    retry_after = None
    if headers is not None:
        try:
            retry_after = float(headers.get("Retry-After"))
        except (TypeError, ValueError):
            retry_after = None
    if retry_after is not None:
        return min(max(retry_after, 1.0), 8.0)
    return 2.0 * (attempt + 1)


async def _henrik_get_json(
    session: aiohttp.ClientSession,
    url: str,
    *,
    timeout: int = 10,
    retries: int = 2,
    priority: bool = False,
) -> tuple[int, Optional[Dict[str, Any]]]:
    """GET a HenrikDev endpoint through the shared 30 req/min rate limiter.

    Returns (status, parsed_json): (200, data) on success, (404, None) when
    the resource is confirmed missing, (0, None) on network errors, and
    (other_status, None) for unexpected API responses. Retries 429s with
    backoff and raises RiotApiInconclusive once retries are exhausted.
    Interactive callers set priority=True so a saturated window resolves
    quickly to RiotApiInconclusive instead of blocking them.
    """
    for attempt in range(retries + 1):
        await _reserve_rate_slot(priority=priority)
        try:
            async with session.get(url, headers=_headers(), timeout=timeout) as r:
                if r.status == 200:
                    try:
                        data = await r.json()
                    except (aiohttp.ContentTypeError, ValueError):
                        data = None
                    return (200, data)
                if r.status == 404:
                    return (404, None)
                if r.status == 429:
                    if attempt < retries:
                        await asyncio.sleep(_retry_delay(r.headers, attempt))
                        continue
                    raise RiotApiInconclusive(f"429 rate limit persisted for {url}")
                return (r.status, None)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return (0, None)
    # Defensive: the loop above either returns or raises.
    raise RiotApiInconclusive(f"429 rate limit persisted for {url}")


def _headers() -> Dict[str, str]:
    """Return auth headers if API key is present, else empty dict."""
    return {"Authorization": API_KEY} if API_KEY else {}


def _normalize_account_payload(payload: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """
    Normalize Henrik account payloads into a consistent shape.

    Expected payloads may be either:
      { "data": { ... } }  OR  { ... } directly.
    """
    acc = payload.get("data", payload) or {}
    return {
        "puuid": acc.get("puuid"),
        "gameName": (acc.get("name") or acc.get("gameName") or "").strip() or None,
        "tagLine": (acc.get("tag") or acc.get("tagLine") or "").strip() or None,
        "region": (acc.get("region") or "").strip() or None,
        "riotId": (
            (
                f"{(acc.get('name') or acc.get('gameName') or '').strip()}#"
                f"{(acc.get('tag') or acc.get('tagLine') or '').strip()}"
            ).strip("#")
            if (acc.get("name") or acc.get("gameName"))
            and (acc.get("tag") or acc.get("tagLine"))
            else None
        ),
        "_raw": acc,
    }


# async helper functions
async def get_account_by_riot_id(
    session: aiohttp.ClientSession,
    name: str,
    tag: str,
    *,
    timeout: int = 10,
    retries: int = 2,
    priority: bool = False,
) -> Optional[Dict[str, Any]]:
    safe_name = quote((name or "").strip(), safe="")
    safe_tag = quote((tag or "").strip(), safe="")
    url = f"{HENRIK_BASE}/v1/account/{safe_name}/{safe_tag}"

    status, data = await _henrik_get_json(
        session, url, timeout=timeout, retries=retries, priority=priority
    )
    if status == 404 or data is None:
        return None
    return _normalize_account_payload(data)


async def get_account_by_puuid(
    session: aiohttp.ClientSession,
    puuid: str,
    *,
    timeout: int = 10,
    retries: int = 2,
    priority: bool = False,
) -> Optional[Dict[str, Any]]:

    puuid = (puuid or "").strip()
    url = f"{HENRIK_BASE}/v1/by-puuid/account/{puuid}"

    status, data = await _henrik_get_json(
        session, url, timeout=timeout, retries=retries, priority=priority
    )
    if status == 404 or data is None or status == 0:
        return None
    if status != 200:
        # Unexpected API status (e.g. 503): inconclusive, not "account gone".
        raise RiotApiInconclusive(f"Henrik API returned {status} for {url}")
    return _normalize_account_payload(data)


async def riot_account_exists_async(
    session: aiohttp.ClientSession,
    name: str,
    tag: str,
    *,
    timeout: int = 10,
    retries: int = 2,
) -> bool | None:
    """Async version of riot_account_exists, routed through the shared
    30 req/min rate limiter.

    Returns True (exists), False (confirmed missing via 404), or None when
    the result is inconclusive (network/auth/rate-limit errors — never treat
    as invalid). Does not block the event loop, so many checks can run in
    parallel.
    """
    name = (name or "").strip()
    tag = (tag or "").strip()
    if not name or not tag:
        return False

    url = f"{HENRIK_BASE}/v2/account/{quote(name, safe='')}/{quote(tag, safe='')}"

    try:
        status, _data = await _henrik_get_json(
            session, url, timeout=timeout, retries=retries
        )
    except RiotApiInconclusive:
        return None

    if status == 200:
        return True
    if status == 404:
        return False
    return None


async def verify_riot_account_async(
    session: aiohttp.ClientSession,
    name: str,
    tag: str,
    *,
    timeout: int = 10,
) -> tuple[bool, str]:
    """Async counterpart of verify_riot_account for interactive paths.

    Runs with priority=True so a saturated rate-limit window can never block
    the signup button for more than INTERACTIVE_MAX_WAIT. A rate limit (429)
    is inconclusive, never a failure: on persistent 429 this returns
    (True, ...) so the signup proceeds. Unexpected statuses are also
    non-blocking. Network errors are likewise skipped.
    """
    name = (name or "").strip()
    tag = (tag or "").strip()
    if not name or not tag:
        return (False, "Missing Riot name or tag.")

    url = f"{HENRIK_BASE}/v2/account/{quote(name, safe='')}/{quote(tag, safe='')}"

    try:
        status, _data = await _henrik_get_json(
            session, url, timeout=timeout, priority=True
        )
    except RiotApiInconclusive:
        # Rate limit persisted after retries: skip verification, don't block.
        return (True, "rate limited (verification skipped)")

    if status == 200:
        return (True, "ok")
    if status == 404:
        return (False, f"Account `{name}#{tag}` not found.")
    if status == 0:
        return (True, "network error (verification skipped)")
    if status == 401 or status == 403:
        return (
            False,
            "Riot lookup failed: API key missing or invalid. Ask an admin to set env `api_key`.",
        )
    return (True, f"api status {status} (verification skipped)")


async def get_recent_matches_async(
    session: aiohttp.ClientSession,
    name: str,
    tag: str,
    *,
    region: str = "na",
    platform: str = "pc",
    timeout: int = 30,
    retries: int = 2,
    priority: bool = False,
) -> Optional[Dict[str, Any]]:
    """GET the most recent matches for a Riot ID through the shared
    30 req/min rate limiter.

    Returns the parsed payload for HTTP 200, None when the Riot ID has no
    recent matches (404), and raises RiotApiInconclusive on network errors,
    auth failures, persistent 429s or unexpected statuses.
    """
    q_name, q_tag = quote((name or "").strip(), safe=""), quote((tag or "").strip(), safe="")
    url = f"{HENRIK_BASE}/v4/matches/{region}/{platform}/{q_name}/{q_tag}"

    status, data = await _henrik_get_json(
        session, url, timeout=timeout, retries=retries, priority=priority
    )
    if status == 404:
        return None
    if status != 200:
        raise RiotApiInconclusive(f"Henrik API returned {status} for {url}")
    if not isinstance(data, dict) or "data" not in data:
        raise RiotApiInconclusive(f"Henrik API returned malformed payload for {url}")
    return data
