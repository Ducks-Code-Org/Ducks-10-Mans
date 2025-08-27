import asyncio
import random

import discord
from discord.ui import Button

from views import safe_reply


class ModeVoteView(discord.ui.View):
    def __init__(self, ctx, bot):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.bot = bot
        self.balanced_button = Button(
            label="Balanced Teams (0)", style=discord.ButtonStyle.green
        )
        self.captains_button = Button(
            label="Captains (0)", style=discord.ButtonStyle.blurple
        )
        self.add_item(self.balanced_button)
        self.add_item(self.captains_button)

        self.is_handling_vote = False

        self.votes = {"Balanced": 0, "Captains": 0}
        self.voters = set()

        self.balanced_button.callback = self.balanced_callback
        self.captains_button.callback = self.captains_callback

        self.voting_phase_ended = False
        self.timeout = False

    def _disable_buttons(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

    async def balanced_callback(self, interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        if self.is_handling_vote:
            await safe_reply("Please wait a few seconds and try again!", ephemeral=True)
            return
        self.is_handling_vote = True

        if self.voting_phase_ended:
            await safe_reply(
                interaction, "This voting phase has already ended", ephemeral=True
            )
            self.is_handling_vote = False
            return

        # Must be queued
        if str(interaction.user.id) not in [str(p["id"]) for p in self.bot.queue]:
            await safe_reply(interaction, "Must be in queue!", ephemeral=True)
            self.is_handling_vote = False
            return

        # No double voting
        if str(interaction.user.id) in self.voters:
            await safe_reply(interaction, "Already voted!", ephemeral=True)
            self.is_handling_vote = False
            return

        # Count vote
        self.votes["Balanced"] += 1
        self.voters.add(str(interaction.user.id))
        print(f"[DEBUG] Updated vote count: {self.votes}")
        self.balanced_button.label = f"Balanced Teams ({self.votes['Balanced']})"

        await self.check_vote()
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException:
            pass

        await safe_reply(interaction, "Voted Balanced!", ephemeral=True)
        self.is_handling_vote = False

    async def captains_callback(self, interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        if self.is_handling_vote:
            await safe_reply("Please wait a few seconds and try again!", ephemeral=True)
            return
        self.is_handling_vote = True

        if self.voting_phase_ended:
            await safe_reply(
                interaction, "This voting phase has already ended", ephemeral=True
            )
            self.is_handling_vote = False
            return

        if str(interaction.user.id) not in [str(p["id"]) for p in self.bot.queue]:
            await safe_reply(interaction, "Must be in queue!", ephemeral=True)
            self.is_handling_vote = False
            return

        if str(interaction.user.id) in self.voters:
            await safe_reply(interaction, "Already voted!", ephemeral=True)
            self.is_handling_vote = False
            return

        self.votes["Captains"] += 1
        self.voters.add(str(interaction.user.id))
        print(f"[DEBUG] Updated vote count: {self.votes}")
        self.captains_button.label = f"Captains ({self.votes['Captains']})"

        await self.check_vote()
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException:
            pass

        self.is_handling_vote = False
        await safe_reply(interaction, "Voted Captains!", ephemeral=True)

    async def send_view(self):
        await self.ctx.send("Vote for mode (Balanced/Captains):", view=self)
        asyncio.create_task(self.start_timer())

    async def check_vote(self):
        print(f"[DEBUG] Checking votes. Current state: {self.votes}")
        print(f"[DEBUG] Voting phase ended: {self.voting_phase_ended}")
        print(f"[DEBUG] Timeout status: {self.timeout}")

        # if self.voting_phase_ended:
        # return

        if self.timeout:
            if self.votes["Balanced"] > self.votes["Captains"]:
                print("[DEBUG] Balanced wins on timeout")
                self.bot.chosen_mode = "Balanced"
                await self.ctx.send("Balanced Teams chosen!")
                self.voting_phase_ended = True
                self._disable_buttons()
                try:
                    await self.ctx.channel.send("Voting closed.")
                except discord.HTTPException:
                    pass
                await self._setup_balanced_teams()
            elif self.votes["Captains"] > self.votes["Balanced"]:
                print("[DEBUG] Captains wins on timeout")
                self.bot.chosen_mode = "Captains"
                await self.ctx.send(
                    "Captains chosen! Captains will be set after map is chosen."
                )
                self.voting_phase_ended = True
                self._disable_buttons()
                try:
                    await self.ctx.channel.send("Voting closed.")
                except discord.HTTPException:
                    pass
            else:
                # Handle tie
                decision = "Balanced" if random.choice([True, False]) else "Captains"
                self.bot.chosen_mode = decision
                await self.ctx.send(f"Tie! {decision} wins by coin flip!")
                if decision == "Balanced":
                    await self._setup_balanced_teams()
            return

        if self.votes["Balanced"] > 4:
            print("[DEBUG] Setting mode to Balanced (majority)")
            self.bot.chosen_mode = "Balanced"
            self.voting_phase_ended = True
            self._disable_buttons()
            try:
                await self.ctx.channel.send("Voting closed.")
            except discord.HTTPException:
                pass
            await self.ctx.send("Balanced Teams chosen!")
            await self._setup_balanced_teams()
            print(f"[DEBUG] Mode after setting: {self.bot.chosen_mode}")
            return
        elif self.votes["Captains"] > 4:
            print("[DEBUG] Setting mode to Captains (majority)")
            self.bot.chosen_mode = "Captains"
            self.voting_phase_ended = True
            self._disable_buttons()
            try:
                await self.ctx.channel.send("Voting closed.")
            except discord.HTTPException:
                pass
            await self.ctx.send(
                "Captains chosen! Captains will be set after map is chosen."
            )
            print(f"[DEBUG] Mode after setting: {self.bot.chosen_mode}")
            return

        if self.timeout and not self.voting_phase_ended:
            balanced_votes = self.votes["Balanced"]
            captains_votes = self.votes["Captains"]

            if balanced_votes > captains_votes:
                print("[DEBUG] Balanced wins on timeout")
                self.bot.chosen_mode = "Balanced"
                self.voting_phase_ended = True
                self._disable_buttons()
                try:
                    await self.ctx.channel.send("Voting closed.")
                except discord.HTTPException:
                    pass
                await self.ctx.send(
                    f"Time's up! Balanced Teams wins with {balanced_votes} votes vs {captains_votes} votes!"
                )
                await self._setup_balanced_teams()
            elif captains_votes > balanced_votes:
                print("[DEBUG] Captains wins on timeout")
                self.bot.chosen_mode = "Captains"
                self.voting_phase_ended = True
                self._disable_buttons()
                try:
                    await self.ctx.channel.send("Voting closed.")  # optional
                except discord.HTTPException:
                    pass
                await self.ctx.send(
                    f"Time's up! Captains wins with {captains_votes} votes vs {balanced_votes} votes!"
                )
            else:
                # Handle tie
                decision = "Balanced" if random.choice([True, False]) else "Captains"
                self.bot.chosen_mode = decision
                self.voting_phase_ended = True
                self._disable_buttons()
                try:
                    await self.ctx.channel.send("Voting closed.")  # optional
                except discord.HTTPException:
                    pass
                await self.ctx.send(
                    f"Time's up! Votes tied {balanced_votes}-{balanced_votes}. {decision} wins by coin flip!"
                )
                if decision == "Balanced":
                    await self._setup_balanced_teams()

            print(f"[DEBUG] Final mode selection: {self.bot.chosen_mode}")
            return

    async def _setup_balanced_teams(self):
        players = self.bot.queue[:]

        def mmr_of(p):
            pid = str(p["id"])
            return self.bot.player_mmr.get(pid, {}).get("mmr", 1000)

        players.sort(key=lambda p: mmr_of(p), reverse=True)

        team1, team2 = [], []
        t1_mmr = 0
        t2_mmr = 0
        for player in players:
            if t1_mmr <= t2_mmr:
                team1.append(player)
                t1_mmr += mmr_of(player)
            else:
                team2.append(player)
                t2_mmr += mmr_of(player)
        self.bot.team1 = team1
        self.bot.team2 = team2

    async def start_timer(self):
        await asyncio.sleep(25)
        if not self.voting_phase_ended:
            self.timeout = True
            await self.check_vote()

        # After mode chosen, do map type vote
        from views.map_type_vote_view import MapTypeVoteView

        map_type_vote = MapTypeVoteView(self.ctx, self.bot)
        await map_type_vote.send_view()
