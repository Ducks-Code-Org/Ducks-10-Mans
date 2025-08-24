import asyncio
import random

import discord
from discord.ui import Button

from globals import official_maps, all_maps


async def _safe_reply(interaction, *args, **kwargs):
    if interaction.response.is_done():
        await interaction.followup.send(*args, **kwargs)
    else:
        await interaction.response.send_message(*args, **kwargs)


class MapTypeVoteView(discord.ui.View):
    def __init__(self, ctx, bot):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.bot = bot
        self.competitive_button = Button(
            label="Competitive Maps (0)", style=discord.ButtonStyle.green
        )
        self.all_maps_button = Button(
            label="All Maps (0)", style=discord.ButtonStyle.blurple
        )
        self.add_item(self.competitive_button)
        self.add_item(self.all_maps_button)

        self.map_pool_votes = {"Competitive": 0, "All": 0}
        self.voters = set()

        self.competitive_button.callback = self.comp_callback
        self.all_maps_button.callback = self.all_callback

        self.voting_phase_ended = False
        self.timeout = False
        self._message = None

    def _disable_buttons(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

    async def _check_vote(self):
        if self.voting_phase_ended:
            return

        comp = self.map_pool_votes["Competitive"]
        allm = self.map_pool_votes["All"]

        # 5+ wins immediately
        if comp > 4 or allm > 4 or self.timeout:
            # decide winner
            if comp > allm:
                await self.ctx.send("Competitive Maps chosen!")
                chosen_maps = official_maps
            elif allm > comp:
                await self.ctx.send("All Maps chosen!")
                chosen_maps = all_maps
            else:
                decision = "All" if random.choice([True, False]) else "Competitive"
                await self.ctx.send(f"Tie! {decision} Maps chosen!")
                chosen_maps = all_maps if decision == "All" else official_maps

            self.voting_phase_ended = True
            self._disable_buttons()
            # try to reflect disabled buttons
            try:
                if self._message:
                    await self._message.edit(view=self)
                await self.ctx.channel.send("Voting closed.")
            except discord.HTTPException:
                pass

            await self._go_to_map_vote(chosen_maps)

    async def _go_to_map_vote(self, chosen_maps):
        from views.map_vote_view import MapVoteView

        map_vote = MapVoteView(self.ctx, self.bot, chosen_maps)
        await map_vote.setup()
        await map_vote.send_view()

    async def comp_callback(self, interaction: discord.Interaction):
        if str(interaction.user.id) not in [str(p["id"]) for p in self.bot.queue]:
            await interaction.response.send_message("Must be in queue!", ephemeral=True)
            return
        if str(interaction.user.id) in self.voters:
            await interaction.response.send_message("Already voted!", ephemeral=True)
            return
        self.map_pool_votes["Competitive"] += 1
        self.voters.add(str(interaction.user.id))
        self.competitive_button.label = (
            f"Competitive Maps ({self.map_pool_votes['Competitive']})"
        )
        await interaction.message.edit(view=self)
        await interaction.response.send_message(
            "Voted Competitive Maps!", ephemeral=True
        )
        await self._check_vote()

    async def all_callback(self, interaction: discord.Interaction):
        if str(interaction.user.id) not in [str(p["id"]) for p in self.bot.queue]:
            await interaction.response.send_message("Must be in queue!", ephemeral=True)
            return
        if str(interaction.user.id) in self.voters:
            await interaction.response.send_message("Already voted!", ephemeral=True)
            return
        self.map_pool_votes["All"] += 1
        self.voters.add(str(interaction.user.id))
        self.all_maps_button.label = f"All Maps ({self.map_pool_votes['All']})"
        await interaction.message.edit(view=self)
        await interaction.response.send_message("Voted All Maps!", ephemeral=True)
        await self._check_vote()

    async def send_view(self):
        self._message = await self.ctx.send("Vote for the map pool:", view=self)
        asyncio.create_task(self._start_timer())

    async def _start_timer(self):
        await asyncio.sleep(25)
        if not self.voting_phase_ended:
            self.timeout = True
            await self._check_vote()
