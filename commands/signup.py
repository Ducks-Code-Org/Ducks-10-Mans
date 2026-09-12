"Starts the signup process for a new match."

import random
import asyncio

import aiohttp
import discord
from discord.ext import commands

from commands import BotCommands
from database import mmr_collection, users
from riot_api import riot_account_exists_async
from views.signup_view import SignupView
from identity import ensure_current_riot_identity


def _remove_user_everywhere(doc) -> str:
    """Delete a user doc and its mmr doc. Returns the Riot ID string."""
    discord_id = str(doc.get("discord_id"))
    users.delete_one({"_id": doc["_id"]})
    mmr_collection.delete_one({"player_id": discord_id})
    name = (doc.get("name") or "").strip()
    tag = (doc.get("tag") or "").strip()
    riot_id = f"{name}#{tag}"
    print(f"[purge] Removed invalid Riot ID {riot_id} ({discord_id})")
    return riot_id


def _kick_from_queue(bot, discord_id: str) -> None:
    """Remove a purged player from any active signup queue, if present."""
    if not bot.signup_active:
        return
    if discord_id in [p["id"] for p in bot.queue]:
        bot.queue = [p for p in bot.queue if p["id"] != discord_id]
        view = getattr(bot, "signup_view", None)
        if view is not None:
            view.sign_up_button.label = f"Sign Up ({len(bot.queue)}/10)"
            print(f"[purge] Kicked purged player from queue ({discord_id})")
            # The signup message is refreshed by the periodic refresh task
            # and again after the background purge completes.


async def purge_invalid_riot_ids(bot=None) -> list[str]:
    """Remove linked Riot accounts that no longer exist on Riot's side.

    All account checks are issued in parallel against the API. Returns the
    display names of the removed players. Inconclusive checks (network/API
    errors) are skipped so flaky API responses never purge data.
    """
    docs = [
        doc
        for doc in users.find()
        if (doc.get("name") or "").strip() and (doc.get("tag") or "").strip()
    ]
    if not docs:
        return []

    removed: list[str] = []
    async with aiohttp.ClientSession() as session:
        # One shared semaphore caps concurrent API calls so we don't hit rate limits.
        sem = asyncio.Semaphore(5)

        async def check(doc):
            async with sem:
                return await riot_account_exists_async(
                    session, doc.get("name"), doc.get("tag")
                )

        results = await asyncio.gather(*(check(doc) for doc in docs))

    for doc, exists in zip(docs, results):
        # exists is False only on a confirmed 404; None (inconclusive) and
        # True (account exists) both keep the link.
        if exists is not False:
            continue
        removed.append(_remove_user_everywhere(doc))
        if bot is not None:
            _kick_from_queue(bot, str(doc.get("discord_id")))

    return removed


async def _run_background_purge(bot, ctx) -> None:
    """Background purge that refreshes the signup message afterwards."""
    try:
        removed = await purge_invalid_riot_ids(bot)
        if removed:
            try:
                await ctx.send(
                    "Removed "
                    + ", ".join(f"`{r}`" for r in removed)
                    + " from the database (Riot account no longer exists)."
                )
            except discord.HTTPException:
                pass
            view = getattr(bot, "signup_view", None)
            if view is not None and view.bot.current_signup_message:
                try:
                    await view.bot.current_signup_message.edit(
                        embed=view.get_signup_embed(), view=view
                    )
                except discord.HTTPException:
                    pass
    except asyncio.CancelledError:
        # A new signup or !cancel superseded this purge run; stop quietly.
        raise
    except Exception as e:
        print(f"[purge] Background purge failed: {e}")


def cancel_background_purge(bot) -> None:
    """Cancel any in-flight background Riot-ID purge task."""
    task = getattr(bot, "background_purge_task", None)
    if task is not None and not task.done():
        task.cancel()
    bot.background_purge_task = None


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

            # A stale purge from a previous signup may still be hogging the
            # rate-limit budget; drop it now that we know we're proceeding.
            cancel_background_purge(self.bot)

            ok, msg, _db_user = await ensure_current_riot_identity(ctx.author.id)
            if not ok:
                await ctx.send(msg)
                return

            self.bot.load_mmr_data()
            print("[DEBUG] Reloaded MMR data at start of signup")

            # Clear any existing signup view
            if self.bot.signup_view is not None:
                self.bot.signup_view = None

        # Fire off the invalid-Riot-ID purge in the background so the signup
        # isn't blocked by the (potentially slow) round of API checks. It
        # removes purged players from the queue as they're detected.
        self.bot.background_purge_task = asyncio.create_task(
            _run_background_purge(self.bot, ctx)
        )

        # Reset all match related states
        # Bump the setup generation so any stale views from a previous
        # match-setup cycle are invalidated.
        self.bot.setup_generation += 1
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
            if getattr(self.bot, "match_role", None):
                try:
                    await self.bot.match_role.delete()
                except discord.HTTPException:
                    pass
            if getattr(self.bot, "match_channel", None):
                try:
                    await self.bot.match_channel.delete()
                except discord.HTTPException:
                    pass
            await ctx.send(f"Error setting up queue: {e}")


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
