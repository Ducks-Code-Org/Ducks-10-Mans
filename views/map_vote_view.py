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
                if str(interaction.user.id) not in [str(p["id"]) for p in self.bot.queue]:
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
                await self._check_vote()
            btn.callback=cb
            self.map_buttons.append(btn)


    def _disable_buttons(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

    async def _start_timer(self):
        await asyncio.sleep(25)
        # If majority already handled, do nothing
        max_votes = max(self.map_votes.values()) if self.map_votes else 0
        if max_votes > 4:
            return

        if not self.map_votes:
            winning_map = random.choice(self.chosen_maps)
            await self.ctx.send(f"No votes received! Randomly selected map: **{winning_map}**")
        else:
            max_votes = max(self.map_votes.values())
            winners = [m for m, v in self.map_votes.items() if v == max_votes]
            if len(winners) > 1:
                winning_map = random.choice(winners)
                await self.ctx.send(f"Tie! Randomly selected: **{winning_map}**")
            else:
                winning_map = winners[0]

        self._disable_buttons()
        try:
            if hasattr(self, "_message") and self._message:
                await self._message.edit(view=self)
        except discord.HTTPException:
            pass

        await self._finalize_and_advance(winning_map)

    async def _finalize_and_advance(self, winning_map: str):
        self.winning_map = winning_map
        self.bot.selected_map = winning_map

        await self.ctx.send(f"Selected map: **{winning_map}**")

        if self.bot.chosen_mode == "Balanced":
            await self.finalize()
        elif self.bot.chosen_mode == "Captains":
            # Ensure captains exist
            if not self.bot.captain1 or not self.bot.captain2:
                sorted_players = sorted(
                    self.bot.queue,
                    key=lambda p: self.bot.player_mmr.get(str(p["id"]), {}).get("mmr", 1000),
                    reverse=True
                )
                self.bot.captain1 = sorted_players[0]
                self.bot.captain2 = sorted_players[1]

            choice_view = SecondCaptainChoiceView(self.ctx, self.bot)
            await self.ctx.send(
                f"<@{self.bot.captain2['id']}>, choose draft type:",
                view=choice_view
            )
        else:
            await self.ctx.send("Error: No game mode selected!")

    async def _check_vote(self):
        # majority check (5+)
        if not self.map_votes:
            return

        max_votes = max(self.map_votes.values())
        if max_votes > 4:
            winners = [m for m, v in self.map_votes.items() if v == max_votes]
            winning_map = random.choice(winners)
            self._disable_buttons()
            try:
                if hasattr(self, "_message") and self._message:
                    await self._message.edit(view=self)
            except discord.HTTPException:
                pass
            await self._finalize_and_advance(winning_map)

    async def send_view(self):
        if not self.bot.chosen_mode:
            print("[DEBUG] No mode selected at start of map vote")
            await self.ctx.send("Error: Game mode not selected. Please start a new queue.")
            return

        for b in self.map_buttons:
            self.add_item(b)

        self._message = await self.ctx.send("Vote for the map to play:", view=self)

        asyncio.create_task(self._start_timer())


    async def finalize(self):
        # Finalize teams after map chosen
        teams_embed=discord.Embed(
            title=f"Teams on {self.winning_map}",
            description="Good luck!",
            color=discord.Color.blue()
        )

        attackers=[]
        for p in self.bot.team1:
            ud=users.find_one({"discord_id": str(p["id"])})
            mmr=self.bot.player_mmr.get(str(p["id"]), {}).get("mmr", 1000)
            if ud:
                rn=ud.get("name","Unknown")
                rt=ud.get("tag","Unknown")
                attackers.append(f"{rn}#{rt} (MMR:{mmr})")
            else:
                attackers.append(f"{p['name']} (MMR:{mmr})")

        defenders=[]
        for p in self.bot.team2:
            ud=users.find_one({"discord_id": str(p["id"])})
            mmr=self.bot.player_mmr.get(str(p["id"]), {}).get("mmr", 1000)
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
