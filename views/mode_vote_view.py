"""This view allows users to interactively vote on the team draft mode."""

import asyncio
import random

import discord
from discord.ui import Button

from views.map_type_vote_view import MapTypeVoteView
from views.captains_drafting_view import CaptainsDraftingView


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

        self.votes = {"Balanced Teams": 0, "Captains": 0}
        self.voters = set()
        self.dummy = False

        # Link callbacks to buttons
        self.setup_callbacks()

    async def balanced_callback(self, interaction: discord.Interaction):
        # make sure the user is in the queue
        if interaction.user.id not in [player["id"] for player in self.bot.queue]:
            await interaction.response.send_message(
                "You must be in the queue to vote!", ephemeral=True
            )
            return
        # make sure the user has not already voted
        if interaction.user.id in self.voters:
            await interaction.response.send_message(
                "You have already voted!", ephemeral=True
            )
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
        if interaction.user.id not in [player["id"] for player in self.bot.queue]:
            await interaction.response.send_message(
                "You must be in the queue to vote!", ephemeral=True
            )
            return
        if interaction.user.id in self.voters:
            await interaction.response.send_message(
                "You have already voted!", ephemeral=True
            )
            return

        self.votes["Captains"] += 1
        self.voters.add(interaction.user.id)
        self.captains_button.label = f"Captains ({self.votes['Captains']})"
        await interaction.message.edit(view=self)
        await interaction.response.send_message(
            f"Voted for Captains! Current votes: {self.votes['Captains']}",
            ephemeral=True,
        )

    def balanced_teams(self, players):
        players.sort(key=lambda p: self.bot.player_mmr[p["id"]]["mmr"], reverse=True)
        self.bot.match_ongoing = True
        team1, team2 = [], []
        team1_mmr, team2_mmr = 0, 0

        for player in players:
            if team1_mmr <= team2_mmr:
                team1.append(player)
                team1_mmr += self.bot.player_mmr[player["id"]]["mmr"]
            else:
                team2.append(player)
                team2_mmr += self.bot.player_mmr[player["id"]]["mmr"]

        return team1, team2

    async def balanced_teams_logic(self):
        team1, team2 = self.balanced_teams(self.bot.queue)
        await self.ctx.send(
            f"**Balanced Teams:**\nAttackers: {', '.join([p['name'] for p in team1])}\nDefenders: {', '.join([p['name'] for p in team2])}"
        )

        self.bot.match_ongoing = True

        self.bot.team1 = team1
        self.bot.team2 = team2

        vote_map_type = MapTypeVoteView(self.ctx, self.bot)

        # vote for maps next
        await vote_map_type.send_view()

    async def captains_mode(self):
        captains = []
        if self.bot.captain1:
            captains.append(self.bot.captain1)
        if self.bot.captain2:
            captains.append(self.bot.captain2)

        # Fill captains with highest MMR if not set
        if len(captains) < 2:
            sorted_players = sorted(
                self.bot.queue,
                key=lambda p: self.bot.player_mmr[p["id"]]["mmr"],
                reverse=True,
            )
            for player in sorted_players:
                if player not in captains:
                    captains.append(player)
                    if len(captains) == 2:
                        break

        captain1, captain2 = captains[:2]

        # Ensure captains are set in the bot
        self.bot.captain1 = captain1
        self.bot.captain2 = captain2

        await self.ctx.send(
            f"**Captains Mode Selected:**\n"
            f"Captain 1: {captain1['name']} (MMR: {self.bot.player_mmr[captain1['id']]['mmr']})\n"
            f"Captain 2: {captain2['name']} (MMR: {self.bot.player_mmr[captain2['id']]['mmr']})"
        )

    async def send_view(self):
        await self.ctx.send("Vote for how teams will be decided:", view=self)

        await asyncio.sleep(25)

        # Determine voting results
        if self.dummy is True:
            await self.ctx.send("Balanced Teams wins the vote!")
            await self.balanced_teams_logic()
        else:
            if self.votes["Balanced Teams"] > self.votes["Captains"]:
                await self.ctx.send("Balanced Teams wins the vote!")
                await self.balanced_teams_logic()
            elif self.votes["Captains"] > self.votes["Balanced Teams"]:
                await self.ctx.send("Captains wins the vote!")
                await self.captains_mode()
                captains_drafting = CaptainsDraftingView(self.ctx, self.bot)
                await captains_drafting.send_current_draft_view()
            else:
                decision = (
                    "Balanced Teams" if random.choice([True, False]) else "Captains"
                )
                await self.ctx.send(f"It's a tie! Flipping a coin... {decision} wins!")
                if decision == "Balanced Teams":
                    await self.balanced_teams_logic()
                else:
                    captains_drafting = CaptainsDraftingView(self.ctx, self.bot)
                    await captains_drafting.send_current_draft_view()
        self.bot.match_ongoing = True
        dummy = False

    def setup_callbacks(self):
        self.balanced_button.callback = self.balanced_callback
        self.captains_button.callback = self.captains_callback
