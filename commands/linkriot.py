"Link your Riot account to your Discord account."

import asyncio
import logging

import aiohttp
from discord.ext import commands

from commands import BotCommands
from database import mmr_collection, users
from services.riot_api import RiotApiInconclusive, get_account_by_riot_id
from tracker_links import tracker_link

log = logging.getLogger(__name__)


async def setup(bot):
    await bot.add_cog(LinkRiotCommand(bot))


class LinkRiotCommand(BotCommands):
    @commands.command()
    async def linkriot(self, ctx, *, riot_input):
        # Validate "Name#Tag"
        try:
            riot_name, riot_tag = riot_input.rsplit("#", 1)
        except ValueError:
            await ctx.send("Please provide your Riot ID in the format: `Name#Tag`")
            return

        try:
            async with aiohttp.ClientSession() as session:
                payload = await get_account_by_riot_id(
                    session, riot_name, riot_tag, priority=True
                )
        except (RiotApiInconclusive, aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.error("Network error linking Riot ID: %s", e, exc_info=e)
            await ctx.send(f"Network error reaching HenrikDev API: {e}")
            return

        # fully document API outcomes
        if payload is None or not payload.get("_raw"):
            log.warning(
                "Link rejected: Riot account %s#%s not found", riot_name, riot_tag
            )
            await ctx.send(
                "Could not find that Riot account. Double-check the name and tag."
            )
            return

        discord_id = str(ctx.author.id)

        # Riot IDs can only be linked to one Discord account: remove any
        # stale duplicate links from other users
        for stale in users.find(
            {"name": riot_name.lower().strip(), "tag": riot_tag.lower().strip()}
        ):
            if str(stale.get("discord_id")) != discord_id:
                users.delete_one({"_id": stale["_id"]})
                mmr_collection.delete_one({"player_id": stale.get("discord_id")})
                log.info(
                    "Removed stale Riot ID link %s#%s from discord id %s",
                    riot_name,
                    riot_tag,
                    stale.get("discord_id"),
                )

        users.update_one(
            {"discord_id": discord_id},
            {
                "$set": {
                    "discord_id": discord_id,
                    "name": riot_name.lower().strip(),
                    "tag": riot_tag.lower().strip(),
                }
            },
            upsert=True,
        )

        full_name = f"{riot_name}#{riot_tag}"
        mmr_collection.update_one(
            {"player_id": discord_id}, {"$set": {"name": full_name}}, upsert=False
        )

        await ctx.send(
            f"Successfully linked {tracker_link(riot_name, riot_tag)} to your Discord account."
        )
        log.info(
            "Linked Riot ID %s#%s to discord id %s", riot_name, riot_tag, discord_id
        )
