import asyncio
import random

import discord
from discord.ui import Button
from maps_service import get_tdm_maps
from views import safe_reply


class MapButton(discord.ui.Button):
    def __init__(self, map_name, parent_view):
        super().__init__(label=f"{map_name} (0)", style=discord.ButtonStyle.secondary)
        self.map_name = map_name
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        queue_ids = [str(p["id"]) for p in self.parent_view.tdm_queue]

        if user_id not in queue_ids:
            await interaction.response.send_message(
                "Must be in queue to vote!", ephemeral=True
            )
            return

        if str(interaction.user.id) in self.parent_view.voters:
            await interaction.response.send_message("Already voted!", ephemeral=True)
            return

        self.parent_view.map_votes[self.map_name] += 1
        self.parent_view.voters.add(str(interaction.user.id))
        self.label = f"{self.map_name} ({self.parent_view.map_votes[self.map_name]})"

        await interaction.message.edit(view=self.parent_view)
        await interaction.response.send_message(
            f"Voted {self.map_name}.", ephemeral=True
        )


class TDMMapVoteView(discord.ui.View):
    def __init__(self, ctx, bot):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.bot = bot
        self.map_buttons = []
        self.map_votes = {}
        self.chosen_maps = []
        self.winning_map = ""
        self.voters = set()
        self.is_handling_vote = False
        # Store direct reference to tdm_queue
        self.tdm_queue = bot.tdm_queue
        print(f"[DEBUG] Queue at init: {self.tdm_queue}")

    async def setup(self):
        TDM_MAP_LIST = get_tdm_maps()

        random_maps = random.sample(TDM_MAP_LIST, 3)
        self.chosen_maps = random_maps
        self.map_votes = {m: 0 for m in random_maps}

        for m in random_maps:
            btn = Button(label=f"{m} (0)", style=discord.ButtonStyle.secondary)

            async def make_callback(map_name=m):  # Note the default argument
                async def callback(interaction: discord.Interaction):
                    if not interaction.response.is_done():
                        await interaction.response.defer(ephemeral=True)

                    if self.is_handling_vote:
                        await safe_reply(
                            "Please wait a few seconds and try again!", ephemeral=True
                        )
                        return
                    self.is_handling_vote = True

                    # Get current queue IDs at time of vote
                    queue_ids = [str(p["id"]) for p in self.bot.tdm_queue]
                    user_id = str(interaction.user.id)

                    print(f"[DEBUG] User attempting vote: {user_id}")
                    print(f"[DEBUG] Current queue IDs: {queue_ids}")

                    if user_id not in queue_ids:
                        await safe_reply(
                            "You must be in queue to vote!", ephemeral=True
                        )
                        self.is_handling_vote = False
                        return

                    if user_id in self.voters:
                        await safe_reply("Already voted!", ephemeral=True)
                        self.is_handling_vote = False
                        return

                    self.map_votes[map_name] += 1
                    self.voters.add(user_id)

                    for b in self.map_buttons:
                        if b.label.startswith(map_name):
                            b.label = f"{map_name} ({self.map_votes[map_name]})"

                    await interaction.message.edit(view=self)
                    await interaction.response.send_message(
                        f"Voted for {map_name}!", ephemeral=True
                    )
                    self.is_handling_vote = False

                return callback

            btn.callback = await make_callback()
            self.map_buttons.append(btn)
            self.add_item(btn)

    async def send_vote_view(self):
        embed = discord.Embed(
            title="TDM Map Vote",
            description="Vote for the map you want to play!",
            color=discord.Color.blue(),
        )
        for map_name in self.chosen_maps:
            embed.add_field(name=map_name, value="0 votes", inline=True)

        message = await self.ctx.send(embed=embed, view=self)

        # Wait 25 seconds for voting
        await asyncio.sleep(25)

        # Determine winning map
        max_votes = max(self.map_votes.values())
        winning_maps = [m for m, v in self.map_votes.items() if v == max_votes]

        if len(winning_maps) > 1:
            # If there's a tie, randomly select one
            self.winning_map = random.choice(winning_maps)
            await self.ctx.send(
                f"There was a tie! Randomly selected: **{self.winning_map}**"
            )
        else:
            self.winning_map = winning_maps[0]
            await self.ctx.send(f"The winning map is: **{self.winning_map}**")

        # Store the selected map
        self.bot.tdm_selected_map = self.winning_map

        # Update embed to show final votes
        final_embed = discord.Embed(
            title="Final Map Vote Results",
            description=f"Winner: **{self.winning_map}**",
            color=discord.Color.green(),
        )

        for map_name in self.chosen_maps:
            final_embed.add_field(
                name=map_name,
                value=f"{self.map_votes[map_name]} votes"
                + (" 👑" if map_name == self.winning_map else ""),
                inline=True,
            )

        await message.edit(embed=final_embed, view=None)
        await self.ctx.send("Proceeding with team formation...")

        tdm_cog = self.bot.get_cog("TDMCommands")
        if tdm_cog:
            await tdm_cog.make_tdm_teams(self.ctx)
        else:
            print("[DEBUG] Couldn't access TDM commands")
