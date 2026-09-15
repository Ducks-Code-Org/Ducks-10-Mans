"Admin commands for managing the bot and server."

import logging

import discord
from discord.ext import commands

from commands import BotCommands
from commands.report import cleanup_match_resources
from commands.signup import cancel_background_purge
from database import mmr_collection
from duck_coins import refund_open_bets
from ranks import remove_all_rank_roles
from recent_queue import remember_recent_queue
from stats_helper import DEFAULT_MMR
from views.mode_vote_view import ModeVoteView
from views.signup_view import SignupView

log = logging.getLogger(__name__)


async def setup(bot):
    await bot.add_cog(AdminCommands(bot))


class AdminCommands(BotCommands):
    @commands.command(name="newseason")
    @commands.has_permissions(administrator=True)
    async def new_season(self, ctx, *, no_reset: str | None = None):
        """
        Creates a new season, saving seasons stats, and assigning SSR rank.
        By default, resets everyone’s MMR + stats. If you pass 'noreset', it will keep stats.
        Usage: !newseason    (resets)
            !newseason noreset
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
                "No player has played a match yet; there is no winner to crown."
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
            f"<@{winner_doc['player_id']}> has been awarded the **Season {doc['season_number'] - 1} SSR** role!\n"
            f"{'All player MMR + stats were reset.' if reset else 'Player stats were preserved (no reset).'}\n"
            f"{'Duck Coins were reset for the new season.' if reset else 'Duck Coins were preserved (no reset).'}"
        )
        if announcement_channel:
            await announcement_channel.send(message)
        else:
            await ctx.send(message)

    @commands.command()
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

    @commands.command()
    @commands.has_permissions(administrator=True)
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
                "A signup is already in progress. Resetting queue for simulation."
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

    # Set the bot to development mode
    @commands.command()
    @commands.has_role("blood")
    async def toggledev(self, ctx):
        log.info(
            "Developer mode %s by %s",
            "disabled" if self.dev_mode else "enabled",
            ctx.author,
        )
        if not self.dev_mode:
            self.dev_mode = True
            await ctx.send("Developer Mode Enabled")
            self.bot.command_prefix = "^"
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
            self.bot.command_prefix = "!"
            try:
                await self.bot.change_presence(
                    status=discord.Status.online, activity=discord.Game(name="10 Mans!")
                )
            except discord.HTTPException:
                pass

    # Stop the signup process or cancel an active match
    @commands.command()
    @commands.has_role("Owner")
    async def cancel(self, ctx):
        # Stop any in-flight background Riot-ID purge so it stops consuming
        # the rate-limit budget and can't delay a follow-up !signup.
        cancel_background_purge(self.bot)

        # Handle an active signup (queue phase before the queue is full)
        if self.bot.signup_active:
            # Invalidate in-flight setup views first so lingering vote/draft
            # tasks see the cancellation and bail out instead of resurrecting
            # match setup.
            self.bot.setup_generation += 1

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

            refund_open_bets(self.bot)
            self.bot.double_downs = set()
            self.bot.map_override_last = 0
            self.bot.map_override_last_by = None

            await ctx.send(
                "Canceled active signup. Feel free to start a new one with `!signup`."
            )
            log.info("Cancelling signup...")

            await cleanup_match_resources(self.bot, cancelled=True)
        # Handle a match that is already in progress
        elif self.bot.match_ongoing or self.bot.selected_map:
            self.bot.setup_generation += 1

            self.bot.match_not_reported = False
            self.bot.match_ongoing = False
            self.bot.chosen_mode = None
            self.bot.selected_map = None
            self.bot.captain1 = None
            self.bot.captain2 = None
            self.bot.team1 = []
            self.bot.team2 = []
            refund_open_bets(self.bot)
            self.bot.double_downs = set()
            self.bot.map_override_last = 0
            self.bot.map_override_last_by = None
            await ctx.send(
                "Cancelled active match. Feel free to start a new one with `!signup`."
            )
            await cleanup_match_resources(self.bot, cancelled=True)
            log.info("Cancelling active match...")
        # Handle a signup whose queue already filled (match setup phase:
        # team-mode vote, map-pool vote, map vote, or captains draft)
        elif self.bot.match_channel:
            self.bot.setup_generation += 1

            self.bot.match_not_reported = False
            self.bot.match_ongoing = False
            self.bot.chosen_mode = None
            self.bot.selected_map = None
            self.bot.captain1 = None
            self.bot.captain2 = None
            self.bot.team1 = []
            self.bot.team2 = []
            refund_open_bets(self.bot)
            self.bot.double_downs = set()
            self.bot.map_override_last = 0
            self.bot.map_override_last_by = None
            await ctx.send(
                "Cancelled match setup. Feel free to start a new one with `!signup`."
            )
            await cleanup_match_resources(self.bot, cancelled=True)
            log.info("Cancelling match setup...")
        else:
            await ctx.send("No active signup or match to cancel.")
