# riot_api.py

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

import requests
import aiohttp

from globals import API_KEY

# Base API
HENRIK_BASE = "https://api.henrikdev.xyz/valorant"


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
) -> Optional[Dict[str, Any]]:
    safe_name = quote((name or "").strip(), safe="")
    safe_tag = quote((tag or "").strip(), safe="")
    url = f"{HENRIK_BASE}/v1/account/{safe_name}/{safe_tag}"

    async with session.get(url, headers=_headers(), timeout=timeout) as r:
        if r.status == 404:
            return None
        r.raise_for_status()
        data = await r.json()
        return _normalize_account_payload(data)


async def get_account_by_puuid(
    session: aiohttp.ClientSession,
    puuid: str,
    *,
    timeout: int = 10,
) -> Optional[Dict[str, Any]]:

    puuid = (puuid or "").strip()
    url = f"{HENRIK_BASE}/v1/by-puuid/account/{puuid}"

    async with session.get(url, headers=_headers(), timeout=timeout) as r:
        if r.status == 404:
            return None
        r.raise_for_status()
        data = await r.json()
        return _normalize_account_payload(data)


# requests
def verify_riot_account(name: str, tag: str) -> Tuple[bool, str]:
    name = (name or "").strip()
    tag = (tag or "").strip()

    if not name or not tag:
        return (False, "Missing Riot name or tag.")

    url = f"{HENRIK_BASE}/v2/account/{quote(name, safe='')}/{quote(tag, safe='')}"

    try:
        r = requests.get(url, headers=_headers(), timeout=10)
    except requests.RequestException as e:
        # Network issues: DNS, timeouts, TLS, etc.
        return (False, f"Network error: {e.__class__.__name__}")

    if r.status_code == 200:
        return (True, "ok")

    if r.status_code == 404:
        return (False, f"Account `{name}#{tag}` not found.")

    if r.status_code in (401, 403):
        return (
            False,
            "Riot lookup failed: API key missing or invalid. Ask an admin to set env `api_key`.",
        )

    # fallback
    return (
        False,
        f"Riot API error ({r.status_code}). Try again in a few seconds or relink your account with `!linkriot`.",
    )
