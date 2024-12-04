import discord
from discord.ui import Button
from globals import official_maps, all_maps
import asyncio
from views.map_vote_view import MapVoteView

class MapTypeVoteView(discord.ui.View):
    def __init__(self, ctx, bot):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.bot = bot
        self.competitive_button = Button(label="Competitive Maps (0)", style=discord.ButtonStyle.green)
        self.all_maps_button = Button(label="All Maps (0)", style=discord.ButtonStyle.blurple)

        self.add_item(self.competitive_button)
        self.add_item(self.all_maps_button)
        self.map_pool_votes = {"Competitive Maps": 0, "All Maps": 0}
        self.voters = set()

        # Link callbacks to buttons
        self.setup_callbacks()

    async def competitive_callback(self, interaction: discord.Interaction):
        # make user is in the queue and hasn't voted yet
        if interaction.user.id not in [player["id"] for player in self.bot.queue]:
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
        if interaction.user.id not in [player["id"] for player in self.bot.queue]:
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

    async def send_view(self):
        await self.ctx.send("Vote for the map pool:", view=self)
        await asyncio.sleep(25)

        if self.map_pool_votes["Competitive Maps"] >= self.map_pool_votes["All Maps"]:


            await self.ctx.send("Competitive Maps selected!")
            map_vote = MapVoteView(self.ctx, self.bot, official_maps)
            await map_vote.setup()

            # Begin vote for specific map
            await map_vote.send_view()
        else:
            await self.ctx.send("All Maps selected!")
            map_vote = MapVoteView(self.ctx, self.bot, all_maps)
            await map_vote.setup()

            # Begin vote for specific map
            await map_vote.send_view()

    def setup_callbacks(self):
        self.competitive_button.callback = self.competitive_callback
        self.all_maps_button.callback = self.all_maps_callback

    # refresh the signup message every minute
