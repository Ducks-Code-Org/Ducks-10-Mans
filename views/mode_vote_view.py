import asyncio
import random
from functools import partial

import discord
from discord.ui import Button

from views import safe_reply
from views.map_type_vote_view import MapTypeVoteView


class ModeVoteView(discord.ui.View):
    def __init__(self, ctx, bot):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.bot = bot

        # Setup Interaction Buttons
        self.balanced_button = Button(
            label="Balanced Teams (0)", style=discord.ButtonStyle.green
        )
        self.captains_button = Button(
            label="Captains (0)", style=discord.ButtonStyle.blurple
        )
        self.add_item(self.balanced_button)
        self.add_item(self.captains_button)
        self.balanced_button.callback = partial(self.vote_callback, mode="Balanced")
        self.captains_button.callback = partial(self.vote_callback, mode="Captains")

        # Start Task Runners
        self.interaction_request_queue = (
            asyncio.Queue()
        )  # Interaction Queue of (interaction, future)
        self.interaction_queue_task = asyncio.create_task(
            self.process_interaction_queue()
        )
        self.timeout_timer_task = asyncio.create_task(self.timeout_timer())

        # Setup State
        self.votes = {"Balanced": 0, "Captains": 0}
        self.voters = set()
        self.voting_phase_ended = False
        self.timeout = False

        print("Starting new mode vote...")

    async def send_view(self):
        await self.ctx.send("Vote how teams should be chosen:", view=self)

    async def vote_callback(self, interaction: discord.Interaction, mode: str):
        # Defer the interaction if not already done, to allow time for processing
        if not interaction.response.is_done():
            try:
                await interaction.response.defer(ephemeral=True)
            except discord.errors.NotFound:
                # Interaction expired, do not queue
                return

        # Add the interaction to the interaction queue and wait for processing
        loop: asyncio.AbstractEventLoop = asyncio.get_event_loop()
        fut: asyncio.Future[None] = loop.create_future()
        await self.interaction_request_queue.put((interaction, mode, fut))
        await fut  # Wait until this request is processed

    async def process_interaction_queue(self):
        while True:
            # Wait for the next interaction request (blocks until available)
            interaction, mode, fut = await self.interaction_request_queue.get()
            try:
                # Process the interaction for this interaction
                await self.handle_mode_vote(interaction, mode)
            finally:
                # Ensure the waiting coroutine is notified, even if an error occurs
                if not fut.done():
                    fut.set_result(None)

    def cancel_interaction_queue_task(self):
        if self.interaction_queue_task:
            self.interaction_queue_task.cancel()
            self.interaction_queue_task = None

    async def handle_mode_vote(self, interaction: discord.Interaction, mode: str):
        # Ensure vote is valid
        if self.voting_phase_ended:
            await safe_reply(
                interaction, "This voting phase has already ended", ephemeral=True
            )
            return
        if str(interaction.user.id) not in [str(p["id"]) for p in self.bot.queue]:
            await safe_reply(interaction, "Must be in queue!", ephemeral=True)
            return
        if str(interaction.user.id) in self.voters:
            await safe_reply(interaction, "Already voted!", ephemeral=True)
            return

        # Update the mode vote count
        self.votes[mode] += 1
        self.voters.add(str(interaction.user.id))

        # Update the button labels and message
        if mode == "Balanced":
            self.balanced_button.label = f"Balanced Teams ({self.votes['Balanced']})"
        else:
            self.captains_button.label = f"Captains ({self.votes['Captains']})"
        await interaction.message.edit(view=self)

        # Reply and check for vote finish
        print(f"Recorded new vote. Current state: {self.votes}")
        await safe_reply(interaction, f"Voted {mode}!", ephemeral=True)
        await self.check_for_winner()

    async def check_for_winner(self):
        async def close_vote():
            if self.timeout:
                print(
                    f"Mode vote ended by timeout. Setting bot mode to: {self.bot.chosen_mode}"
                )
            else:
                print(
                    f"Mode vote ended by majority. Setting bot mode to: {self.bot.chosen_mode}"
                )
            self.voting_phase_ended = True
            self.cancel_interaction_queue_task()
            self.cancel_timeout_timer()
            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    child.disabled = True
            map_type_vote = MapTypeVoteView(self.ctx, self.bot)
            await map_type_vote.send_view()

        # Check for a majority winner
        if self.votes["Balanced"] > 1:
            await self.ctx.send("Balanced wins by majority!")
            self.bot.chosen_mode = "Balanced"
            self.setup_balanced_teams()
            await close_vote()
            return
        elif self.votes["Captains"] > 1:
            await self.ctx.send("Captains wins by majority!")
            self.bot.chosen_mode = "Captains"
            await close_vote()
            return

        # Check for a winner by timeout
        if self.timeout:
            balanced_votes = self.votes["Balanced"]
            captains_votes = self.votes["Captains"]
            if balanced_votes > captains_votes:
                await self.ctx.send("Balanced wins by timeout!")
                self.bot.chosen_mode = "Balanced"
                self.setup_balanced_teams()
                await close_vote()
            elif captains_votes > balanced_votes:
                await self.ctx.send("Captains wins by timeout!")
                self.bot.chosen_mode = "Captains"
                await close_vote()
            else:
                decision = "Balanced" if random.choice([True, False]) else "Captains"
                await self.ctx.send(f"Tie! {decision} wins by coin flip!")
                self.bot.chosen_mode = decision
                await close_vote()
            return

    def setup_balanced_teams(self):
        print("Generating balanced teams...")

        # Sort players by MMR (highest to lowest)
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

    async def timeout_timer(self):
        await asyncio.sleep(25)
        if not self.voting_phase_ended:
            self.timeout = True
            await self.check_for_winner()

    def cancel_timeout_timer(self):
        if self.timeout_timer_task:
            self.timeout_timer_task.cancel()
            self.timeout_timer_task = None
