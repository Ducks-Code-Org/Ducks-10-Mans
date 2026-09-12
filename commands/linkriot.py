"Link your Riot account to your Discord account."

import asyncio

import aiohttp
from discord.ext import commands

from commands import BotCommands
from database import users, mmr_collection
from riot_api import RiotApiInconclusive, get_account_by_riot_id
from tracker_links import tracker_link


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
            await ctx.send(f"Network error reaching HenrikDev API: {e}")
            return

        # fully document API outcomes
        if payload is None or not payload.get("_raw"):
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
                print(
                    f"[linkriot] Removed stale Riot ID link {riot_name}#{riot_tag} "
                    f"from discord id {stale.get('discord_id')}"
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
