import discord
from discord.ui import Button
from database import users
import asyncio



class MapVoteView(discord.ui.View):
    def __init__(self, ctx, bot, map_choices):
        super().__init__()
        self.ctx = ctx
        self.bot = bot
        self.map_choices = map_choices  # map choices passed from map type vote view
        self.map_buttons = []

        self.map_votes = {map_name: 0 for map_name in map_choices}
        self.voters = set()

    async def setup(self):
        await self.setup_map_buttons()

    async def setup_map_buttons(self):
        for map_name in self.map_choices:
            button = Button(label=f"{map_name} (0)", style=discord.ButtonStyle.secondary)

            async def map_callback(interaction: discord.Interaction, map_name=map_name):
                if interaction.user.id not in [player["id"] for player in self.bot.queue]:
                    await interaction.response.send_message("You must be in the queue to vote!", ephemeral=True)
                    return
                if interaction.user.id in self.voters:
                    await interaction.response.send_message("You have already voted!", ephemeral=True)
                    return
                self.map_votes[map_name] += 1
                self.voters.add(interaction.user.id)
                # Update the button label
                for btn in self.map_buttons:
                    if btn.label.startswith(map_name):
                        btn.label = f"{map_name} ({self.map_votes[map_name]})"
                await interaction.message.edit(view=self)
                await interaction.response.send_message(f"You voted for {map_name}.", ephemeral=True)

            button.callback = map_callback
            self.map_buttons.append(button)

    async def send_view(self):
        for button in self.map_buttons:
            self.add_item(button)
        await self.ctx.send("Vote for the map to play:", view=self)

        await asyncio.sleep(25)

        winning_map = max(self.map_votes, key=self.map_votes.get)
        selected_map_name = winning_map
        await self.ctx.send(f"The selected map is **{winning_map}**!")
        self.bot.selected_map = winning_map
        teams_embed = discord.Embed(
            title=f"Teams for the match on {winning_map}",
            description="Good luck to both teams!",
            color=discord.Color.blue()
        )

        attackers = []
        for player in self.bot.team1:
            user_data = users.find_one({"discord_id": str(player["id"])})
            mmr = self.bot.player_mmr.get(player["id"], {}).get("mmr", 1000)
            if user_data:
                riot_name = user_data.get("name", "Unknown")
                riot_tag = user_data.get("tag", "Unknown")
                attackers.append(f"{riot_name}#{riot_tag} (MMR: {mmr})")
            else:
                attackers.append(f"{player['name']} (MMR: {mmr})")

        defenders = []
        for player in self.bot.team2:
            user_data = users.find_one({"discord_id": str(player["id"])})
            mmr = self.bot.player_mmr.get(player["id"], {}).get("mmr", 1000)
            if user_data:
                riot_name = user_data.get("name", "Unknown")
                riot_tag = user_data.get("tag", "Unknown")
                defenders.append(f"{riot_name}#{riot_tag} (MMR: {mmr})")
            else:
                defenders.append(f"{player['name']} (MMR: {mmr})")

        teams_embed.add_field(
            name="**Attackers:**",
            value='\n'.join(attackers),
            inline=False
        )
        teams_embed.add_field(
            name="**Defenders:**",
            value='\n'.join(defenders),
            inline=False
        )

        # Send the finalized teams again
        await self.ctx.send(embed=teams_embed)

        await self.ctx.send("Start the match, then use !report to finalize results")
