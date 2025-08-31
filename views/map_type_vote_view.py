import asyncio
import random
from functools import partial

import discord
from discord.ui import Button

from maps_service import get_competitive_maps, get_standard_maps
from views import safe_reply
from views.map_vote_view import MapVoteView


class MapTypeVoteView(discord.ui.View):
    def __init__(self, ctx, bot):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.bot = bot

        # Setup Interaction Buttons
        self.competitive_button = Button(
            label="Competitive Maps (0)", style=discord.ButtonStyle.green
        )
        self.all_maps_button = Button(
            label="All Maps (0)", style=discord.ButtonStyle.blurple
        )
        self.add_item(self.competitive_button)
        self.add_item(self.all_maps_button)
        self.competitive_button.callback = partial(
            self.vote_callback, mode="Competitive"
        )
        self.all_maps_button.callback = partial(self.vote_callback, mode="All")

        # Setup Task Runners
        self.interaction_request_queue = (
            asyncio.Queue()
        )  # Interaction Queue of (interaction, mode, future)
        self.interaction_queue_task = asyncio.create_task(
            self.process_interaction_queue()
        )
        self.timeout_timer_task = asyncio.create_task(self.timeout_timer())

        # Setup State
        self.map_pool_votes = {"Competitive": 0, "All": 0}
        self.voters = set()
        self.voting_phase_ended = False
        self.timeout = False
        self.view_message = None

        print("Starting new map type vote...")

    async def send_view(self):
        self.view_message = await self.ctx.send("Vote for the map pool:", view=self)

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
                await self.handle_map_type_vote(interaction, mode)
            finally:
                # Ensure the waiting coroutine is notified, even if an error occurs
                if not fut.done():
                    fut.set_result(None)

    def cancel_interaction_queue_task(self):
        if self.interaction_queue_task:
            self.interaction_queue_task.cancel()
            self.interaction_queue_task = None

    async def handle_map_type_vote(
        self, interaction: discord.Interaction, map_type: str
    ):
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

        # Update the vote count
        self.map_pool_votes[map_type] += 1
        self.voters.add(str(interaction.user.id))

        # Update the button labels and message
        if map_type == "Competitive":
            self.competitive_button.label = (
                f"Competitive Maps ({self.map_pool_votes['Competitive']})"
            )
        else:
            self.all_maps_button.label = f"All Maps ({self.map_pool_votes['All']})"
        await interaction.message.edit(view=self)

        # Reply and check for vote finish
        print(f"Recorded new vote. Current state: {self.map_pool_votes}")
        await safe_reply(interaction, f"Voted {map_type} Maps!", ephemeral=True)
        await self.check_for_winner()

    async def check_for_winner(self):
        competitive_votes = self.map_pool_votes["Competitive"]
        all_votes = self.map_pool_votes["All"]

        # Check for majority winner
        if competitive_votes > 5:
            self.voting_phase_ended = True
            await self.ctx.send("Competitive Maps wins by majority!")
            chosen_map_type = "Competitive"
            await self.close_vote(chosen_map_type)
            return
        elif all_votes > 5:
            self.voting_phase_ended = True
            await self.ctx.send("All Maps wins by majority!")
            chosen_map_type = "All"
            await self.close_vote(chosen_map_type)
            return

        # Check for timeout winner
        if self.timeout:
            self.voting_phase_ended = True
            if competitive_votes > all_votes:
                await self.ctx.send("Competitive Maps wins by timeout!")
                chosen_map_type = "Competitive"
                await self.close_vote(chosen_map_type)
            elif all_votes > competitive_votes:
                await self.ctx.send("All Maps wins by timeout!")
                chosen_map_type = "All"
                await self.close_vote(chosen_map_type)
            else:
                decision = "Competitive" if random.choice([True, False]) else "All"
                await self.ctx.send(f"Tie! {decision} wins by coin flip!")
                chosen_map_type = decision
                await self.close_vote(chosen_map_type)
            return

    async def close_vote(self, chosen_map_type):
        if self.timeout:
            print("Map type vote ended by timeout.")
        else:
            print("Map type vote ended by majority.")
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        await self.view_message.edit(view=self)

        if chosen_map_type == "Competitive":
            map_list: list[str] = get_competitive_maps()
        else:
            map_list: list[str] = get_standard_maps()

        map_vote = MapVoteView(self.ctx, self.bot, map_list)
        await map_vote.setup()
        await map_vote.send_view()
        self.stop()
        self.cancel_interaction_queue_task()
        self.cancel_timeout_timer()

    async def timeout_timer(self):
        await asyncio.sleep(25)
        if not self.voting_phase_ended:
            self.timeout = True
            await self.check_for_winner()

    def cancel_timeout_timer(self):
        if self.timeout_timer_task:
            self.timeout_timer_task.cancel()
            self.timeout_timer_task = None
