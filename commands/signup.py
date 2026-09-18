"Starts the signup process for a new match."

import asyncio
import logging
import random

import aiohttp
import discord
from discord.ext import commands

from commands import BotCommands
from commands.report import cleanup_match_resources
from database import mmr_collection, users
from game.identity import ensure_current_riot_identity
from game.recent_queue import get_recent_queue, pingrecent_message
from services.riot_api import (
    get_account_by_puuid,
    riot_account_exists_async,
)
from views.signup_view import SignupView

log = logging.getLogger(__name__)

# Riot ID shown for players whose account vanished from Riot's side (account
# deleted, region migrated, or Riot data corruption). Their MMR stats are
# preserved — only the link is unset — and are restored as soon as they
# re-link with `!linkriot` (which matches on discord_id and keeps the mmr doc).
UNLINKED_NAME = "N/A"
UNLINKED_TAG = "N/A"


def _remove_user_everywhere(doc) -> str:
    """Delete a user doc and its mmr doc. Returns the Riot ID string."""
    discord_id = str(doc.get("discord_id"))
    users.delete_one({"_id": doc["_id"]})
    mmr_collection.delete_one({"player_id": discord_id})
    name = (doc.get("name") or "").strip()
    tag = (doc.get("tag") or "").strip()
    riot_id = f"{name}#{tag}"
    log.info("Removed invalid Riot ID %s (%s)", riot_id, discord_id)
    return riot_id


def _unlink_user(doc) -> str:
    """Unset a dead Riot link but keep the player's MMR stats.

    The user doc keeps its discord_id but name/tag/puuid are dropped, so the
    player shows as N/A on leaderboards and cannot rejoin the queue (signup
    requires a linked Riot ID) until they run `!linkriot Name#Tag` again.
    `!linkriot` updates the same users doc keyed by discord_id, so the
    historic mmr_data doc (keyed by player_id) is picked up automatically —
    nothing is deleted and no stats are lost.
    Returns the now-dead Riot ID string for the purge announcement.
    """
    discord_id = str(doc.get("discord_id"))
    name = (doc.get("name") or "").strip()
    tag = (doc.get("tag") or "").strip()
    riot_id = f"{name}#{tag}"
    users.update_one(
        {"_id": doc["_id"]},
        {
            "$unset": {"name": "", "tag": "", "puuid": ""},
            "$set": {"discord_id": discord_id},
        },
    )
    log.info(
        "Unlinked dead Riot ID %s (%s); MMR stats preserved until re-link",
        riot_id,
        discord_id,
    )
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
            log.info("Kicked purged player from queue (%s)", discord_id)
            # The signup message is refreshed by the periodic refresh task
            # and again after the background purge completes.


async def purge_invalid_riot_ids(bot=None) -> list[str]:
    """Unlink Riot accounts that no longer exist on Riot's side.

    All account checks are issued in parallel against the API. Returns the
    display names of the unlinked players. Inconclusive checks (network/API
    errors) are skipped so flaky API responses never purge data.

    A confirmed 404 on the stored Riot ID no longer deletes anything right
    away: the stored puuid is checked first, and when it still resolves the
    link is treated as a rename and refreshed (issue #182). Only an account
    that is gone entirely (no puuid or puuid also 404s) is unlinked: the
    Riot ID fields are cleared but the player's MMR stats are kept — they
    reappear (restored) as soon as they re-link with `!linkriot`.
    """
    docs = [
        doc
        for doc in users.find()
        if (doc.get("name") or "").strip() and (doc.get("tag") or "").strip()
    ]
    if not docs:
        return []

    log.info("Checking %s linked Riot ID(s) for validity", len(docs))
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
        # The stored Riot ID is gone — but that's what a rename looks like.
        # Resolve via puuid before destroying any stats (issue #182).
        puuid = (doc.get("puuid") or "").strip()
        if puuid:
            try:
                acc = await get_account_by_puuid(session, puuid)
            except Exception as e:
                # Network/API errors are inconclusive, never "account gone";
                # keep the link so a flaky response can't wipe stats.
                log.warning("PUUID resolve failed for %s during purge: %s", puuid, e)
                continue
            if acc and acc.get("gameName") and acc.get("tagLine"):
                new_name = acc["gameName"].lower().strip()
                new_tag = acc["tagLine"].lower().strip()
                users.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {"name": new_name, "tag": new_tag, "puuid": puuid}},
                )
                log.info(
                    "Riot ID renamed: %s#%s -> %s#%s (%s); stats preserved",
                    doc.get("name"),
                    doc.get("tag"),
                    new_name,
                    new_tag,
                    doc.get("discord_id"),
                )
                continue
        removed.append(_unlink_user(doc))
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
                    "Unlinked "
                    + ", ".join(f"`{r}`" for r in removed)
                    + " (Riot account no longer exists). Their stats are kept; "
                    "re-link with `!linkriot Name#Tag` to play again."
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
        log.error("Background purge failed: %s", e, exc_info=e)


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
    await bot.add_cog(PingRecentCommand(bot))


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
            log.debug("Reloaded MMR data at start of signup")

            # Tear down any existing signup view. Just dropping the reference
            # leaks its background tasks, which then race the new signup's
            # refresh task over current_signup_message — recreating stale
            # embeds/buttons or deleting them (issue #181).
            if self.bot.signup_view is not None:
                self.bot.signup_view.cleanup()
                self.bot.signup_view = None

            # Recover from a stuck previous match: if a report never
            # completed (e.g. the match was never visible on the Riot API and
            # the report claim blocked retrying), the old match channel/role
            # and per-match flags are still set. Cleaning them up here lets
            # the new signup start from a blank slate instead of piling a new
            # signup on top of the old match channel. The new signup's
            # generation bump (below) also invalidates any stale views.
            if (
                self.bot.match_channel is not None
                or self.bot.match_role is not None
                or self.bot.current_teams_message is not None
            ):
                log.warning(
                    "Stale match resources found at signup: channel=%r role=%r — cleaning up",
                    self.bot.match_channel,
                    self.bot.match_role,
                )
                await cleanup_match_resources(self.bot, cancelled=True)
                self.bot.current_teams_message = None

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
        log.info("Starting signup for %s", self.bot.match_name)

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
            log.error("Error setting up queue: %s", e, exc_info=e)
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


class PingRecentCommand(BotCommands):
    @commands.command(name="pingrecent")
    async def pingrecent(self, ctx):
        """Pings everyone who was in the most recently cancelled/finished queue."""
        recent_ids, cancelled = get_recent_queue()
        if not recent_ids:
            await ctx.send("No recent queue found to ping.")
            return
        log.info("%s pinged %s recent queue player(s)", ctx.author, len(recent_ids))
        await ctx.send(pingrecent_message(recent_ids, cancelled))
