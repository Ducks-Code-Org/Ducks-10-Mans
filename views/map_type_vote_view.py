import asyncio
import discord
from discord.ui import Button
import random

from globals import official_maps, all_maps
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

        self.map_pool_votes={"Competitive":0,"All":0}
        self.voters=set()

        self.competitive_button.callback=self.comp_callback
        self.all_maps_button.callback=self.all_callback

    async def comp_callback(self, interaction: discord.Interaction):
        if str(interaction.user.id) not in [p["id"] for p in self.bot.queue]:
            await interaction.response.send_message("Must be in queue!", ephemeral=True)
            return
        if str(interaction.user.id) in self.voters:
            await interaction.response.send_message("Already voted!", ephemeral=True)
            return
        self.map_pool_votes["Competitive"]+=1
        self.voters.add(str(interaction.user.id))
        self.competitive_button.label=f"Competitive Maps ({self.map_pool_votes['Competitive']})"
        await interaction.message.edit(view=self)
        await interaction.response.send_message("Voted Competitive Maps!", ephemeral=True)

    async def all_callback(self, interaction: discord.Interaction):
        if str(interaction.user.id) not in [p["id"] for p in self.bot.queue]:
            await interaction.response.send_message("Must be in queue!", ephemeral=True)
            return
        if str(interaction.user.id) in self.voters:
            await interaction.response.send_message("Already voted!", ephemeral=True)
            return
        self.map_pool_votes["All"]+=1
        self.voters.add(str(interaction.user.id))
        self.all_maps_button.label=f"All Maps ({self.map_pool_votes['All']})"
        await interaction.message.edit(view=self)
        await interaction.response.send_message("Voted All Maps!", ephemeral=True)

    async def send_view(self):
        await self.ctx.send("Vote for the map pool:", view=self)
        await asyncio.sleep(25)
        if self.map_pool_votes["Competitive"]>self.map_pool_votes["All"]:
            await self.ctx.send("Competitive Maps chosen!")
            chosen_maps=official_maps
        elif self.map_pool_votes["All"]>self.map_pool_votes["Competitive"]:
            await self.ctx.send("All Maps chosen!")
            chosen_maps=all_maps
        else:
            decision="All" if random.choice([True,False]) else "Competitive"
            await self.ctx.send(f"Tie! {decision} Maps chosen!")
            chosen_maps= all_maps if decision=="All" else official_maps

        map_vote=MapVoteView(self.ctx,self.bot,chosen_maps)
        await map_vote.setup()
        await map_vote.send_view()