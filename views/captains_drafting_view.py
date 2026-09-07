import asyncio
import math

import discord
from discord.ui import Select

from database import users
from commands.report import cleanup_match_resources
from recent_queue import remember_recent_queue
from tracker_links import tracker_link

DECISION_TIMEOUT_SECONDS = 120
PICK_TIMEOUT_SECONDS = 120


class SecondCaptainChoiceView(discord.ui.View):
    def __init__(self, ctx, bot, setup_generation: int | None = None):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.bot = bot
        # Capture the current setup cycle so we can detect a later !cancel
        # (or a new signup superseding this view). Parent views pass their own
        # captured generation so a cancel racing view creation is still seen.
        self.setup_generation = (
            bot.setup_generation if setup_generation is None else setup_generation
        )
        self.view_message = None
        self.decision_time_remaining = DECISION_TIMEOUT_SECONDS
        self.timeout_timer_task = asyncio.create_task(self.timeout_timer())
        self.decision_finished = False

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
        if self.is_setup_cancelled():
            self.decision_finished = True
            self.cancel_timeout_timer()
            return
        await self.ctx.send(
            f"Captains Chosen: <@{self.bot.captain1['id']}> and <@{self.bot.captain2['id']}>"
        )
        self.view_message = await self.ctx.send(
            f"<@{self.bot.captain2['id']}>, choose draft type: ({self.decision_time_remaining}s)",
            view=self,
        )

    def is_setup_cancelled(self) -> bool:
        """Whether this setup cycle was cancelled (e.g. by !cancel)."""
        return self.bot.setup_generation != self.setup_generation

    async def _validate_second_captain(self, interaction: discord.Interaction) -> bool:
        if self.decision_finished:
            await interaction.response.send_message(
                "This decision phase has already ended.", ephemeral=True
            )
            return False
        if self.is_setup_cancelled():
            self.decision_finished = True
            self.cancel_timeout_timer()
            try:
                self.stop()
            except Exception:
                pass
            await interaction.response.send_message(
                "This match setup was cancelled.", ephemeral=True
            )
            return False
        captain2 = getattr(self.bot, "captain2", None)
        if not captain2 or str(interaction.user.id) != str(captain2["id"]):
            await interaction.response.send_message(
                "Only the second captain can make this choice!", ephemeral=True
            )
            return False
        return True

    async def first_pick_callback(self, interaction: discord.Interaction):
        if not await self._validate_second_captain(interaction):
            return

        self.cancel_timeout_timer()
        self.decision_finished = True
        self.first_pick_button.disabled = True
        self.double_pick_button.disabled = True
        await interaction.message.edit(view=self)

        await interaction.response.send_message("First pick selected!", ephemeral=True)
        await self.start_draft(single_pick=True)

    async def double_pick_callback(self, interaction: discord.Interaction):
        if not await self._validate_second_captain(interaction):
            return

        self.cancel_timeout_timer()
        self.decision_finished = True
        self.first_pick_button.disabled = True
        self.double_pick_button.disabled = True
        await interaction.message.edit(view=self)

        await interaction.response.send_message(
            "2nd + 3rd pick selected!", ephemeral=True
        )
        await self.start_draft(single_pick=False)

    async def start_draft(self, single_pick: bool):
        # Only proceed if this setup cycle is still the current one.
        if self.is_setup_cancelled():
            return
        mode_name = "Single Pick" if single_pick else "Double Pick"
        await self.ctx.send(f"**{mode_name}** chosen! Starting draft phase...")

        drafting_view = CaptainsDraftingView(
            self.ctx, self.bot, single_pick, self.setup_generation
        )

        await drafting_view.send_current_draft_view()

        # Lock buttons
        for child in self.children:
            child.disabled = True

    async def timeout_timer(self):
        loop = asyncio.get_event_loop()
        start_time = loop.time()
        last_shown = DECISION_TIMEOUT_SECONDS
        while True:
            if self.decision_finished:
                return
            remaining = DECISION_TIMEOUT_SECONDS - (loop.time() - start_time)
            if remaining <= 0:
                break
            display_time = math.ceil(remaining)
            if display_time != last_shown:
                last_shown = display_time
                self.decision_time_remaining = display_time
                captain2 = getattr(self.bot, "captain2", None)
                if self.is_setup_cancelled() or not captain2:
                    self.decision_finished = True
                    return
                if self.view_message:
                    try:
                        await self.view_message.edit(
                            content=f"<@{captain2['id']}>, choose draft type: ({self.decision_time_remaining}s)",
                            view=self,
                        )
                    except discord.NotFound:
                        pass
            await asyncio.sleep(min(1.0, remaining))
        if self.decision_finished:
            return
        if self.is_setup_cancelled():
            # Match setup was cancelled (e.g. by !cancel); nothing to do.
            self.decision_finished = True
            return
        if self.view_message:
            captain2 = getattr(self.bot, "captain2", None)
            if captain2:
                try:
                    await self.view_message.edit(
                        content=f"<@{captain2['id']}>, choose draft type: (0s)",
                        view=self,
                    )
                except discord.NotFound:
                    pass
        # Cancel Signup
        self.decision_finished = True
        try:
            await self.ctx.send("The captain took too long. Match will be cancelled...")
        except (discord.NotFound, discord.HTTPException):
            pass
        self.bot.setup_generation += 1
        self.bot.signup_active = False
        self.bot.match_ongoing = False
        self.bot.match_not_reported = False
        if self.bot.queue:
            remember_recent_queue(self.bot.queue)
        self.bot.queue.clear()
        self.bot.team1 = []
        self.bot.team2 = []
        self.bot.captain1 = None
        self.bot.captain2 = None
        self.bot.chosen_mode = None
        self.bot.selected_map = None

        await cleanup_match_resources(self.bot)

        try:
            self.stop()
        except Exception:
            pass

    def cancel_timeout_timer(self):
        if self.timeout_timer_task:
            self.timeout_timer_task.cancel()
            self.timeout_timer_task = None


