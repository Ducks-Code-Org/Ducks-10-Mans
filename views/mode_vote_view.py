import asyncio
import random
import discord
from discord.ui import Button, View
import time

class ModeVoteView(discord.ui.View):
    def __init__(self, ctx, bot):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.bot = bot
        self.balanced_button = Button(label="Balanced Teams (0)", style=discord.ButtonStyle.green)
        self.captains_button = Button(label="Captains (0)", style=discord.ButtonStyle.blurple)
        self.add_item(self.balanced_button)
        self.add_item(self.captains_button)

        self.votes = {"Balanced":0, "Captains":0}
        self.voters = set()

        self.balanced_button.callback = self.balanced_callback
        self.captains_button.callback = self.captains_callback

        self.voting_phase_ended = False
        self.timeout = False

    async def balanced_callback(self, interaction: discord.Interaction):
        if self.voting_phase_ended: #doesn't allow for votes if phase has already ended
            await interaction.response.send_message("This voting phase has already ended", ephemeral=True)

        if interaction.user.id not in [p["id"] for p in self.bot.queue]:
            await interaction.response.send_message("Must be in queue!", ephemeral=True)
            return
        
        if interaction.user.id in self.voters:
            await interaction.response.send_message("Already voted!", ephemeral=True)
            return
        self.votes["Balanced"]+=1
        self.voters.add(interaction.user.id)
        self.balanced_button.label = f"Balanced Teams ({self.votes['Balanced']})"
        self.check_vote() #check if an option has won
        await interaction.message.edit(view=self)
        await interaction.response.send_message("Voted Balanced!", ephemeral=True)

    async def captains_callback(self, interaction: discord.Interaction):
        if self.voting_phase_ended:
            await interaction.response.send_message("This voting phase has already ended", ephemeral=True)

        if interaction.user.id not in [p["id"] for p in self.bot.queue]:
            await interaction.response.send_message("Must be in queue!", ephemeral=True)
            return
  
        if interaction.user.id in self.voters:
            await interaction.response.send_message("Already voted!", ephemeral=True)
            return
        
        self.votes["Captains"]+=1
        self.voters.add(interaction.user.id)
        self.captains_button.label = f"Captains ({self.votes['Captains']})"
        self.check_vote() #check if an option has won
        await interaction.message.edit(view=self)
        await interaction.response.send_message("Voted Captains!", ephemeral=True)

    async def send_view(self):
        await self.ctx.send("Vote for mode (Balanced/Captains):", view=self)
        asyncio.create_task(self.start_timer())

    #check_vote checks if an option has mathemetically won after every vote callback, as well as handles voting phase timeout logic
    async def check_vote(self):
        if self.timeout: 
            if self.votes["Balanced"]>self.votes["Captains"]:
                self.bot.chosen_mode="Balanced"
                await self.ctx.send("Balanced Teams chosen!")
                # Set balanced teams now
                # sort by mmr, alternate
                players= self.bot.queue[:]
                players.sort(key=lambda p: self.bot.player_mmr[p["id"]]["mmr"], reverse=True)
                team1, team2 = [], []
                t1_mmr=0
                t2_mmr=0
                for player in players:
                    if t1_mmr<=t2_mmr:
                        team1.append(player)
                        t1_mmr+= self.bot.player_mmr[player["id"]]["mmr"]
                    else:
                        team2.append(player)
                        t2_mmr+= self.bot.player_mmr[player["id"]]["mmr"]
                self.bot.team1=team1
                self.bot.team2=team2
            elif self.votes["Captains"]>self.votes["Balanced"]:
                self.bot.chosen_mode="Captains"
                await self.ctx.send("Captains chosen! Captains will be set after map is chosen.")
            else:
                decision="Balanced" if random.choice([True,False]) else "Captains"
                await self.ctx.send(f"Tie! {decision} wins by coin flip!")
                self.bot.chosen_mode=decision
                if decision=="Balanced":
                    # do balanced assignment now
                    players= self.bot.queue[:]
                    players.sort(key=lambda p: self.bot.player_mmr[p["id"]]["mmr"], reverse=True)
                    team1, team2 = [], []
                    t1_mmr=0
                    t2_mmr=0
                    for player in players:
                        if t1_mmr<=t2_mmr:
                            team1.append(player)
                            t1_mmr+= self.bot.player_mmr[player["id"]]["mmr"]
                        else:
                            team2.append(player)
                            t2_mmr+= self.bot.player_mmr[player["id"]]["mmr"]
                    self.bot.team1=team1
                    self.bot.team2=team2

            return

        if self.votes["Captains"] > 4: #if captains reaches 5 votes first, it wins (reaching 5 first can be considered as a tiebreaker)
            self.bot.chosen_mode="Captains"
            self.voting_phase_ended = True
            await self.ctx.send("Captains chosen! Captains will be set after map is chosen.")
            return
        elif self.votes["Balanced Teams"] > 4:
            self.bot.chosen_mode="Balanced"
            self.voting_phase_ended = True
            await self.ctx.send("Balanced Teams chosen!")
            # Set balanced teams now
            # sort by mmr, alternate
            players= self.bot.queue[:]
            players.sort(key=lambda p: self.bot.player_mmr[p["id"]]["mmr"], reverse=True)
            team1, team2 = [], []
            t1_mmr=0
            t2_mmr=0
            for player in players:
                if t1_mmr<=t2_mmr:
                    team1.append(player)
                    t1_mmr+= self.bot.player_mmr[player["id"]]["mmr"]
                else:
                    team2.append(player)
                    t2_mmr+= self.bot.player_mmr[player["id"]]["mmr"]
            self.bot.team1=team1
            self.bot.team2=team2
            return

    async def start_timer(self):
        await asyncio.sleep(25)  # Wait 25 seconds
        if not self.voting_phase_ended:  # Ensure we haven't already ended the voting phase
            self.timeout = True
            self.voting_phase_ended = True
            await self.check_vote()
            

        # After mode chosen, do map type vote
        from views.map_type_vote_view import MapTypeVoteView
        map_type_vote=MapTypeVoteView(self.ctx,self.bot)
        await map_type_vote.send_view()