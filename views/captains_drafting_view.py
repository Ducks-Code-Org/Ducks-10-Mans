import asyncio
from typing import Dict

import discord
from discord.ui import Select
from urllib.parse import quote

from database import users


class SecondCaptainChoiceView(discord.ui.View):
    def __init__(self, ctx, bot):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.bot = bot

        # Buttons
        self.first_pick_button = discord.ui.Button(
            label="First Pick", style=discord.ButtonStyle.green, custom_id="single_pick"
        )
        self.double_pick_button = discord.ui.Button(
            label="2nd + 3rd Pick",
            style=discord.ButtonStyle.blurple,
            custom_id="double_pick",
        )

        # Callbacks
        self.first_pick_button.callback = self.first_pick_callback
        self.double_pick_button.callback = self.double_pick_callback

        # Add to view
        self.add_item(self.first_pick_button)
        self.add_item(self.double_pick_button)

    async def send_view(self):
        """Show captains and present draft mode choice."""
        c1_data = users.find_one({"discord_id": str(self.bot.captain1["id"])})
        c2_data = users.find_one({"discord_id": str(self.bot.captain2["id"])})

        c1_name = (
            f"{c1_data.get('name', 'Unknown')}#{c1_data.get('tag', 'Unknown')}"
            if c1_data
            else self.bot.captain1["name"]
        )
        c2_name = (
            f"{c2_data.get('name', 'Unknown')}#{c2_data.get('tag', 'Unknown')}"
            if c2_data
            else self.bot.captain2["name"]
        )

        embed = discord.Embed(
            title="Team Captains",
            description="Choose draft type",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Captain 1", value=c1_name, inline=True)
        embed.add_field(name="Captain 2", value=c2_name, inline=True)
        embed.add_field(
            name="Instructions",
            value=f"<@{self.bot.captain2['id']}> must choose either Single Pick or Double Pick",
            inline=False,
        )

        await self.ctx.send(embed=embed, view=self)

    async def _validate_second_captain(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != str(self.bot.captain2["id"]):
            await interaction.response.send_message(
                "Only the second captain can make this choice!", ephemeral=True
            )
            return False
        return True

    async def first_pick_callback(self, interaction: discord.Interaction):
        if not await self._validate_second_captain(interaction):
            return

        # Disable buttons after
        self.first_pick_button.disabled = True
        self.double_pick_button.disabled = True
        await interaction.message.edit(view=self)

        await interaction.response.send_message("First pick selected!", ephemeral=True)
        await self.start_draft(single_pick=True)

    async def double_pick_callback(self, interaction: discord.Interaction):
        if not await self._validate_second_captain(interaction):
            return

        self.first_pick_button.disabled = True
        self.double_pick_button.disabled = True
        await interaction.message.edit(view=self)

        await interaction.response.send_message(
            "2nd + 3rd pick selected!", ephemeral=True
        )
        await self.start_draft(single_pick=False)

    async def start_draft(self, single_pick: bool):
        mode_name = "Single Pick" if single_pick else "Double Pick"
        await self.ctx.send(f"**{mode_name}** chosen! Starting draft phase...")

        drafting_view = CaptainsDraftingView(self.ctx, self.bot, single_pick)

        await drafting_view.send_current_draft_view()

        # Lock buttons
        for child in self.children:
            child.disabled = True


class CaptainsDraftingView(discord.ui.View):
    def __init__(self, ctx, bot, single_pick: bool):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.bot = bot

        # Build remaining pool
        cap1_id = str(self.bot.captain1["id"])
        cap2_id = str(self.bot.captain2["id"])
        self.remaining_players = [
            p for p in self.bot.queue if str(p["id"]) not in {cap1_id, cap2_id}
        ]

        # Pick order patterns
        if single_pick:
            self.pick_order = [
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
            self.pick_order = [
                self.bot.captain1,
                self.bot.captain2,
                self.bot.captain2,
                self.bot.captain1,
                self.bot.captain1,
                self.bot.captain2,
                self.bot.captain2,
                self.bot.captain1,
            ]

        self.pick_count = 0
        self.draft_finished = False

        self.remaining_players_message = None
        self.drafting_message = None
        self.captain_pick_message = None

        if not getattr(self.bot, "team1", None):
            self.bot.team1 = []
        if not getattr(self.bot, "team2", None):
            self.bot.team2 = []
        if not self.bot.team1 or self.bot.team1[0].get("id") != self.bot.captain1["id"]:
            if not any(p.get("id") == self.bot.captain1["id"] for p in self.bot.team1):
                self.bot.team1.insert(0, self.bot.captain1)
        if not self.bot.team2 or self.bot.team2[0].get("id") != self.bot.captain2["id"]:
            if not any(p.get("id") == self.bot.captain2["id"] for p in self.bot.team2):
                self.bot.team2.insert(0, self.bot.captain2)

        c1_data = users.find_one({"discord_id": str(self.bot.captain1["id"])})
        c2_data = users.find_one({"discord_id": str(self.bot.captain2["id"])})
        self.captain1_name = (
            f"{c1_data.get('name','Unknown')}#{c1_data.get('tag','Unknown')}"
            if c1_data
            else self.bot.captain1["name"]
        )
        self.captain2_name = (
            f"{c2_data.get('name','Unknown')}#{c2_data.get('tag','Unknown')}"
            if c2_data
            else self.bot.captain2["name"]
        )

        # component for picking players
        self.player_select = Select(placeholder="Pick player", options=[])
        self.player_select.callback = self.select_callback
        self.add_item(self.player_select)

    # logic
    def _team_cap(self) -> int:
        """Max players per team INCLUDING the captain. Default 5."""
        return getattr(self, "max_per_team", 5)

    def _picks_exhausted(self) -> bool:
        """
        Stop if:
        - we've used all entries in pick_order, OR
        - both teams reached the cap, OR
        - no players left to pick.
        """
        cap = self._team_cap()

        team1_len = len(self.bot.team1)
        team2_len = len(self.bot.team2)

        out_of_turns = self.pick_count >= len(self.pick_order)
        teams_full = (team1_len >= cap) and (team2_len >= cap)
        pool_empty = len(self.remaining_players) == 0

        return out_of_turns or teams_full or pool_empty

    def picks_exhausted(self) -> bool:
        return self._picks_exhausted()

    async def finalize_draft(self):
        """Finalize teams, cleanup UI, announce result."""
        if self.draft_finished:
            return
        self.draft_finished = True

        try:
            self.player_select.disabled = True
        except Exception:
            pass

        while self.remaining_players:
            target = (
                self.bot.team1
                if len(self.bot.team1) <= len(self.bot.team2)
                else self.bot.team2
            )
            target.append(self.remaining_players.pop(0))

        for msg_attr in (
            "remaining_players_message",
            "drafting_message",
            "captain_pick_message",
        ):
            msg = getattr(self, msg_attr, None)
            if msg:
                try:
                    await msg.delete()
                except discord.NotFound:
                    pass
                setattr(self, msg_attr, None)

        # final teams embed
        map_name = getattr(self.bot, "selected_map", "Map")
        teams_embed = discord.Embed(
            title=f"Teams on {map_name}",
            description="Good luck!",
            color=discord.Color.blue(),
        )

        attackers = []
        for p in self.bot.team1:
            ud = users.find_one({"discord_id": str(p["id"])})
            mmr = (
                getattr(self.bot, "player_mmr", {})
                .get(str(p["id"]), {})
                .get("mmr", 1000)
            )
            if ud:
                rn = ud.get("name", "Unknown")
                rt = ud.get("tag", "Unknown")
                attackers.append(f"{rn}#{rt} (MMR:{mmr})")
            else:
                attackers.append(f"{p['name']} (MMR:{mmr})")

        defenders = []
        for p in self.bot.team2:
            ud = users.find_one({"discord_id": str(p["id"])})
            mmr = (
                getattr(self.bot, "player_mmr", {})
                .get(str(p["id"]), {})
                .get("mmr", 1000)
            )
            if ud:
                rn = ud.get("name", "Unknown")
                rt = ud.get("tag", "Unknown")
                defenders.append(f"{rn}#{rt} (MMR:{mmr})")
            else:
                defenders.append(f"{p['name']} (MMR:{mmr})")

        teams_embed.add_field(
            name="**Attackers:**", value="\n".join(attackers) or "—", inline=False
        )
        teams_embed.add_field(
            name="**Defenders:**", value="\n".join(defenders) or "—", inline=False
        )

        await self.ctx.send(embed=teams_embed)
        await self.ctx.send("Start match and use `!report` to finalize results.")

        self.bot.match_ongoing = True
        self.bot.match_not_reported = True
        self.bot.match_channel.edit(name=f"{self.bot.match_name}《LIVE》")

        # prevent further callbacks
        try:
            self.stop()
        except Exception:
            pass

    async def finish_draft(self, *args, **kwargs):
        await self.finalize_draft()

    async def select_callback(self, interaction: discord.Interaction):
        if self.draft_finished:
            await interaction.response.send_message(
                "Draft is already complete!", ephemeral=True
            )
            return

        if self._picks_exhausted():
            await self.finalize_draft()
            return

        # Enforce turn taking
        current_captain_id = str(self.pick_order[self.pick_count]["id"])
        if str(interaction.user.id) != current_captain_id:
            await interaction.response.send_message("Not your turn.", ephemeral=True)
            return

        selected_id = str(self.player_select.values[0])
        player_dict = next(
            (p for p in self.remaining_players if str(p["id"]) == selected_id), None
        )
        if not player_dict:
            await interaction.response.send_message(
                "Player not available.", ephemeral=True
            )
            return

        # Assign to current captain's team
        if current_captain_id == str(self.bot.captain1["id"]):
            self.bot.team1.append(player_dict)
        else:
            self.bot.team2.append(player_dict)

        self.pick_count += 1
        try:
            self.remaining_players.remove(player_dict)
        except ValueError:
            pass

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        await self.draft_next_player()

    async def draft_next_player(self):
        if self._picks_exhausted():
            await self.finalize_draft()
            return
        await self.send_current_draft_view()

    async def send_current_draft_view(self):
        if self.draft_finished:
            return

        # If no one left to pick, finish
        if not self.remaining_players or self._picks_exhausted():
            await self.finalize_draft()
            return

        # Rebuild select options from remaining players
        options = []
        for player in self.remaining_players:
            user_data = users.find_one({"discord_id": str(player["id"])})
            if user_data:
                label = f"{user_data.get('name', 'Unknown')}#{user_data.get('tag', 'Unknown')}"
            else:
                label = player["name"]
            options.append(discord.SelectOption(label=label, value=str(player["id"])))
        self.player_select.options = options

        # Remaining players embed

        remaining_players_data: Dict[str, str | None] = (
            {}
        )  # Maps player names to tracker.gg links
        for player in self.remaining_players:
            user_data = users.find_one({"discord_id": str(player["id"])})
            if user_data:
                user_name = quote(f"{user_data.get('name','Unknown')}")
                user_tag = quote(f"{user_data.get('tag','Unknown')}")
                remaining_players_data[f"{user_name}#{user_tag}"] = (
                    f"https://tracker.gg/valorant/profile/riot/{user_name}%23{user_tag}/overview"
                )
            else:
                remaining_players_data[player["name"]] = None

        remaining_players_text: str = ""
        if remaining_players_data:
            for name, tracker_link in remaining_players_data.items():
                if tracker_link:
                    remaining_players_text += f"[{name}]({tracker_link})\n"
                else:
                    remaining_players_text += f"{name}\n"
            # Remove trailing newline
            remaining_players_text = remaining_players_text.rstrip("\n")
        else:
            remaining_players_text = "—"

        remaining_players_embed = discord.Embed(
            title="Remaining Players",
            description=remaining_players_text,
            color=discord.Color.blue(),
        )

        # Teams embed
        def list_names(team):
            out = []
            for p in team:
                ud = users.find_one({"discord_id": str(p["id"])})
                if ud:
                    out.append(f"{ud.get('name','Unknown')}#{ud.get('tag','Unknown')}")
                else:
                    out.append(p["name"])
            return "\n".join(out) if out else "No players yet"

        drafting_embed = discord.Embed(
            title="Current Draft", color=discord.Color.green()
        )
        drafting_embed.add_field(
            name=f"{self.captain1_name}'s Team",
            value=list_names(self.bot.team1),
            inline=False,
        )
        drafting_embed.add_field(
            name=f"{self.captain2_name}'s Team",
            value=list_names(self.bot.team2),
            inline=False,
        )

        # Prompt for current captain
        current_captain_id = self.pick_order[self.pick_count]["id"]
        ud = users.find_one({"discord_id": str(current_captain_id)})
        if ud:
            curr_captain_name = f"{ud.get('name','Unknown')}#{ud.get('tag','Unknown')}"
        else:
            c = (
                self.bot.captain1
                if self.bot.captain1["id"] == current_captain_id
                else self.bot.captain2
            )
            curr_captain_name = c["name"]

        message = f"**{curr_captain_name}**, pick a player:"

        if (
            self.captain_pick_message
            and self.remaining_players_message
            and self.drafting_message
        ):
            try:
                await self.remaining_players_message.edit(embed=remaining_players_embed)
                await self.drafting_message.edit(embed=drafting_embed)
                await self.captain_pick_message.edit(content=message, view=self)
            except discord.NotFound:
                self.remaining_players_message = await self.ctx.send(
                    embed=remaining_players_embed
                )
                self.drafting_message = await self.ctx.send(embed=drafting_embed)
                self.captain_pick_message = await self.ctx.send(
                    content=message, view=self
                )
        else:
            self.remaining_players_message = await self.ctx.send(
                embed=remaining_players_embed
            )
            self.drafting_message = await self.ctx.send(embed=drafting_embed)
            self.captain_pick_message = await self.ctx.send(content=message, view=self)

        if not self.draft_finished:
            # If only one player left, auto-assign and finalize
            if len(self.remaining_players) == 1:
                player_dict = self.remaining_players[0]
                if str(current_captain_id) == str(self.bot.captain1["id"]):
                    self.bot.team1.append(player_dict)
                else:
                    self.bot.team2.append(player_dict)
                self.pick_count += 1
                self.remaining_players.clear()
                await self.finalize_draft()
                return
            try:
                await self.bot.wait_for(
                    "interaction",
                    check=lambda i: i.data.get("component_type") == 3
                    and str(i.user.id) == str(current_captain_id),
                    timeout=120,
                )
            except asyncio.TimeoutError:
                if not self.draft_finished:
                    await self.ctx.send(
                        f"{curr_captain_name} took too long. Draft canceled. Cleaning up…"
                    )
                    await asyncio.sleep(2)

                    # Reset state
                    self.bot.signup_active = False
                    self.bot.match_ongoing = False
                    self.bot.match_not_reported = False
                    self.bot.queue.clear()
                    self.bot.team1 = []
                    self.bot.team2 = []
                    self.bot.captain1 = None
                    self.bot.captain2 = None
                    self.bot.chosen_mode = None
                    self.bot.selected_map = None

                    try:
                        if getattr(self.bot, "match_channel", None):
                            await self.bot.match_channel.delete()
                    except discord.NotFound:
                        pass
                    finally:
                        self.bot.match_channel = None

                    try:
                        if getattr(self.bot, "match_role", None):
                            await self.bot.match_role.delete()
                    except discord.NotFound:
                        pass
                    finally:
                        self.bot.match_role = None

                    try:
                        self.stop()
                    except Exception:
                        pass
