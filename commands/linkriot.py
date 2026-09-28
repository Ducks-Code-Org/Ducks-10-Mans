"Link your Riot account to your Discord account."

import asyncio
import logging

import aiohttp
from discord import app_commands
from discord.ext import commands

from commands import BotCommands
from database import mmr_collection, users
from services.riot_api import RiotApiInconclusive, get_account_by_riot_id
from tracker_links import tracker_link

# Message reused by both the Riot-ID and puuid conflict checks.
_ALREADY_LINKED = (
    "That Riot account is already linked to another Discord account. "
    "If it's yours, ask an admin to unlink it first."
)

log = logging.getLogger(__name__)


async def setup(bot):
    await bot.add_cog(LinkRiotCommand(bot))


class LinkRiotCommand(BotCommands):
    @commands.hybrid_command(
        name="linkriot",
        description="Link your Riot account to your Discord account (hidden reply)",
    )
    @app_commands.describe(riot_input="Your Riot ID in Name#Tag format")
    async def linkriot(self, ctx, *, riot_input: str):
        # Validate "Name#Tag"
        try:
            riot_name, riot_tag = riot_input.rsplit("#", 1)
        except ValueError:
            await ctx.send(
                "Please provide your Riot ID in the format: `Name#Tag`", ephemeral=True
            )
            return

        try:
            async with aiohttp.ClientSession() as session:
                payload = await get_account_by_riot_id(
                    session, riot_name, riot_tag, priority=True
                )
        except (RiotApiInconclusive, aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.error("Network error linking Riot ID: %s", e, exc_info=e)
            await ctx.send(f"Network error reaching HenrikDev API: {e}", ephemeral=True)
            return

        # fully document API outcomes
        if payload is None or not payload.get("_raw"):
            log.warning(
                "Link rejected: Riot account %s#%s not found", riot_name, riot_tag
            )
            await ctx.send(
                "Could not find that Riot account. Double-check the name and tag.",
                ephemeral=True,
            )
            return

        discord_id = str(ctx.author.id)

        # Persist the puuid so a later Riot ID rename can be resolved back to
        # this account instead of looking like a dead link (issue #182).
        new_puuid = (payload.get("puuid") or "").strip()

        # A Riot account may only be linked to one Discord account. Reject
        # the link when another account owns it (by Riot ID or, when known,
        # by puuid — a renamed account is still the same account). Never
        # touch the other user's data: their MMR/stats stay intact.
        conflict_query = {
            "$or": [
                {"name": riot_name.lower().strip(), "tag": riot_tag.lower().strip()}
            ]
        }
        if new_puuid:
            conflict_query["$or"].append({"puuid": new_puuid})
        for owner in users.find(conflict_query):
            if str(owner.get("discord_id")) != discord_id:
                log.warning(
                    "Link rejected: Riot ID %s#%s already linked to discord id %s",
                    riot_name,
                    riot_tag,
                    owner.get("discord_id"),
                )
                await ctx.send(_ALREADY_LINKED, ephemeral=True)
                return

        set_fields = {
            "discord_id": discord_id,
            "name": riot_name.lower().strip(),
            "tag": riot_tag.lower().strip(),
        }
        if new_puuid:
            set_fields["puuid"] = new_puuid
        users.update_one(
            {"discord_id": discord_id},
            {"$set": set_fields},
            upsert=True,
        )

        full_name = f"{riot_name}#{riot_tag}"
        mmr_collection.update_one(
            {"player_id": discord_id}, {"$set": {"name": full_name}}, upsert=False
        )

        await ctx.send(
            f"Successfully linked {tracker_link(riot_name, riot_tag)} to your Discord account.",
            ephemeral=True,
        )
        log.info(
            "Linked Riot ID %s#%s to discord id %s", riot_name, riot_tag, discord_id
        )
