import discord
from discord.ui import Button, View
from database import users
from globals import player_mmr, player_names
from voting import start_voting
import asyncio
import random

class ModeVoteView(discord.ui.View):
    def __init__(self, ctx, queue):
        super().__init__()
        self.ctx = ctx
        self.queue = queue  # Queue passed from signup view
        self.balanced_button = Button(label="Balanced Teams (0)", style=discord.ButtonStyle.green)
        self.captains_button = Button(label="Captains (0)", style=discord.ButtonStyle.blurple)
        self.add_item(self.balanced_button)
        self.add_item(self.captains_button)
        self.balanced_button.callback = self.balanced_callback
        self.captains_button.callback = self.captains_callback

        self.votes = {"Balanced Teams": 0, "Captains": 0}
        self.voters = set()
        self.dummy = False

        # Link callbacks to buttons
        self.setup_callbacks()



    async def balanced_callback(self, interaction: discord.Interaction):
        # make sure the user is in the queue
        if interaction.user.id not in [player["id"] for player in self.queue]:
            await interaction.response.send_message("You must be in the queue to vote!", ephemeral=True)
            return
        # make sure the user has not already voted
        if interaction.user.id in self.voters:
            await interaction.response.send_message("You have already voted!", ephemeral=True)
            return

        self.votes["Balanced Teams"] += 1
        self.voters.add(interaction.user.id)
        self.balanced_button.label = f"Balanced Teams ({self.votes['Balanced Teams']})"
        await interaction.message.edit(view=self)
        await interaction.response.send_message(
            f"Voted for Balanced Teams! Current votes: {self.votes['Balanced Teams']}",
            ephemeral=True,
        )

    async def captains_callback(self, interaction: discord.Interaction):
        if interaction.user.id not in [player["id"] for player in self.queue]:
            await interaction.response.send_message("You must be in the queue to vote!", ephemeral=True)
            return
        if interaction.user.id in self.voters:
            await interaction.response.send_message("You have already voted!", ephemeral=True)
            return

        self.votes["Captains"] += 1
        self.voters.add(interaction.user.id)
        self.captains_button.label = f"Captains ({self.votes['Captains']})"
        await interaction.message.edit(view=self)
        await interaction.response.send_message(
            f"Voted for Captains! Current votes: {self.votes['Captains']}",
            ephemeral=True,
        )

    async def start_voting(self):
        await self.ctx.send("Vote for how teams will be decided:", view=self)

        await asyncio.sleep(25)

        # Determine voting results
        if self.dummy == True:
            await self.ctx.send("Balanced Teams wins the vote!")
            await balanced_teams_logic(channel)
        else:
            if self.votes["Balanced Teams"] > self.votes["Captains"]:
                await self.ctx.send("Balanced Teams wins the vote!")
                await balanced_teams_logic(channel)
            elif votes["Captains"] > votes["Balanced Teams"]:
                await self.ctx.send("Captains wins the vote!")
                await captains_mode(channel)
            else:
                decision = "Balanced Teams" if random.choice([True, False]) else "Captains"
                await self.ctx.send(f"It's a tie! Flipping a coin... {decision} wins!")
                if decision == "Balanced Teams":
                    await balanced_teams_logic(channel)
                else:
                    await captains_mode(channel)
        match_ongoing = True
        dummy = False

    def balanced_teams(self, players):
        players.sort(key=lambda p: player_mmr[p["id"]]["mmr"], reverse=True)
        match_ongoing = True
        team1, team2 = [], []
        team1_mmr, team2_mmr = 0, 0

        for player in players:
            if team1_mmr <= team2_mmr:
                team1.append(player)
                team1_mmr += player_mmr[player["id"]]["mmr"]
            else:
                team2.append(player)
                team2_mmr += player_mmr[player["id"]]["mmr"]

        return team1, team2

    async def balanced_teams_logic(self):
        team1, team2 = self.balanced_teams(self.queue)
        await self.ctx.send(
            f"**Balanced Teams:**\nAttackers: {', '.join([p['name'] for p in team1])}\nDefenders: {', '.join([p['name'] for p in team2])}")

        self.bot.match_ongoing = True

        # vote for maps next
        await vote_map(ctx)

    def setup_callbacks(self):
        self.balanced_button.callback = self.balanced_callback
        self.captains_button.callback = self.captains_callback

    # refresh the signup message every minute
