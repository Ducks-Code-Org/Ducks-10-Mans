"Link your Riot account to your Discord account."

import requests
import discord
from discord.ext import commands

from commands import BotCommands
from database import users, mmr_collection, tdm_mmr_collection
from globals import api_key


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

        if not api_key or not api_key.strip():
            await ctx.send("API key is not configured")
            return

        from urllib.parse import quote

        q_name = quote(riot_name, safe="")
        q_tag = quote(riot_tag, safe="")

        url = f"https://api.henrikdev.xyz/valorant/v2/account/{q_name}/{q_tag}"
        try:
            resp = requests.get(url, headers={"Authorization": api_key}, timeout=30)
        except requests.RequestException as e:
            await ctx.send(f"Network error reaching HenrikDev API: {e}")
            return

        # fully document API outcomes
        if resp.status_code == 401:
            await ctx.send(
                "HenrikDev API rejected the request (401). Check that your API key is valid."
            )
            return
        if resp.status_code == 429:
            await ctx.send("Rate limit hit (429). Try again in a bit.")
            return
        if resp.status_code == 503:
            await ctx.send(
                "Riot/HenrikDev upstream is temporarily unavailable (503). Try again later."
            )
            return
        if resp.status_code == 404:
            await ctx.send(
                "Could not find that Riot account. Double-check the name and tag."
            )
            return
        if resp.status_code != 200:
            await ctx.send(f"Unexpected error from API ({resp.status_code}).")
            return

        data = resp.json()
        if "data" not in data:
            await ctx.send(
                "Could not find your Riot account. Please check the name and tag."
            )
            return

        discord_id = str(ctx.author.id)
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
        tdm_mmr_collection.update_one(
            {"player_id": discord_id}, {"$set": {"name": full_name}}, upsert=False
        )

        if self.bot.signup_active and any(
            p["id"] == discord_id for p in self.bot.queue
        ):
            if self.bot.current_signup_message:
                riot_names = []
                for player in self.bot.queue:
                    player_data = users.find_one({"discord_id": player["id"]})
                    riot_names.append(
                        f"{player_data.get('name')}#{player_data.get('tag')}"
                        if player_data
                        else "Unknown"
                    )
                try:
                    await self.bot.current_signup_message.edit(
                        content="Click a button to manage your queue status!\n"
                        + f"Current queue ({len(self.bot.queue)}/10): {', '.join(riot_names)}"
                    )
                except discord.NotFound:
                    pass
            await ctx.send(
                f"Successfully linked {full_name} to your Discord account and updated your active queue entry."
            )
        else:
            await ctx.send(f"Successfully linked {full_name} to your Discord account.")
