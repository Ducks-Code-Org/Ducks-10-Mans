"""Captains drafting after second captain choice."""

import asyncio
import discord
from discord.ui import Select
from database import users
from discord.ui import Button

class SecondCaptainChoiceView(discord.ui.View):
    def __init__(self, ctx, bot):
        super().__init__(timeout=None)
        self.ctx=ctx
        self.bot=bot
        self.first_pick_button=Button(label="Single Pick", style=discord.ButtonStyle.green)
        self.double_pick_button=Button(label="Double Pick", style=discord.ButtonStyle.blurple)
        self.first_pick_button.callback=self.first_pick_callback
        self.double_pick_button.callback=self.double_pick_callback
        self.add_item(self.first_pick_button)
        self.add_item(self.double_pick_button)

    async def first_pick_callback(self, interaction: discord.Interaction):
        if interaction.user.id!=self.bot.captain2["id"]:
            await interaction.response.send_message("Only second captain can choose!", ephemeral=True)
            return
        await interaction.response.send_message("Single pick chosen. Starting draft...", ephemeral=True)
        await self.start_draft(True)

    async def double_pick_callback(self, interaction: discord.Interaction):
        if interaction.user.id!=self.bot.captain2["id"]:
            await interaction.response.send_message("Only second captain can choose!", ephemeral=True)
            return
        await interaction.response.send_message("Double pick chosen. Starting draft...", ephemeral=True)
        await self.start_draft(False)

    async def start_draft(self, single_pick: bool):
        drafting_view=CaptainsDraftingView(self.ctx,self.bot,single_pick)
        await drafting_view.send_current_draft_view()
        for c in self.children:
            c.disabled=True

