import discord
from discord.ui import Button, View
from database import users
from globals import player_mmr, player_names
from voting import start_voting
import asyncio
import random


class MapTypeVoteView(discord.ui.View):
    def __init__(self, ctx, bot, queue):
        super().__init__()
        self.ctx = ctx
        self.bot = bot
        self.queue = queue  # Queue passed from signup view
        self.competitive_button = Button(label="Competitive Maps (0)", style=discord.ButtonStyle.green)
        self.all_maps_button = Button(label="All Maps (0)", style=discord.ButtonStyle.blurple)

        self.add_item(self.competitive_button)
        self.add_item(self.all_maps_button)
        self.competitive_button.callback = self.balanced_callback
        self.all_maps_button.callback = self.captains_callback

        self.map_pool_votes = {"Competitive Maps": 0, "All Maps": 0}
        self.voters = set()

        # Link callbacks to buttons
        self.setup_callbacks()

    async def competitive_callback(self, interaction: discord.Interaction):
        # make user is in the queue and hasn't voted yet
        if interaction.user.id not in [player["id"] for player in self.queue]:
            await interaction.response.send_message("You must be in the queue to vote!", ephemeral=True)
            return
        if interaction.user.id in self.voters:
            await interaction.response.send_message("You have already voted!", ephemeral=True)
            return
        self.map_pool_votes["Competitive Maps"] += 1
        self.voters.add(interaction.user.id)
        self.competitive_button.label = f"Competitive Maps ({self.map_pool_votes['Competitive Maps']})"
        await interaction.message.edit(view=self)
        await interaction.response.send_message("You voted for Competitive Maps.", ephemeral=True)

    async def all_maps_callback(self, interaction: discord.Interaction):
        # make sure the user is in the queue and hasn't voted yet
        if interaction.user.id not in [player["id"] for player in self.queue]:
            await interaction.response.send_message("You must be in the queue to vote!", ephemeral=True)
            return
        if interaction.user.id in self.voters:
            await interaction.response.send_message("You have already voted!", ephemeral=True)
            return
        self.map_pool_votes["All Maps"] += 1
        self.voters.add(interaction.user.id)
        self.all_maps_button.label = f"All Maps ({self.map_pool_votes['All Maps']})"
        await interaction.message.edit(view=self)
        await interaction.response.send_message("You voted for All Maps.", ephemeral=True)

    async def start_voting_map_type(self):
        

    def setup_callbacks(self):
        self.competitive_button.callback = self.balanced_callback
        self.all_maps_button.callback = self.captains_callback

    # refresh the signup message every minute