class CaptainsDraftingView(discord.ui.View):
    def __init__(
        self, ctx, bot, single_pick: bool, setup_generation: int | None = None
    ):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.bot = bot
        # Capture the current setup cycle so we can detect a later !cancel
        # (or a new signup superseding this draft). Parent views pass their own
        # captured generation so a cancel racing view creation is still seen.
        self.setup_generation = (
            bot.setup_generation if setup_generation is None else setup_generation
        )

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

        self.draft_time_remaining = PICK_TIMEOUT_SECONDS
        self.draft_timer_task = None

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
            tracker_link(c1_data.get("name", "Unknown"), c1_data.get("tag", "Unknown"))
            if c1_data
            else self.bot.captain1["name"]
        )
        self.captain2_name = (
            tracker_link(c2_data.get("name", "Unknown"), c2_data.get("tag", "Unknown"))
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

        # If the match setup was cancelled externally (e.g. !cancel), skip
        # finalization entirely.
        if self.is_setup_cancelled():
            return

        if self.draft_timer_task:
            self.draft_timer_task.cancel()
            self.draft_timer_task = None

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
                attackers.append(
                    f"{tracker_link(ud.get('name', 'Unknown'), ud.get('tag', 'Unknown'))} (MMR:{mmr})"
                )
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
                defenders.append(
                    f"{tracker_link(ud.get('name', 'Unknown'), ud.get('tag', 'Unknown'))} (MMR:{mmr})"
                )
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
        if self.bot.match_channel:
            try:
                await self.bot.match_channel.edit(
                    name=f"{self.bot.match_name}《in-game》"
                )
            except (discord.NotFound, discord.HTTPException):
                pass

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

        # If the match setup was cancelled externally (e.g. !cancel mid-draft),
        # stop quietly without processing the pick.
        if self.is_setup_cancelled():
            self.draft_finished = True
            self.cancel_draft_timer()
            try:
                self.player_select.disabled = True
            except Exception:
                pass
            try:
                self.stop()
            except Exception:
                pass
            await interaction.response.send_message(
                "This match setup was cancelled.", ephemeral=True
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
        captain1 = getattr(self.bot, "captain1", None)
        if current_captain_id == str(captain1["id"]):
            self.bot.team1.append(player_dict)
        else:
            self.bot.team2.append(player_dict)

        self.pick_count += 1
        try:
            self.remaining_players.remove(player_dict)
        except ValueError:
            pass

        self.draft_time_remaining = PICK_TIMEOUT_SECONDS
        if self.draft_timer_task:
            self.draft_timer_task.cancel()
            self.draft_timer_task = None

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        await self.draft_next_player()

    async def draft_next_player(self):
        if self.is_setup_cancelled():
            self.draft_finished = True
            return
        if self._picks_exhausted():
            await self.finalize_draft()
            return
        await self.send_current_draft_view()

    def start_draft_timer(self):
        if self.draft_timer_task:
            self.draft_timer_task.cancel()
        self.draft_timer_task = asyncio.create_task(self.draft_timeout_timer())

    def cancel_draft_timer(self):
        if self.draft_timer_task:
            self.draft_timer_task.cancel()
            self.draft_timer_task = None

    def _current_captain_name(self, tracker_link_name: bool = False) -> str | None:
        current_captain_id = str(self.pick_order[self.pick_count]["id"])
        ud = users.find_one({"discord_id": current_captain_id})
        if ud:
            if tracker_link_name:
                return tracker_link(ud.get("name", "Unknown"), ud.get("tag", "Unknown"))
            return f"{ud.get('name', 'Unknown')}#{ud.get('tag', 'Unknown')}"
        captain1 = getattr(self.bot, "captain1", None)
        captain2 = getattr(self.bot, "captain2", None)
        if captain1 and str(captain1.get("id")) == current_captain_id:
            c = captain1
        elif captain2:
            c = captain2
        else:
            return None
        return c["name"]

    async def draft_timeout_timer(self):
        loop = asyncio.get_event_loop()
        start_time = loop.time()
        last_shown = PICK_TIMEOUT_SECONDS
        while not self.draft_finished:
            # If the match setup was cancelled externally (e.g. !cancel
            # mid-draft), stop the timer quietly.
            if self.is_setup_cancelled():
                self.draft_finished = True
                return
            remaining = PICK_TIMEOUT_SECONDS - (loop.time() - start_time)
            if remaining <= 0:
                break
            display_time = math.ceil(remaining)
            if display_time != last_shown:
                last_shown = display_time
                self.draft_time_remaining = display_time
                curr_captain_name = self._current_captain_name(tracker_link_name=True)
                if curr_captain_name is None:
                    # Captain state was cleared — stop.
                    self.draft_finished = True
                    return
                if self.captain_pick_message:
                    message = f"**{curr_captain_name}**, pick a player: ({self.draft_time_remaining}s)"
                    try:
                        await self.captain_pick_message.edit(content=message, view=self)
                    except discord.NotFound:
                        pass
            await asyncio.sleep(min(1.0, remaining))
        if self.draft_finished:
            return
        if self.is_setup_cancelled():
            self.draft_finished = True
            return
        self.draft_time_remaining = 0
        curr_captain_name = self._current_captain_name(tracker_link_name=True)
        if curr_captain_name is None:
            # Captain state was cleared — stop.
            self.draft_finished = True
            return
        if self.captain_pick_message:
            try:
                await self.captain_pick_message.edit(
                    content=f"**{curr_captain_name}**, pick a player: (0s)", view=self
                )
            except discord.NotFound:
                pass
        await self._cancel_match_on_timeout(curr_captain_name)

    async def _cancel_match_on_timeout(self, captain_name: str):
        if self.is_setup_cancelled():
            # Someone already cancelled (e.g. !cancel); do not touch state again.
            self.draft_finished = True
            return

        self.draft_finished = True

        try:
            await self.ctx.send(
                f"{captain_name} took too long. Match will be cancelled..."
            )
        except (discord.NotFound, discord.HTTPException):
            pass
        await asyncio.sleep(2)

        # Invalidate this setup cycle, reset shared state, then clean up
        # channel/role centrally
        self.bot.setup_generation += 1
        self.bot.signup_active = False
        self.bot.match_ongoing = False
        self.bot.match_not_reported = False
        if self.bot.queue:
            remember_recent_queue(self.bot.queue)
        self.bot.queue.clear()
        self.bot.team1 = []
        self.bot.team2 = []
        self.bot.captain1 = None
        self.bot.captain2 = None
        self.bot.chosen_mode = None
        self.bot.selected_map = None

        await cleanup_match_resources(self.bot)

        try:
            self.stop()
        except Exception:
            pass

    def is_setup_cancelled(self) -> bool:
        """Whether this setup cycle was cancelled (e.g. by !cancel)."""
        return self.bot.setup_generation != self.setup_generation

    def _player_mmr(self, player) -> int:
        return self.bot.player_mmr.get(str(player["id"]), {}).get("mmr", 1000)

    async def send_current_draft_view(self):
        if self.draft_finished:
            return

        # If the match setup was cancelled externally (e.g. !cancel mid-draft),
        # stop quietly.
        if self.is_setup_cancelled():
            self.draft_finished = True
            try:
                self.player_select.disabled = True
            except Exception:
                pass
            try:
                self.stop()
            except Exception:
                pass
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
            label = f"{label} (MMR: {self._player_mmr(player)})"
            options.append(discord.SelectOption(label=label, value=str(player["id"])))
        self.player_select.options = options

        # Remaining players embed

        remaining_players_lines = []
        for player in self.remaining_players:
            user_data = users.find_one({"discord_id": str(player["id"])})
            mmr = self._player_mmr(player)
            if user_data:
                remaining_players_lines.append(
                    f"{tracker_link(user_data.get('name', 'Unknown'), user_data.get('tag', 'Unknown'))} (MMR: {mmr})"
                )
            else:
                remaining_players_lines.append(f"{player['name']} (MMR: {mmr})")

        if remaining_players_lines:
            remaining_players_text = "\n".join(remaining_players_lines)
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
                mmr = self._player_mmr(p)
                if ud:
                    out.append(
                        f"{tracker_link(ud.get('name', 'Unknown'), ud.get('tag', 'Unknown'))} (MMR: {mmr})"
                    )
                else:
                    out.append(f"{p['name']} (MMR: {mmr})")
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
        curr_captain_name = self._current_captain_name(tracker_link_name=True)
        if curr_captain_name is None:
            # Captain state was cleared (e.g. by !cancel) — stop the draft quietly.
            self.draft_finished = True
            try:
                self.player_select.disabled = True
            except Exception:
                pass
            try:
                self.stop()
            except Exception:
                pass
            return

        message = (
            f"**{curr_captain_name}**, pick a player: ({self.draft_time_remaining}s)"
        )

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

        self.start_draft_timer()

        if not self.draft_finished:
            # If only one player left, auto-assign and finalize
            if len(self.remaining_players) == 1:
                player_dict = self.remaining_players[0]
                captain1 = getattr(self.bot, "captain1", None)
                if captain1 and str(current_captain_id) == str(captain1["id"]):
                    self.bot.team1.append(player_dict)
                else:
                    self.bot.team2.append(player_dict)
                self.pick_count += 1
                self.remaining_players.clear()
                await self.finalize_draft()
                return