class CaptainsDraftingView(discord.ui.View):
    def __init__(self, ctx, bot, single_pick):
        super().__init__(timeout=None)
        self.ctx=ctx
        self.bot=bot
        self.player_select=Select(placeholder="Pick player", options=[])
        self.add_item(self.player_select)
        self.remaining_players=[p for p in self.bot.queue if p not in [self.bot.captain1,self.bot.captain2]]
        if single_pick:
            self.pick_order=[
                self.bot.captain2,
                self.bot.captain1,
                self.bot.captain1,
                self.bot.captain2,
                self.bot.captain2,
                self.bot.captain1,
                self.bot.captain1,
                self.bot.captain2,
            ]
        else:
            self.pick_order=[
                self.bot.captain1,
                self.bot.captain2,
                self.bot.captain2,
                self.bot.captain1,
                self.bot.captain1,
                self.bot.captain2,
                self.bot.captain2,
                self.bot.captain1,
            ]
        self.pick_count=0
        self.remaining_players_message=None
        self.drafting_message=None
        self.captain_pick_message=None
        c1_data=users.find_one({"discord_id":str(self.bot.captain1["id"])})
        self.captain1_name=f"{c1_data.get('name','Unknown')}#{c1_data.get('tag','Unknown')}" if c1_data else self.bot.captain1["name"]
        c2_data=users.find_one({"discord_id":str(self.bot.captain2["id"])})
        self.captain2_name=f"{c2_data.get('name','Unknown')}#{c2_data.get('tag','Unknown')}" if c2_data else self.bot.captain2["name"]

        self.player_select.callback=self.select_callback

    async def finalize_draft(self):
        self.pick_count=0
        if self.remaining_players_message:
            await self.remaining_players_message.delete()
        if self.drafting_message:
            await self.drafting_message.delete()
        if self.captain_pick_message:
            await self.captain_pick_message.delete()

        self.bot.team1.insert(0,self.bot.captain1)
        self.bot.team2.insert(0,self.bot.captain2)

        # Now all steps done: finalize match (like in map_vote_view finalize)
        teams_embed=discord.Embed(title=f"Teams on {self.bot.selected_map}", description="Good luck!", color=discord.Color.blue())
        attackers=[]
        for p in self.bot.team1:
            ud=users.find_one({"discord_id":str(p["id"])})
            mmr=self.bot.player_mmr[p["id"]]["mmr"]
            if ud:
                rn=ud.get("name","Unknown")
                rt=ud.get("tag","Unknown")
                attackers.append(f"{rn}#{rt} (MMR:{mmr})")
            else:
                attackers.append(f"{p['name']} (MMR:{mmr})")

        defenders=[]
        for p in self.bot.team2:
            ud=users.find_one({"discord_id":str(p["id"])})
            mmr=self.bot.player_mmr[p["id"]]["mmr"]
            if ud:
                rn=ud.get("name","Unknown")
                rt=ud.get("tag","Unknown")
                defenders.append(f"{rn}#{rt} (MMR:{mmr})")
            else:
                defenders.append(f"{p['name']} (MMR:{mmr})")

        teams_embed.add_field(name="**Attackers:**", value="\n".join(attackers), inline=False)
        teams_embed.add_field(name="**Defenders:**", value="\n".join(defenders), inline=False)

        await self.ctx.send(embed=teams_embed)
        await self.ctx.send("Start match and use `!report` to finalize results.")

    async def select_callback(self, interaction: discord.Interaction):
        current_captain_id=self.pick_order[self.pick_count]["id"]
        if interaction.user.id!=current_captain_id:
            await interaction.response.send_message("Not your turn.", ephemeral=True)
            return
        selected_id=int(self.player_select.values[0])
        player_dict= next((p for p in self.remaining_players if p["id"]==selected_id),None)
        if not player_dict:
            await interaction.response.send_message("Player not available.", ephemeral=True)
            return

        if current_captain_id==self.bot.captain1["id"]:
            self.bot.team1.append(player_dict)
        else:
            self.bot.team2.append(player_dict)
        self.pick_count+=1
        self.remaining_players.remove(player_dict)
        await interaction.response.defer()
        await self.draft_next_player()

    async def draft_next_player(self):
        if len(self.remaining_players)==0:
            await self.finalize_draft()
            return
        await self.send_current_draft_view()

    async def send_current_draft_view(self):
        options=[]
        for p in self.remaining_players:
            ud=users.find_one({"discord_id":str(p["id"])})
            if ud:
                rn=ud.get("name","Unknown")
                rt=ud.get("tag","Unknown")
                label=f"{rn}#{rt}"
            else:
                label=p["name"]
            options.append(discord.SelectOption(label=label,value=str(p["id"])))
        self.player_select.options=options

        remaining_players_names=[]
        for p in self.remaining_players:
            ud=users.find_one({"discord_id":str(p["id"])})
            if ud:
                rn=ud.get("name","Unknown")
                rt=ud.get("tag","Unknown")
                name=f"{rn}#{rt}"
            else:
                name=p["name"]
            remaining_players_names.append(name)

        remaining_players_embed=discord.Embed(title="Remaining Players", description="\n".join(remaining_players_names), color=discord.Color.blue())
        team1_names=[]
        for p in self.bot.team1:
            ud=users.find_one({"discord_id":str(p["id"])})
            if ud:
                rn=ud.get("name","Unknown")
                rt=ud.get("tag","Unknown")
                team1_names.append(f"{rn}#{rt}")
            else:
                team1_names.append(p["name"])

        team2_names=[]
        for p in self.bot.team2:
            ud=users.find_one({"discord_id":str(p["id"])})
            if ud:
                rn=ud.get("name","Unknown")
                rt=ud.get("tag","Unknown")
                team2_names.append(f"{rn}#{rt}")
            else:
                team2_names.append(p["name"])

        drafting_embed=discord.Embed(title="Current Draft", color=discord.Color.green())
        drafting_embed.add_field(name=f"{self.captain1_name}'s Team", value="\n".join(team1_names) if team1_names else "No players yet", inline=False)
        drafting_embed.add_field(name=f"{self.captain2_name}'s Team", value="\n".join(team2_names) if team2_names else "No players yet", inline=False)

        current_captain_id=self.pick_order[self.pick_count]["id"]
        ud=users.find_one({"discord_id":str(current_captain_id)})
        if ud:
            curr_captain_name=f"{ud.get('name','Unknown')}#{ud.get('tag','Unknown')}"
        else:
            c = (self.bot.captain1 if self.bot.captain1["id"]==current_captain_id else self.bot.captain2)
            curr_captain_name=c["name"]

        message=f"**{curr_captain_name}**, pick a player:"
        if self.captain_pick_message:
            await self.remaining_players_message.edit(embed=remaining_players_embed)
            await self.drafting_message.edit(embed=drafting_embed)
            await self.captain_pick_message.edit(content=message, view=self)
        else:
            self.remaining_players_message=await self.ctx.send(embed=remaining_players_embed)
            self.drafting_message=await self.ctx.send(embed=drafting_embed)
            self.captain_pick_message=await self.ctx.send(content=message, view=self)

        try:
            await self.bot.wait_for(
                "interaction",
                check=lambda i: i.data.get("component_type") == 3 and i.user.id == current_captain_id,
                timeout=60,
            )
        except asyncio.TimeoutError:
            current_captain = next((c for c in [self.bot.captain1, self.bot.captain2] if c["id"]==current_captain_id), None)
            current_captain_name = current_captain["name"]
            await self.ctx.send(f"{current_captain_name} took too long. Draft canceled.")
            await asyncio.sleep(5)
            self.bot.queue.clear()
            await self.bot.match_channel.delete()
            await self.bot.match_role.delete()