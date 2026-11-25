# identity.py
import logging

import aiohttp

from database import users
from services.riot_api import (
    RiotApiInconclusive,
    get_account_by_puuid,
    get_account_by_riot_id,
)

log = logging.getLogger(__name__)


def canonical_riot_id(payload: dict, name: str, tag: str) -> str:
    """Canonical `GameName#TagLine` from a normalized Riot account payload.

    The payload's own casing wins; the caller's input is the fallback for
    sparse payloads so a link is never stored without a display field.
    """
    return (payload.get("riotId") or f"{name}#{tag}").strip()


async def ensure_current_riot_identity(discord_id: int):
    doc = users.find_one({"discord_id": str(discord_id)})
    if not doc:
        return (
            False,
            "You need to link your Riot account first using `/linkriot Name#Tag`.",
            None,
        )

    puuid = (doc.get("puuid") or "").strip()
    name = (doc.get("name") or "").strip()
    tag = (doc.get("tag") or "").strip()

    if not puuid and (not name or not tag):
        return (
            False,
            "Your Riot link looks incomplete. Re-link with `/linkriot Name#Tag`.",
            None,
        )

    async with aiohttp.ClientSession() as session:
        acc = None
        try:
            if puuid:
                acc = await get_account_by_puuid(session, puuid, priority=True)
            if acc is None and name and tag:
                acc = await get_account_by_riot_id(session, name, tag, priority=True)
        except RiotApiInconclusive:
            # Rate limits / API problems are inconclusive, never failures.
            # Skip the refresh silently so signup isn't blocked or crashed.
            log.warning(
                "Riot lookup inconclusive (rate limited or API error); skipping identity refresh"
            )
            return (True, "", doc)

        if acc is None:
            return (
                False,
                "I couldn’t find your Riot account anymore. Re-link with `/linkriot Name#Tag`.",
                None,
            )

        new_name = acc["gameName"]
        new_tag = acc["tagLine"]
        new_puuid = acc["puuid"]

        updates = {}
        if new_puuid and new_puuid != puuid:
            updates["puuid"] = new_puuid
        if new_name and new_name != name:
            updates["name"] = new_name.lower().strip()
        if new_tag and new_tag != tag:
            updates["tag"] = new_tag.lower().strip()
        # Canonical casing for the managed nickname (issue #268). Existing
        # links are filled in on their next signup; no bulk API fetch.
        if new_name and new_tag:
            canonical = canonical_riot_id(acc, new_name, new_tag)
            if canonical != (doc.get("riot_display_name") or "").strip():
                updates["riot_display_name"] = canonical

        log.info("Updating database for: %s#%s", new_name, new_tag)
        if updates:
            users.update_one({"_id": doc["_id"]}, {"$set": updates})
            doc.update(updates)

        return (True, "", doc)
