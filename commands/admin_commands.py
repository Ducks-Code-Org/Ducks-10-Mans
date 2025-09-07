"Admin commands for managing the bot and server."

import discord
from discord.ext import commands

from commands import BotCommands
from database import mmr_collection
from views.signup_view import SignupView
from views.mode_vote_view import ModeVoteView
from views.captains_drafting_view import CaptainsDraftingView


async def setup(bot):
    await bot.add_cog(AdminCommands(bot))


class AdminCommands(BotCommands):
    @commands.command(name="newseason")
    @commands.has_permissions(administrator=True)
    async def new_season(self, ctx, *, no_reset: str = None):
        """
        Creates a new season that ends exactly 2 calendar months from now (UTC).
        By default, resets everyone’s MMR + stats. If you pass 'noreset', it will keep stats.
        Usage: !newseason    (resets)
            !newseason noreset
        """
        reset = True
        if no_reset and no_reset.lower() in {"noreset", "keep", "false", "0"}:
            reset = False

        doc = self.bot.create_new_season(reset_player_stats=reset)

        start_cst = doc["started_at_cst"]
        end_cst = doc["ends_at_cst"]

        start_str = start_cst.strftime("%Y-%m-%d %I:%M %p %Z")
        end_str = end_cst.strftime("%Y-%m-%d %I:%M %p %Z")

        await ctx.send(
            f"**Season {doc['season_number']}** created.\n"
            f"Starts (Central): `{start_str}`\n"
            f"Ends (Central):   `{end_str}`\n"
            f"{'All player MMR + stats were reset.' if reset else 'Player stats were preserved (no reset).'}"
        )

    @commands.command()
    @commands.has_role("Owner")
    async def initialize_rounds(self, ctx):
        result = mmr_collection.update_many({}, {"$set": {"total_rounds_played": 0}})
        await ctx.send(
            f"Initialized total_rounds_played for {result.modified_count} players."
        )

    @commands.command()
    async def simulate_queue(self, ctx):
        if self.bot.signup_view is None:
            self.bot.signup_view = SignupView(ctx, self.bot)
        if self.bot.signup_active:
            await ctx.send(
                "A signup is already in progress. Resetting queue for simulation."
            )
            self.bot.queue.clear()

        # Add 10 dummy players to the queue
        queue = [{"id": i, "name": f"Player{i}"} for i in range(1, 11)]

        # Assign default MMR to the dummy players and map IDs to names
        for player in queue:
            if player["id"] not in self.bot.player_mmr:
                self.bot.player_mmr[player["id"]] = {
                    "mmr": 1000,
                    "wins": 0,
                    "losses": 0,
                }
            self.bot.player_names[player["id"]] = player["name"]

        self.bot.save_mmr_data()

        self.bot.signup_active = True
        await ctx.send(
            f"Simulated full queue: {', '.join([player['name'] for player in queue])}"
        )

        await ctx.send("The queue is now full, proceeding to the voting stage.")

        mode_vote = ModeVoteView(ctx, self.bot)
        await mode_vote.send_view()

    # Set the bot to development mode
    @commands.command()
    @commands.has_role("blood")
    async def toggledev(self, ctx):
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

    # Stop the signup process
    @commands.command()
    @commands.has_role("Owner")
    async def cancel(self, ctx):
        if not self.bot.signup_active:
            await ctx.send("No signup is active to cancel")
            return

        if self.bot.signup_view:
            self.bot.signup_view.cleanup()
            self.bot.signup_view = None

        self.bot.queue = []
        self.bot.current_signup_message = None
        self.bot.signup_active = False

        await ctx.send("Canceled Signup")
        print("Cancelling signup...")

        try:
            await self.bot.match_channel.delete()
            await self.bot.match_role.delete()
        except discord.NotFound:
            pass

    @commands.command()
    @commands.has_role("Owner")
    async def force_draft(self, ctx):
        bot_queue = [
            {"name": "Player3", "id": 1},
            {"name": "Player4", "id": 2},
            {"name": "Player5", "id": 3},
            {"name": "Player6", "id": 4},
            {"name": "Player7", "id": 5},
            {"name": "Player8", "id": 6},
            {"name": "Player9", "id": 7},
            {"name": "Player10", "id": 8},
        ]
        for bot in bot_queue:
            self.bot.queue.append(bot)
        draft = CaptainsDraftingView(ctx, self.bot, True)
        await draft.send_current_draft_view()
