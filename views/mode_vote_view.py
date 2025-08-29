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

        self.interaction_request_queue = []  # List of (interaction, mode, future)
        self.interaction_queue_task = asyncio.create_task(
            self.process_interaction_queue()
        )

        self.votes = {"Balanced": 0, "Captains": 0}
        self.voters = set()

        self.balanced_button.callback = self.balanced_callback
        self.captains_button.callback = self.captains_callback

        self.voting_phase_ended = False
        self.timeout = False

    async def process_interaction_queue(self):
        while True:
            if not self.interaction_request_queue:
                await asyncio.sleep(0.1)
                continue
            interaction, mode, fut = self.interaction_request_queue.pop(0)
            try:
                await self._handle_vote(interaction, mode)
            finally:
                if not fut.done():
                    fut.set_result(None)

    def _disable_buttons(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

    async def _handle_vote(self, interaction: discord.Interaction, mode: str):
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

        self.votes[mode] += 1
        self.voters.add(str(interaction.user.id))
        print(f"[DEBUG] Updated vote count: {self.votes}")
        if mode == "Balanced":
            self.balanced_button.label = f"Balanced Teams ({self.votes['Balanced']})"
        else:
            self.captains_button.label = f"Captains ({self.votes['Captains']})"

        await self.check_vote()
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException:
            pass

        await safe_reply(interaction, f"Voted {mode}!", ephemeral=True)

    async def balanced_callback(self, interaction: discord.Interaction):
        if not interaction.response.is_done():
            try:
                await interaction.response.defer(ephemeral=True)
            except Exception as e:
                if isinstance(e, discord.errors.NotFound):
                    # Interaction expired, do not queue
                    return
                else:
                    raise

        # Add the interaction to the interaction queue and wait for processing
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        self.interaction_request_queue.append((interaction, "Balanced", fut))
        await fut  # Wait until this request is processed

    async def captains_callback(self, interaction: discord.Interaction):
        if not interaction.response.is_done():
            try:
                await interaction.response.defer(ephemeral=True)
            except Exception as e:
                if isinstance(e, discord.errors.NotFound):
                    # Interaction expired, do not queue
                    return
                else:
                    raise

        # Add the interaction to the interaction queue and wait for processing
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        self.interaction_request_queue.append((interaction, "Captains", fut))
        await fut  # Wait until this request is processed

    async def send_view(self):
        await self.ctx.send("Vote for mode (Balanced/Captains):", view=self)
        asyncio.create_task(self.start_timer())

    async def check_vote(self):
        print(f"[DEBUG] Checking votes. Current state: {self.votes}")
        print(f"[DEBUG] Voting phase ended: {self.voting_phase_ended}")
        print(f"[DEBUG] Timeout status: {self.timeout}")

        def announce_and_close(msg):
            async def inner():
                self.voting_phase_ended = True
                self._disable_buttons()
                if (
                    hasattr(self, "interaction_queue_task")
                    and self.interaction_queue_task
                ):
                    self.interaction_queue_task.cancel()
                    self.interaction_queue_task = None
                self.interaction_request_queue.clear()
                try:
                    await self.ctx.channel.send("Voting closed.")
                except discord.HTTPException:
                    pass
                await self.ctx.send(msg)

            return inner

        def handle_balanced():
            self.bot.chosen_mode = "Balanced"
            return announce_and_close("Balanced Teams chosen!")

        def handle_captains():
            self.bot.chosen_mode = "Captains"
            return announce_and_close(
                "Captains chosen! Captains will be set after map is chosen."
            )

        def handle_tie(msg_prefix="Tie!"):
            decision = "Balanced" if random.choice([True, False]) else "Captains"
            self.bot.chosen_mode = decision

            async def tie_inner():
                self.voting_phase_ended = True
                self._disable_buttons()
                try:
                    await self.ctx.channel.send("Voting closed.")
                except discord.HTTPException:
                    pass
                await self.ctx.send(f"{msg_prefix} {decision} wins by coin flip!")
                if decision == "Balanced":
                    await self._setup_balanced_teams()

            return tie_inner

        # Majority wins
        if self.votes["Balanced"] > 4:
            print("[DEBUG] Setting mode to Balanced (majority)")
            await handle_balanced()()
            await self._setup_balanced_teams()
            print(f"[DEBUG] Mode after setting: {self.bot.chosen_mode}")
            return
        elif self.votes["Captains"] > 4:
            print("[DEBUG] Setting mode to Captains (majority)")
            await handle_captains()()
            print(f"[DEBUG] Mode after setting: {self.bot.chosen_mode}")
            return

        # Timeout logic
        if self.timeout:
            balanced_votes = self.votes["Balanced"]
            captains_votes = self.votes["Captains"]
            if balanced_votes > captains_votes:
                print("[DEBUG] Balanced wins on timeout")
                await handle_balanced()()
                await self._setup_balanced_teams()
            elif captains_votes > balanced_votes:
                print("[DEBUG] Captains wins on timeout")
                await handle_captains()()
            else:
                print("[DEBUG] Tie on timeout")
                await handle_tie(
                    msg_prefix=f"Time's up! Votes tied {balanced_votes}-{captains_votes}."
                )()
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
