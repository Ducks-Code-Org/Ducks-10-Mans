import asyncio
import random

import discord
from discord.ui import Button

from database import users
from views.captains_drafting_view import SecondCaptainChoiceView
from views import safe_reply


class MapVoteView(discord.ui.View):
    def __init__(self, ctx, bot, map_choices):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.bot = bot

        # Setup Task Runners
        self.interaction_request_queue = (
            asyncio.Queue()
        )  # Interaction Queue of (interaction, map, future)
        self.interaction_queue_task = asyncio.create_task(
            self.process_interaction_queue()
        )
        self.timeout_timer_task = asyncio.create_task(self.timeout_timer())

        # Setup State
        self.map_choices = map_choices
        self.map_buttons = []
        self.map_votes = {}
        self.chosen_maps = []
        self.winning_map = ""
        self.voters = set()
        self.view_message = None
        self.voting_phase_ended = False
        self.vote_lock = asyncio.Lock()

        print("Starting new map vote...")

    async def setup(self):
        # Select 3 random maps from the given pool
        self.chosen_maps = random.sample(self.map_choices, 3)
        self.map_votes = {map: 0 for map in self.chosen_maps}
        for map in self.chosen_maps:
            # Dynamically setup buttons and callbacks for each map
            async def vote_callback(interaction: discord.Interaction, map=map):
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
                await self.interaction_request_queue.put((interaction, map, fut))
                await fut  # Wait until this request is processed

            button = Button(label=f"{map} (0)", style=discord.ButtonStyle.secondary)
            button.callback = vote_callback
            self.map_buttons.append(button)

    async def send_view(self):
        if not self.bot.chosen_mode:
            print("No mode selected at start of map vote.")
            await self.ctx.send(
                "Error: Game mode not selected. Please start a new queue."
            )
            return

        for button in self.map_buttons:
            self.add_item(button)

        self.view_message = await self.ctx.send("Vote for the map to play:", view=self)

    async def process_interaction_queue(self):
        while True:
            # Wait for the next interaction request (blocks until available)
            interaction, map, fut = await self.interaction_request_queue.get()
            try:
                # Process the interaction for this interaction
                await self.handle_map_vote(interaction, map)
            finally:
                # Ensure the waiting coroutine is notified, even if an error occurs
                if not fut.done():
                    fut.set_result(None)

    def cancel_interaction_queue_task(self):
        if self.interaction_queue_task:
            self.interaction_queue_task.cancel()
            self.interaction_queue_task = None

    async def handle_map_vote(self, interaction: discord.Interaction, map):
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
        self.map_votes[map] += 1
        self.voters.add(str(interaction.user.id))

        # Update button labels
        for button in self.map_buttons:
            if button.label.startswith(map):
                button.label = f"{map} ({self.map_votes[map]})"
        await interaction.message.edit(view=self)

        # Reply and check for vote finish
        print(f"Recorded new vote. Current state: {self.map_votes}")
        await safe_reply(interaction, f"Voted {map}.", ephemeral=True)
        await self.check_for_winner()

    async def check_for_winner(self):
        async with self.vote_lock:
            if self.voting_phase_ended:
                return
            # Check for majority winner
            highest_number_of_votes = max(self.map_votes.values())
            if highest_number_of_votes > 5:
                self.voting_phase_ended = True
                # Find the winning map
                winning_map: str = next(
                    (
                        map_name
                        for map_name, num_votes in self.map_votes.items()
                        if num_votes == highest_number_of_votes
                    ),
                    None,
                )
                message = f"{winning_map} wins by majority!"
                await self.ctx.send(message)
                print(message)
                await self.close_vote(winning_map)
                return

            # Check for timeout winner
            if self.timeout:
                self.voting_phase_ended = True
                # Collect all maps that have the highest number of votes (handles ties)
                winners = []
                for map_name, vote_count in self.map_votes.items():
                    if vote_count == highest_number_of_votes:
                        winners.append(map_name)
                winning_map = random.choice(winners)
                if len(winners) > 1:
                    message = f"Tie! Randomly selected: **{winning_map}**"
                    await self.ctx.send(message)
                    print(message)
                else:
                    message = f"{winning_map} wins by timeout!"
                    await self.ctx.send(message)
                    print(message)
                await self.close_vote(winning_map)
                return

    async def close_vote(self, winning_map: str):
        self.winning_map = winning_map
        self.bot.selected_map = winning_map
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        await self.view_message.edit(view=self)

        # Finalize match setup
        if self.bot.chosen_mode == "Balanced":
            await self.finalize_match_setup()
        elif self.bot.chosen_mode == "Captains":
            # Set Captains
            if not self.bot.captain1 or not self.bot.captain2:
                if not self.assign_captains():
                    await self.ctx.send(
                        "Error: Not enough players to assign captains. Please start a new queue."
                    )
                    self.stop()
                    self.cancel_interaction_queue_task()
                    self.cancel_timeout_timer()
                    return

            choice_view = SecondCaptainChoiceView(self.ctx, self.bot)
            await self.ctx.send(
                f"<@{self.bot.captain2['id']}>, choose draft type:", view=choice_view
            )
        else:
            await self.ctx.send("Error: No game mode selected!")

        self.stop()
        self.cancel_interaction_queue_task()
        self.cancel_timeout_timer()

    def assign_captains(self) -> bool:
        # Assign 2 captains randomly from the top 5 MMR players, with a decreasing bias for lower MMR
        sorted_players = sorted(
            self.bot.queue,
            key=lambda p: self.bot.player_mmr.get(str(p["id"]), {}).get("mmr", 1000),
            reverse=True,
        )

        if len(sorted_players) < 2:
            print(
                "Not enough players in the queue to assign captains. Stopping queue..."
            )
            return False
        if len(sorted_players) < 5:
            print("Warning: Less than 5 players detected in queue. Continuing...")
            self.bot.captain1 = sorted_players[0]
            self.bot.captain2 = sorted_players[1]
            return True

        top_five_players = sorted_players[:5]
        # Weights: [5, 4, 3, 2, 1] for top 5 players. Highest MMR gets highest weight (5), lowest gets lowest (1).
        # This means the top MMR player is 5x more likely to be chosen than the 5th.
        weights = [5 - i for i in range(5)]
        captain1, captain2 = random.choices(top_five_players, weights=weights, k=2)
        if captain1 == captain2:
            # If duplicate, pick second captain from remaining
            remaining_players = [p for p in top_five_players if p != captain1]
            remaining_weights = [
                weights[i] for i, p in enumerate(top_five_players) if p != captain1
            ]
            captain2 = (
                random.choices(remaining_players, weights=remaining_weights, k=1)[0]
                if remaining_players
                else captain1
            )
        self.bot.captain1 = captain1
        self.bot.captain2 = captain2
        return True

    async def finalize_match_setup(self):
        # Finalize teams after map chosen
        teams_embed = discord.Embed(
            title=f"Teams on {self.winning_map}",
            description="Good luck!",
            color=discord.Color.blue(),
        )

        attackers = []
        for p in self.bot.team1:
            ud = users.find_one({"discord_id": str(p["id"])})
            mmr = self.bot.player_mmr.get(str(p["id"]), {}).get("mmr", 1000)
            if ud:
                rn = ud.get("name", "Unknown")
                rt = ud.get("tag", "Unknown")
                attackers.append(f"{rn}#{rt} (MMR:{mmr})")
            else:
                attackers.append(f"{p['name']} (MMR:{mmr})")

        defenders = []
        for p in self.bot.team2:
            ud = users.find_one({"discord_id": str(p["id"])})
            mmr = self.bot.player_mmr.get(str(p["id"]), {}).get("mmr", 1000)
            if ud:
                rn = ud.get("name", "Unknown")
                rt = ud.get("tag", "Unknown")
                defenders.append(f"{rn}#{rt} (MMR:{mmr})")
            else:
                defenders.append(f"{p['name']} (MMR:{mmr})")

        teams_embed.add_field(
            name="**Attackers:**", value="\n".join(attackers), inline=False
        )
        teams_embed.add_field(
            name="**Defenders:**", value="\n".join(defenders), inline=False
        )

        await self.ctx.send(embed=teams_embed)
        await self.ctx.send("Start match, then `!report` to finalize results.")

        self.bot.match_ongoing = True
        self.bot.match_not_reported = True
        await self.bot.match_channel.edit(name=f"{self.bot.match_name}《in-game》")

    async def timeout_timer(self):
        await asyncio.sleep(25)
        if not self.voting_phase_ended:
            self.timeout = True
            await self.check_for_winner()

    def cancel_timeout_timer(self):
        if self.timeout_timer_task:
            self.timeout_timer_task.cancel()
            self.timeout_timer_task = None
