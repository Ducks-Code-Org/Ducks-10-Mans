import asyncio
import discord
from discord.ui import Button
import random

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

    async def setup(self):
        # Randomly select 3 maps from the TDM map pool
        from globals import tdm_maps
        random_maps = random.sample(tdm_maps, 3)
        self.chosen_maps = random_maps
        self.map_votes = {m: 0 for m in random_maps}

        for m in random_maps:
            btn = Button(label=f"{m} (0)", style=discord.ButtonStyle.secondary)
            
            async def make_callback(map_name):
                async def callback(interaction: discord.Interaction):
                    if str(interaction.user.id) not in [p["id"] for p in self.bot.tdm_queue]:
                        await interaction.response.send_message("Must be in queue!", ephemeral=True)
                        return
                    
                    if str(interaction.user.id) in self.voters:
                        await interaction.response.send_message("Already voted!", ephemeral=True)
                        return
                    
                    self.map_votes[map_name] += 1
                    self.voters.add(str(interaction.user.id))
                    
                    for b in self.map_buttons:
                        if b.label.startswith(map_name):
                            b.label = f"{map_name} ({self.map_votes[map_name]})"
                    
                    await interaction.message.edit(view=self)
                    await interaction.response.send_message(f"Voted {map_name}.", ephemeral=True)
                return callback

            btn.callback = await make_callback(m)
            self.map_buttons.append(btn)
            self.add_item(btn)

    async def send_vote_view(self):
        embed = discord.Embed(
            title="TDM Map Vote",
            description="Vote for the map you want to play!",
            color=discord.Color.blue()
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
            await self.ctx.send(f"There was a tie! Randomly selected: **{self.winning_map}**")
        else:
            self.winning_map = winning_maps[0]
            await self.ctx.send(f"The winning map is: **{self.winning_map}**")
        
        # Store the selected map
        self.bot.tdm_selected_map = self.winning_map
        
        # Update embed to show final votes
        final_embed = discord.Embed(
            title="Final Map Vote Results",
            description=f"Winner: **{self.winning_map}**",
            color=discord.Color.green()
        )
        for map_name in self.chosen_maps:
            final_embed.add_field(
                name=map_name,
                value=f"{self.map_votes[map_name]} votes" + 
                      (" 👑" if map_name == self.winning_map else ""),
                inline=True
            )
        
        await message.edit(embed=final_embed, view=None)
        
        # Proceed with team formation
        await self.ctx.send("Proceeding with team formation...")