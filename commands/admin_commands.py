"Admin commands for managing the bot and server."

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from commands import BotCommands
from commands.maintenance_commands import resolve_user_arg
from commands.report import cleanup_match_resources
from commands.signup import cancel_background_purge
from database import mmr_collection
from game.duck_coins import (
    announce_cancellation_async,
    refund_match_coins,
)
from game.ranks import remove_all_rank_roles
from game.recent_queue import remember_recent_queue
from game.stats_helper import DEFAULT_MMR
from views.mode_vote_view import ModeVoteView
from views.signup_view import SignupView

log = logging.getLogger(__name__)


async def setup(bot):
    # Shared with commands/report.py: whichever cog loads first creates it.
    if not hasattr(bot, "report_lock"):
        bot.report_lock = asyncio.Lock()
    await bot.add_cog(AdminCommands(bot))


class AdminCommands(BotCommands):
    @commands.hybrid_command(
        name="newseason",
        description="Start a new season and crown the SSR winner",
    )
    @commands.has_role("Owner")
    @app_commands.describe(no_reset="Pass 'noreset' to keep everyone's stats")
    async def new_season(self, ctx, *, no_reset: str | None = None):
        """
        Creates a new season, saving seasons stats, and assigning SSR rank.
        By default, resets everyone's MMR + stats. Pass 'noreset' to keep stats.
        """
        reset = True
        if no_reset and no_reset.lower() in {"noreset", "keep", "false", "0"}:
            reset = False

        # Determine winner info
        winner_doc = mmr_collection.find_one(
            {"matches_played": {"$gt": 0}}, sort=[("mmr", -1)]
        )
        if winner_doc is None:
            await ctx.send(
                "No player has played a match yet; there is no winner to crown.",
                ephemeral=True,
            )
            return

        doc = self.bot.create_new_season(reset_player_stats=reset, winner=winner_doc)
        log.info(
            "New season %s created (reset=%s, winner=%s)",
            doc["season_number"],
            reset,
            winner_doc.get("player_id"),
        )

        # Strip every rank role — fresh season means fresh ranks.
        try:
            await remove_all_rank_roles(ctx.guild)
        except Exception as e:
            log.warning("Could not remove rank roles: %s", e)

        # Assign SSR Rank to winner
        ssr_role = await ctx.guild.create_role(
            name=f"Season {doc['season_number'] - 1} SSR", hoist=True
        )
        await ctx.guild.edit_role_positions(positions={ssr_role: 5})
        await ssr_role.edit(color=discord.Color.teal())
        winner_member = ctx.guild.get_member(int(winner_doc["player_id"]))
        if winner_member:
            await winner_member.add_roles(ssr_role)
        else:
            log.warning(
                "Winner %s is not in this guild; SSR role created but not assigned.",
                winner_doc.get("player_id"),
            )

        # Try to send to 'announcements' channel if it exists
        announcement_channel = None
        if ctx.guild:
            for channel in ctx.guild.text_channels:
                if channel.name.lower() == "announcements":
                    announcement_channel = channel
                    break
        message = (
            f"**<@&1311935865626431529> Season {doc['season_number']}** started.\n"
            f"<@{winner_doc['player_id']}> has been awarded the {ssr_role.mention} role!\n"
            f"{'All player MMR + stats were reset.' if reset else 'Player stats were preserved (no reset).'}\n"
            f"{'Duck Coins were reset for the new season.' if reset else 'Duck Coins were preserved (no reset).'}"
        )
        if announcement_channel:
            await announcement_channel.send(message)
        else:
            await ctx.send(message)

    @commands.hybrid_command(
        name="initialize_rounds",
        description="Zero every player's total rounds played",
    )
    @commands.has_role("Owner")
    async def initialize_rounds(self, ctx):
        result = mmr_collection.update_many({}, {"$set": {"total_rounds_played": 0}})
        log.info(
            "%s reset total_rounds_played for %s players",
            ctx.author,
            result.modified_count,
        )
        await ctx.send(
            f"Initialized total_rounds_played for {result.modified_count} players."
        )

    @commands.hybrid_command(
        name="simulate_queue",
        description="Fill the queue with 10 fake players for testing",
    )
    @commands.has_role("Owner")
    async def simulate_queue(self, ctx):
        log.info("Simulated queue started by %s", ctx.author)
        # Start a new setup cycle: invalidate any stale views first.
        self.bot.setup_generation += 1

        # Clean up any previous signup view and start a fresh one
        if self.bot.signup_view is not None:
            self.bot.signup_view.cleanup()
            self.bot.signup_view = None
        self.bot.signup_view = SignupView(ctx, self.bot)

        if self.bot.signup_active:
            await ctx.send(
                "A signup is already in progress. Resetting queue for simulation.",
                ephemeral=True,
            )
        self.bot.queue.clear()

        # Add 10 dummy players to the queue
        queue = [{"id": i, "name": f"Player{i}"} for i in range(1, 11)]

        # Assign default MMR to the dummy players and map IDs to names.
        # Kept in memory only: never persisted, so simulation can't pollute
        # the real MMR database with fake players.
        for player in queue:
            if player["id"] not in self.bot.player_mmr:
                self.bot.player_mmr[player["id"]] = {
                    "mmr": DEFAULT_MMR,
                    "wins": 0,
                    "losses": 0,
                }
            self.bot.player_names[player["id"]] = player["name"]

        self.bot.signup_active = True
        await ctx.send(
            f"Simulated full queue: {', '.join([player['name'] for player in queue])}"
        )

        await ctx.send("The queue is now full! Proceeding with match setup...")

        mode_vote = ModeVoteView(ctx, self.bot, self.bot.setup_generation)
        await mode_vote.send_view()

    @commands.hybrid_command(
        name="setcaptain",
        description="Manually set a draft captain (Captains mode, before map vote ends)",
    )
    @commands.has_permissions(administrator=True)
    @app_commands.describe(
        slot="Which captain slot to set: 1 or 2",
        target="Player to make captain (@mention or linked Name#Tag)",
    )
    async def setcaptain(self, ctx, slot: str, target: str):
        """Manually set a draft captain (Captains mode, before map vote ends).

        Valid between the mode vote picking Captains and the map vote ending:
        assign_captains() (map vote end) skips auto-assignment only when both
        slots are pre-filled; once selected_map exists the draft is running and
        captains are locked in.
        """
        slot = (slot or "").strip()
        target = (target or "").strip()
        if slot not in {"1", "2"} or not target:
            await ctx.send(
                "Usage: `/setcaptain <1|2> <@user|Name#Tag>`", ephemeral=True
            )
            return
        if self.bot.match_ongoing or self.bot.match_not_reported:
            await ctx.send(
                "A match is already ongoing or awaiting report — captains are locked in.",
                ephemeral=True,
            )
            return
        if self.bot.chosen_mode != "Captains":
            await ctx.send(
                "Captains can only be set once the mode vote has picked Captains.",
                ephemeral=True,
            )
            return
        if self.bot.selected_map:
            await ctx.send(
                "The map vote already ended — the captain draft has started, "
                "so captains can no longer be changed.",
                ephemeral=True,
            )
            return
        if not self.bot.queue:
            await ctx.send("There is no queue to set captains from.", ephemeral=True)
            return

        pid = resolve_user_arg(target, ctx.guild)
        if not pid:
            await ctx.send(
                f"Could not resolve player `{target}` — use an @mention or a linked `Name#Tag`.",
                ephemeral=True,
            )
            return
        player = next((p for p in self.bot.queue if str(p["id"]) == str(pid)), None)
        if player is None:
            await ctx.send("That player is not in the current queue.", ephemeral=True)
            return

        attr = "captain1" if slot == "1" else "captain2"
        other = self.bot.captain2 if slot == "1" else self.bot.captain1
        if other and str(other["id"]) == str(pid):
            await ctx.send(
                f"**{player['name']}** is already the other captain.", ephemeral=True
            )
            return

        previous = getattr(self.bot, attr)
        setattr(self.bot, attr, player)

        mention = f" (<@{pid}>)" if pid.isdigit() else ""
        replaced = f" (replacing {previous['name']})" if previous else ""
        warn = (
            " — set the other slot too, otherwise both captains are re-randomized"
            " when the map vote ends."
            if other is None
            else ""
        )
        log.info("%s set %s to %s%s", ctx.author, attr, player["name"], replaced)
        await ctx.send(
            f"Captain {slot} set to **{player['name']}**{mention}{replaced}{warn}"
        )

    # Set the bot to development mode
    @commands.hybrid_command(
        name="toggledev",
        description="Toggle developer mode (admins only, hides bot activity)",
    )
    @commands.has_permissions(administrator=True)
    async def toggledev(self, ctx):
        log.info(
            "Developer mode %s by %s",
            "disabled" if self.dev_mode else "enabled",
            ctx.author,
        )
        if not self.dev_mode:
            self.dev_mode = True
            await ctx.send("Developer Mode Enabled (commands are now admin-only)")
            try:
                await self.bot.change_presence(
                    status=discord.Status.do_not_disturb,
                    activity=discord.Game(name="Bot Maintenance"),
                )
            except discord.HTTPException:
                pass
        else:
            self.dev_mode = False
            await ctx.send("Developer Mode Disabled")
            try:
                await self.bot.change_presence(
                    status=discord.Status.online, activity=discord.Game(name="10 Mans!")
                )
            except discord.HTTPException:
                pass

    # Stop the signup process or cancel an active match
    @commands.hybrid_command(
        name="cancel",
        description="Cancel the active signup, setup, or match and refund coins",
    )
    @commands.has_permissions(administrator=True)
    async def cancel(self, ctx):
        # Serialize against !report: a cancel must not tear down the match
        # channel or refund coins while a report is mid-commit (and must not
        # refund a doubledown/map override for a match that was played).
        async with self.bot.report_lock:
            await self._cancel_locked(ctx)

    async def _cancel_locked(self, ctx):
        # Stop any in-flight background Riot-ID purge so it stops consuming
        # the rate-limit budget and can't delay a follow-up !signup.
        cancel_background_purge(self.bot)

        # Handle an active signup (queue phase before the queue is full)
        if self.bot.signup_active:
            # Invalidate in-flight setup views first so lingering vote/draft
            # tasks see the cancellation and bail out instead of resurrecting
            # match setup.
            self.bot.setup_generation += 1
            self.bot.match_setup_generation = None

            if self.bot.signup_view:
                self.bot.signup_view.cleanup()
                self.bot.signup_view = None

            if self.bot.queue:
                remember_recent_queue(self.bot.queue, cancelled=True)
            self.bot.current_signup_message = None
            self.bot.signup_active = False
            self.bot.match_not_reported = False
            self.bot.match_ongoing = False
            self.bot.chosen_mode = None
            self.bot.selected_map = None
            self.bot.captain1 = None
            self.bot.captain2 = None
            self.bot.team1 = []
            self.bot.team2 = []
            self.bot.queue.clear()

            refunded = refund_match_coins(self.bot)
            if refunded:
                await announce_cancellation_async(self.bot, ctx.guild)

            await ctx.send(
                "Canceled active signup. Feel free to start a new one with `/signup`."
            )
            log.info("Cancelling signup...")

            await cleanup_match_resources(self.bot, cancelled=True)
        # Handle a match that is already in progress
        elif self.bot.match_ongoing or self.bot.selected_map:
            self.bot.setup_generation += 1
            self.bot.match_setup_generation = None

            self.bot.match_not_reported = False
            self.bot.match_ongoing = False
            self.bot.chosen_mode = None
            self.bot.selected_map = None
            self.bot.captain1 = None
            self.bot.captain2 = None
            self.bot.team1 = []
            self.bot.team2 = []
            refunded = refund_match_coins(self.bot)
            if refunded:
                await announce_cancellation_async(self.bot, ctx.guild)
            await ctx.send(
                "Cancelled active match. Feel free to start a new one with `/signup`."
            )
            await cleanup_match_resources(self.bot, cancelled=True)
            log.info("Cancelling active match...")
        # Handle a signup whose queue already filled (match setup phase:
        # team-mode vote, map-pool vote, map vote, or captains draft)
        elif self.bot.match_channel:
            self.bot.setup_generation += 1
            self.bot.match_setup_generation = None

            self.bot.match_not_reported = False
            self.bot.match_ongoing = False
            self.bot.chosen_mode = None
            self.bot.selected_map = None
            self.bot.captain1 = None
            self.bot.captain2 = None
            self.bot.team1 = []
            self.bot.team2 = []
            refunded = refund_match_coins(self.bot)
            if refunded:
                await announce_cancellation_async(self.bot, ctx.guild)
            await ctx.send(
                "Cancelled match setup. Feel free to start a new one with `/signup`."
            )
            await cleanup_match_resources(self.bot, cancelled=True)
            log.info("Cancelling match setup...")
        else:
            await ctx.send("No active signup or match to cancel.")
