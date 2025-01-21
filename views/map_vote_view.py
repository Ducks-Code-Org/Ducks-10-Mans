import asyncio
import discord
from discord.ui import Button
from database import users
import random

from views.captains_drafting_view import SecondCaptainChoiceView, CaptainsDraftingView

class MapVoteView(discord.ui.View):
    def __init__(self, ctx, bot, map_choices):
        super().__init__(timeout=None)
        self.ctx=ctx
        self.bot=bot
        self.map_choices=map_choices
        self.map_buttons=[]
        self.map_votes={}
        self.chosen_maps=[]
        self.winning_map=""
        self.voters=set()

    async def setup(self):
        random_maps=random.sample(self.map_choices,3)
        self.chosen_maps=random_maps
        self.map_votes={m:0 for m in random_maps}
        for m in random_maps:
            btn=Button(label=f"{m} (0)", style=discord.ButtonStyle.secondary)
            async def cb(interaction: discord.Interaction, chosen=m):
                if str(interaction.user.id) not in [p["id"] for p in self.bot.queue]:
                    await interaction.response.send_message("Must be in queue!", ephemeral=True)
                    return
                if str(interaction.user.id) in self.voters:
                    await interaction.response.send_message("Already voted!", ephemeral=True)
                    return
                self.map_votes[chosen]+=1
                self.voters.add(str(interaction.user.id))
                for b in self.map_buttons:
                    if b.label.startswith(chosen):
                        b.label=f"{chosen} ({self.map_votes[chosen]})"
                await interaction.message.edit(view=self)
                await interaction.response.send_message(f"Voted {chosen}.", ephemeral=True)
            btn.callback=cb
            self.map_buttons.append(btn)

    async def send_view(self):
        if not self.bot.bot.chosen_mode:
            print("[DEBUG] No mode selected at start of map vote")
            await self.ctx.send("Error: Game mode not selected. Please start a new queue.")
            return
        for b in self.map_buttons:
            self.add_item(b)
        message = await self.ctx.send("Vote for the map to play:", view=self)
        
        try:
            await asyncio.sleep(25)
            
            if not self.map_votes:  # If no votes received
                self.winning_map = random.choice(self.chosen_maps)
                await self.ctx.send(f"No votes received! Randomly selected map: **{self.winning_map}**")
            else:
                max_votes = max(self.map_votes.values())
                winning_maps = [m for m, v in self.map_votes.items() if v == max_votes]
                
                if len(winning_maps) > 1:
                    self.winning_map = random.choice(winning_maps)
                    await self.ctx.send(f"Tie! Randomly selected: **{self.winning_map}**")
                else:
                    self.winning_map = winning_maps[0]
                    await self.ctx.send(f"Selected map: **{self.winning_map}**")

            self.bot.selected_map = self.winning_map

            # Now check chosen_mode and proceed accordingly
            if self.bot.chosen_mode == "Balanced":
                await self.finalize()
            elif self.bot.chosen_mode == "Captains":
                if not self.bot.captain1 or not self.bot.captain2:
                    sorted_players = sorted(
                        self.bot.queue,
                        key=lambda p: self.bot.player_mmr[p["id"]]["mmr"],
                        reverse=True
                    )
                    self.bot.captain1 = sorted_players[0]
                    self.bot.captain2 = sorted_players[1]
                
                from views.captains_drafting_view import SecondCaptainChoiceView
                choice_view = SecondCaptainChoiceView(self.ctx, self.bot)
                await self.ctx.send(
                    f"<@{self.bot.captain2['id']}>, choose draft type:",
                    view=choice_view
                )
            else:
                await self.ctx.send("Error: No game mode selected!")
                
        except Exception as e:
            await self.ctx.send(f"Error during map selection: {str(e)}")

    async def finalize(self):
        # Finalize teams after map chosen.
        teams_embed=discord.Embed(
            title=f"Teams on {self.winning_map}",
            description="Good luck!",
            color=discord.Color.blue()
        )

        attackers=[]
        for p in self.bot.team1:
            ud=users.find_one({"discord_id": str(p["id"])})
            mmr=self.bot.player_mmr.get(p["id"],{}).get("mmr",1000)
            if ud:
                rn=ud.get("name","Unknown")
                rt=ud.get("tag","Unknown")
                attackers.append(f"{rn}#{rt} (MMR:{mmr})")
            else:
                attackers.append(f"{p['name']} (MMR:{mmr})")

        defenders=[]
        for p in self.bot.team2:
            ud=users.find_one({"discord_id": str(p["id"])})
            mmr=self.bot.player_mmr.get(p["id"],{}).get("mmr",1000)
            if ud:
                rn=ud.get("name","Unknown")
                rt=ud.get("tag","Unknown")
                defenders.append(f"{rn}#{rt} (MMR:{mmr})")
            else:
                defenders.append(f"{p['name']} (MMR:{mmr})")

        teams_embed.add_field(name="**Attackers:**", value="\n".join(attackers), inline=False)
        teams_embed.add_field(name="**Defenders:**", value="\n".join(defenders), inline=False)

        await self.ctx.send(embed=teams_embed)
        await self.ctx.send("Start match, then `!report` to finalize results.")
        self.bot.match_ongoing = True
        self.bot.match_not_reported = True
