import asyncio
import logging
import math
import random

import discord
from discord.ui import Select

from database import users
from game.ranks import display_rank_for
from game.stats_helper import DEFAULT_MMR
from tracker_links import display_line_for, display_name_for
from game.voice_presence import move_teams_to_voice, voice_presence_enabled

log = logging.getLogger(__name__)

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
        # Set once the draft actually starts. Guards against the timeout tail
        # and a captain's late click racing each other into two drafts.
        self._draft_started = False

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

        log.info("Draft type chosen by %s: First Pick", interaction.user)
        self.cancel_timeout_timer()
        self.decision_finished = True

        # Acknowledge the interaction FIRST. Discord must receive a response
        # within 3 seconds; message edits can be slow and were previously
        # burning that window, causing 404 Unknown interaction (10062).
        if interaction.response.is_done():
            await interaction.followup.send("First pick selected!", ephemeral=True)
        else:
            await interaction.response.send_message(
                "First pick selected!", ephemeral=True
            )

        self.first_pick_button.disabled = True
        self.double_pick_button.disabled = True
        try:
            await interaction.message.edit(view=self)
        except (discord.NotFound, discord.HTTPException):
            log.warning("Could not disable draft-type buttons (message gone)")

        await self.start_draft(single_pick=True)

    async def double_pick_callback(self, interaction: discord.Interaction):
        if not await self._validate_second_captain(interaction):
            return

        log.info("Draft type chosen by %s: 2nd + 3rd Pick", interaction.user)
        self.cancel_timeout_timer()
        self.decision_finished = True

        # Acknowledge the interaction FIRST (see first_pick_callback).
        if interaction.response.is_done():
            await interaction.followup.send("2nd + 3rd pick selected!", ephemeral=True)
        else:
            await interaction.response.send_message(
                "2nd + 3rd pick selected!", ephemeral=True
            )

        self.first_pick_button.disabled = True
        self.double_pick_button.disabled = True
        try:
            await interaction.message.edit(view=self)
        except (discord.NotFound, discord.HTTPException):
            log.warning("Could not disable draft-type buttons (message gone)")

        await self.start_draft(single_pick=False)

    async def start_draft(self, single_pick: bool):
        # Only the first caller may start the draft: a captain clicking at the
        # same moment the timeout tail fires would otherwise create two drafts.
        if self._draft_started:
            return
        self._draft_started = True
        # Only proceed if this setup cycle is still the current one.
        if self.is_setup_cancelled():
            return
        mode_name = "Single Pick" if single_pick else "Double Pick"
        log.info("Starting captains draft (%s)", mode_name)
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
            # Disable the buttons so a late click can't race the timeout tail.
            for child in self.children:
                child.disabled = True
            captain2 = getattr(self.bot, "captain2", None)
            if captain2:
                try:
                    await self.view_message.edit(
                        content=f"<@{captain2['id']}>, choose draft type: (0s)",
                        view=self,
                    )
                except discord.NotFound:
                    pass
        # Randomly decide the draft type instead of cancelling the match.
        self.decision_finished = True
        single_pick = random.choice([True, False])
        log.warning(
            "Second captain did not choose a draft type in time; randomly selected %s",
            "First Pick" if single_pick else "2nd + 3rd Pick",
        )
        try:
            await self.ctx.send(
                "The captain took too long to choose a draft type. "
                f"Randomly selected: **{'First Pick' if single_pick else '2nd + 3rd Pick'}**"
            )
        except (discord.NotFound, discord.HTTPException):
            pass
        await self.start_draft(single_pick)

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
        # Set while an auto-pick on timeout is committing; guards against a
        # captain's manual pick racing it (both mutate the same state).
        self.auto_pick_in_progress = False

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
        # Plain text, not links: embed field names don't render markdown links.
        self.captain1_name = (
            f"{c1_data.get('name', 'Unknown')}#{c1_data.get('tag', 'Unknown')}"
            if c1_data
            else self.bot.captain1["name"]
        )
        self.captain2_name = (
            f"{c2_data.get('name', 'Unknown')}#{c2_data.get('tag', 'Unknown')}"
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

    async def finalize_draft(self):
        """Finalize teams, cleanup UI, announce result."""
        if self.draft_finished:
            return
        self.draft_finished = True

        # If the match setup was cancelled externally (e.g. !cancel), skip
        # finalization entirely.
        if self.is_setup_cancelled():
            log.info("Draft finalization skipped: match setup was cancelled")
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

        # Hard guarantee: never announce unbalanced teams. A one-player gap is
        # legitimate when the queue lost someone mid-setup; anything larger
        # means a pick went to the wrong side, so move the latest picks back.
        while len(self.bot.team1) > len(self.bot.team2) + 1:
            self.bot.team2.append(self.bot.team1.pop())
            log.warning(
                "Draft teams were unbalanced; moved %s to Defenders (now %s/%s)",
                self.bot.team2[-1].get("name"),
                len(self.bot.team1),
                len(self.bot.team2),
            )
        while len(self.bot.team2) > len(self.bot.team1) + 1:
            self.bot.team1.append(self.bot.team2.pop())
            log.warning(
                "Draft teams were unbalanced; moved %s to Attackers (now %s/%s)",
                self.bot.team1[-1].get("name"),
                len(self.bot.team1),
                len(self.bot.team2),
            )

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
                except (discord.HTTPException, AttributeError):
                    # Deletion failed (e.g. missing permissions); at least
                    # detach the live view so the dropdown stops working.
                    try:
                        await msg.edit(view=None)
                    except (
                        discord.NotFound,
                        discord.HTTPException,
                        AttributeError,
                    ):
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
            attackers.append(
                f"{display_line_for(ud, guild=self.ctx.guild, discord_id=str(p['id']))} ({self._player_rank(p)})"
            )

        defenders = []
        for p in self.bot.team2:
            ud = users.find_one({"discord_id": str(p["id"])})
            defenders.append(
                f"{display_line_for(ud, guild=self.ctx.guild, discord_id=str(p['id']))} ({self._player_rank(p)})"
            )

        teams_embed.add_field(
            name="**Attackers:**", value="\n".join(attackers) or "—", inline=False
        )
        teams_embed.add_field(
            name="**Defenders:**", value="\n".join(defenders) or "—", inline=False
        )

        log.info(
            "Captains draft finalized: Attackers=%s Defenders=%s",
            [p.get("name") for p in self.bot.team1],
            [p.get("name") for p in self.bot.team2],
        )

        # Flip the match flags BEFORE announcing teams (issue #235): the
        # powerup notice opens its countdown right away, and the slow voice
        # moves below must not leave /doubledown answering "no match is
        # running" while that notice is already live. The cancellation check
        # above guarantees a superseded cycle never reaches these writes.
        self.bot.match_ongoing = True
        self.bot.match_not_reported = True

        self.bot.current_teams_message = await self.ctx.send(embed=teams_embed)
        await self.ctx.send("Start match and use `/report` to finalize results.")

        from game.duck_coins import on_teams_announced, open_map_override_grace

        # Open the grace window first so the match-channel powerup countdown
        # reads the real deadline from its very first tick.
        open_map_override_grace(self.bot)
        await on_teams_announced(self.bot, self.ctx)

        if voice_presence_enabled() and self.ctx.guild:
            await move_teams_to_voice(self.ctx.guild, self.bot.team1, self.bot.team2)

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

        # The timeout path is committing a random pick; don't double-pick.
        if self.auto_pick_in_progress:
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
        captain1 = getattr(self.bot, "captain1", None)
        if current_captain_id == str(captain1["id"]):
            self.bot.team1.append(player_dict)
        else:
            self.bot.team2.append(player_dict)
        log.info("Draft pick by %s: %s", interaction.user, player_dict.get("name"))

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

    def _current_captain_name(self) -> str | None:
        if self.pick_count >= len(self.pick_order):
            # Pick order exhausted (a concurrent pick advanced the turn while
            # a stale timer fired) — no current captain to name; callers
            # treat None as "stop quietly".
            return None
        current_captain_id = str(self.pick_order[self.pick_count]["id"])
        ud = users.find_one({"discord_id": current_captain_id})
        if ud:
            # Plain text: these names go into embed field names, where
            # markdown links don't render.
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
                curr_captain_name = self._current_captain_name()
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
        curr_captain_name = self._current_captain_name()
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
        await self._auto_pick_on_timeout(curr_captain_name)

    async def _auto_pick_on_timeout(self, captain_name: str):
        """Make a random pick for the captain instead of cancelling the match."""
        if self.is_setup_cancelled():
            # Someone already cancelled (e.g. !cancel); do not touch state.
            self.draft_finished = True
            return

        self.auto_pick_in_progress = True
        try:
            if self.draft_finished or self.pick_count >= len(self.pick_order):
                # A manual pick raced the timer tail (advancing the turn past
                # the last entry, possibly already finalizing). Don't index
                # pick_order out of range: finalize distributes anything left.
                self.draft_timer_task = None
                await self.finalize_draft()
                return
            if not self.remaining_players:
                # We're inside the draft timer task; clear the reference so
                # finalize_draft doesn't cancel this coroutine mid-finalize.
                self.draft_timer_task = None
                await self.finalize_draft()
                return
            player_dict = random.choice(self.remaining_players)
            current_captain_id = str(self.pick_order[self.pick_count]["id"])
            captain1 = getattr(self.bot, "captain1", None)
            if captain1 and current_captain_id == str(captain1["id"]):
                self.bot.team1.append(player_dict)
            else:
                self.bot.team2.append(player_dict)
            log.warning(
                "Draft pick timeout for %s; randomly selected %s",
                captain_name,
                player_dict.get("name"),
            )

            self.pick_count += 1
            try:
                self.remaining_players.remove(player_dict)
            except ValueError:
                pass

            try:
                await self.ctx.send(
                    f"{captain_name} took too long to pick. Randomly selected: "
                    f"**{player_dict['name']}**"
                )
            except (discord.NotFound, discord.HTTPException):
                pass
        finally:
            self.auto_pick_in_progress = False

        self.draft_time_remaining = PICK_TIMEOUT_SECONDS
        # We are running inside the draft timer task itself; clear the
        # reference so start_draft_timer doesn't cancel this coroutine
        # mid-finalize (mirrors select_callback's cancel-and-clear).
        self.draft_timer_task = None
        await self.draft_next_player()

    def is_setup_cancelled(self) -> bool:
        """Whether this setup cycle was cancelled (e.g. by !cancel)."""
        return self.bot.setup_generation != self.setup_generation

    def _player_rank(self, player, *, mention: bool = True) -> str:
        """Rank-role mention for display, or plaintext 'Unranked' (issue #212)."""
        stats = self.bot.player_mmr.get(str(player["id"]), {})
        matches = stats.get("matches_played", 0)
        if not matches:
            matches = stats.get("wins", 0) + stats.get("losses", 0)
        return display_rank_for(
            self.ctx.guild,
            stats.get("mmr", DEFAULT_MMR),
            matches_played=matches,
            mention=mention,
        )

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
            if user_data and user_data.get("name") and user_data.get("tag"):
                label = f"{user_data.get('name', 'Unknown')}#{user_data.get('tag', 'Unknown')}"
            else:
                # Unlinked players: Discord display name (best effort).
                label = display_name_for(
                    user_data, guild=self.ctx.guild, discord_id=str(player["id"])
                )
            # Select labels are plain text: a mention would show as <@&id>.
            label = f"{label} ({self._player_rank(player, mention=False)})"
            options.append(discord.SelectOption(label=label, value=str(player["id"])))
        self.player_select.options = options

        # Remaining players embed

        remaining_players_lines = []
        for player in self.remaining_players:
            user_data = users.find_one({"discord_id": str(player["id"])})
            remaining_players_lines.append(
                f"{display_line_for(user_data, guild=self.ctx.guild, discord_id=str(player['id']))} ({self._player_rank(player)})"
            )

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
                out.append(
                    f"{display_line_for(ud, guild=self.ctx.guild, discord_id=str(p['id']))} ({self._player_rank(p)})"
                )
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
        curr_captain_name = self._current_captain_name()
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
                # A pick that raced these in-flight edits may have already
                # finalized the draft — finalize_draft deletes these very
                # messages, so NotFound often means the draft is over, not
                # that the messages moved. Recreating the UI there strands a
                # draft view (showing the last unpicked player) in the match
                # channel after the teams embed was sent, with nothing left
                # to clean it up.
                if self.draft_finished or self.is_setup_cancelled():
                    try:
                        self.stop()
                    except Exception:
                        pass
                    return
                # The draft is genuinely still live: delete whichever of the
                # previous messages still exist so the rebuild doesn't leave
                # orphaned duplicates behind.
                for stale in (
                    self.remaining_players_message,
                    self.drafting_message,
                    self.captain_pick_message,
                ):
                    if stale:
                        try:
                            await stale.delete()
                        except (discord.NotFound, discord.HTTPException):
                            pass
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

        # Arm the timer only if the draft is still live: a pick that
        # finalized while the edits above were suspended must not leave a
        # stray 120-second timer (and its auto-pick) running against a
        # finished draft.
        if self.draft_finished or self.is_setup_cancelled():
            return

        self.start_draft_timer()

        # If only one player left, auto-assign and finalize
        if len(self.remaining_players) == 1:
            if self.pick_count >= len(self.pick_order):
                # A racing pick already used the final turn while this render
                # was suspended on its edits. Finalize (its balancing step
                # distributes the leftover player) instead of indexing
                # pick_order out of range — that crash used to strand the
                # draft view (and its just-armed timer) in the channel.
                await self.finalize_draft()
                return
            # Re-read the turn: a stale-menu pick can commit while the
            # message edits above are suspended, advancing pick_count past
            # current_captain_id and misrouting the last pick (4/6 teams).
            current_captain_id = str(self.pick_order[self.pick_count]["id"])
            player_dict = self.remaining_players[0]
            captain1 = getattr(self.bot, "captain1", None)
            if captain1 and current_captain_id == str(captain1["id"]):
                self.bot.team1.append(player_dict)
            else:
                self.bot.team2.append(player_dict)
            self.pick_count += 1
            self.remaining_players.clear()
            await self.finalize_draft()
            return
