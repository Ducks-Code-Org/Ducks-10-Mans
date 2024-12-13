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
                if interaction.user.id not in [p["id"] for p in self.bot.queue]:
                    await interaction.response.send_message("Must be in queue!", ephemeral=True)
                    return
                if interaction.user.id in self.voters:
                    await interaction.response.send_message("Already voted!", ephemeral=True)
                    return
                self.map_votes[chosen]+=1
                self.voters.add(interaction.user.id)
                for b in self.map_buttons:
                    if b.label.startswith(chosen):
                        b.label=f"{chosen} ({self.map_votes[chosen]})"
                await interaction.message.edit(view=self)
                await interaction.response.send_message(f"Voted {chosen}.", ephemeral=True)
            btn.callback=cb
            self.map_buttons.append(btn)

    async def send_view(self):
        for b in self.map_buttons:
            self.add_item(b)
        await self.ctx.send("Vote for the map to play:", view=self)
        await asyncio.sleep(25)
        self.winning_map = max(self.map_votes,key=self.map_votes.get)
        await self.ctx.send(f"Selected map: **{self.winning_map}**")

        self.bot.selected_map=self.winning_map

        # Now check chosen_mode
        if self.bot.chosen_mode=="Balanced":
            # finalize directly
            await self.finalize()
        else:
            # Captains mode chosen, now run SecondCaptainChoiceView for pick order and then drafting
            await self.ctx.send("Captains mode chosen. Second captain, choose draft order.")
            choice_view=SecondCaptainChoiceView(self.ctx,self.bot)
            await self.ctx.send(f"<@{self.bot.captain2['id']}>, choose draft type:", view=choice_view)
            # After drafting finalizes (in CaptainsDraftingView finalize_draft method), finalize is called
            # The finalize will be called after drafting completes.

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