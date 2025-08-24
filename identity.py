# identity.py
import aiohttp
from database import users
from riot_api import get_account_by_puuid, get_account_by_riot_id


async def ensure_current_riot_identity(discord_id: int):
    doc = users.find_one({"discord_id": str(discord_id)})
    if not doc:
        return (
            False,
            "You need to link your Riot account first using `!linkriot Name#Tag`.",
            None,
        )

    puuid = (doc.get("puuid") or "").strip()
    name = (doc.get("name") or "").strip()
    tag = (doc.get("tag") or "").strip()

    if not puuid and (not name or not tag):
        return (
            False,
            "Your Riot link looks incomplete. Re-link with `!linkriot Name#Tag`.",
            None,
        )

    async with aiohttp.ClientSession() as session:
        acc = None
        if puuid:
            acc = await get_account_by_puuid(session, puuid)
        if acc is None and name and tag:
            acc = await get_account_by_riot_id(session, name, tag)

        if acc is None:
            return (
                False,
                "I couldn’t find your Riot account anymore. Re-link with `!linkriot Name#Tag`.",
                None,
            )

        new_name = acc["gameName"]
        new_tag = acc["tagLine"]
        new_puuid = acc["puuid"]

        updates = {}
        if new_puuid and new_puuid != puuid:
            updates["puuid"] = new_puuid
        if new_name and new_name != name:
            updates["name"] = new_name
        if new_tag and new_tag != tag:
            updates["tag"] = new_tag

        if updates:
            users.update_one({"_id": doc["_id"]}, {"$set": updates})
            doc.update(updates)

        return (True, "", doc)
