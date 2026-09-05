"Starts the signup process for a new match."

import random
import asyncio

import discord
from discord.ext import commands

from commands import BotCommands
from database import mmr_collection, tdm_mmr_collection, users
from riot_api import riot_account_exists
from views.signup_view import SignupView
from identity import ensure_current_riot_identity


def purge_invalid_riot_ids() -> list[str]:
    """Remove linked Riot accounts that no longer exist on Riot's side.

    Returns the display names of the removed players. Inconclusive checks
    (network/API errors) are skipped so flaky API responses never purge data.
    """
    removed = []
    for doc in list(users.find()):
        name = (doc.get("name") or "").strip()
        tag = (doc.get("tag") or "").strip()
        if not name or not tag:
            # Incomplete link: nothing to verify against the API
            continue

        exists = riot_account_exists(name, tag)
        if exists is not False:
            continue

        discord_id = str(doc.get("discord_id"))
        users.delete_one({"_id": doc["_id"]})
        mmr_collection.delete_one({"player_id": discord_id})
        tdm_mmr_collection.delete_one({"player_id": discord_id})
        print(f"[purge] Removed invalid Riot ID {name}#{tag} ({discord_id})")
        removed.append(f"{name}#{tag}")

    return removed


async def setup(bot):
    if not hasattr(bot, "signup_lock"):
        bot.signup_lock = asyncio.Lock()
    await bot.add_cog(SignupCommand(bot))


class SignupCommand(BotCommands):
    @commands.command()
    async def signup(self, ctx):
        async with self.bot.signup_lock:
            if not await ensure_perms(ctx):
                return

            if self.bot.signup_active:
                await ctx.send("A signup is already in progress.")
                return

            if self.bot.match_not_reported:
                await ctx.send("Report the last match before starting another one.")
                return

            ok, msg, _db_user = await ensure_current_riot_identity(ctx.author.id)
            if not ok:
                await ctx.send(msg)
                return

            # Clean out Riot IDs that no longer exist before starting the queue
            removed = purge_invalid_riot_ids()
            if removed:
                await ctx.send(
                    "Removed "
                    + ", ".join(f"`{r}`" for r in removed)
                    + " from the database (Riot account no longer exists)."
                )

            self.bot.load_mmr_data()
            print("[DEBUG] Reloaded MMR data at start of signup")

            # Clear any existing signup view
            if self.bot.signup_view is not None:
                self.bot.signup_view = None

        # Reset all match related states
        self.bot.signup_active = True
        self.bot.queue = []
        self.bot.captain1 = None
        self.bot.captain2 = None
        self.bot.team1 = []
        self.bot.team2 = []
        self.bot.chosen_mode = None
        self.bot.selected_map = None

        self.bot.match_name = f"match-{random.randrange(1, 10**4):04}"

        try:
            self.bot.match_role = await ctx.guild.create_role(
                name=self.bot.match_name, hoist=True
            )
            await ctx.guild.edit_role_positions(positions={self.bot.match_role: 5})

            match_channel_permissions = {
                ctx.guild.default_role: discord.PermissionOverwrite(
                    send_messages=False
                ),
                self.bot.match_role: discord.PermissionOverwrite(send_messages=True),
            }

            self.bot.match_channel = await ctx.guild.create_text_channel(
                name=self.bot.match_name,
                category=ctx.channel.category,
                position=0,
                overwrites=match_channel_permissions,
            )

            self.bot.signup_view = SignupView(ctx, self.bot)

            self.bot.current_signup_message = await self.bot.match_channel.send(
                embed=self.bot.signup_view.get_signup_embed(), view=self.bot.signup_view
            )

            await ctx.send(f"Queue started! Signup: <#{self.bot.match_channel.id}>")
        except Exception as e:
            # Cleanup
            self.bot.signup_active = False
            if hasattr(self.bot, "match_role") and self.bot.match_role:
                try:
                    await self.bot.match_role.delete()
                except:
                    pass
            if hasattr(self.bot, "match_channel") and self.bot.match_channel:
                try:
                    await self.bot.match_channel.delete()
                except:
                    pass
            await ctx.send(f"Error setting up queue: {str(e)}")


async def ensure_perms(ctx) -> bool:
    me = ctx.guild.me
    missing = []
    if not me.guild_permissions.manage_roles:
        missing.append("Manage Roles")
    if not me.guild_permissions.manage_channels:
        missing.append("Manage Channels")
    if missing:
        await ctx.send(
            f"I need the following permissions in this server: {', '.join(missing)}"
        )
        return False
    return True
