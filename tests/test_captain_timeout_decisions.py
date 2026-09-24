import asyncio
import discord
import logging
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub modules with import-time side effects (Mongo connection) so this
# self-check can run without a database or API access.
_database_stub = types.ModuleType("database")
_database_stub.users = types.SimpleNamespace(find_one=lambda *a, **k: None)
_database_stub.mmr_collection = types.SimpleNamespace()
_database_stub.seasons = types.SimpleNamespace()
_database_stub.all_matches = types.SimpleNamespace()
_database_stub.recent_queue = types.SimpleNamespace()
_database_stub.coin_escrow = types.SimpleNamespace(
    update_one=lambda *a, **k: None, find_one=lambda *a, **k: None
)
sys.modules["database"] = _database_stub

_maps_stub = types.ModuleType("services.maps_service")
_maps_stub.get_competitive_maps = lambda: ["Ascent", "Bind"]
_maps_stub.get_standard_maps = lambda: ["Ascent", "Bind"]
sys.modules["services.maps_service"] = _maps_stub

from views.captains_drafting_view import CaptainsDraftingView, SecondCaptainChoiceView


class FakeBot:
    def __init__(self):
        self.setup_generation = 1
        self.queue = [{"id": str(i), "name": f"p{i}"} for i in range(10)]
        self.signup_active = True
        self.match_ongoing = False
        self.match_not_reported = False
        self.chosen_mode = "Captains"
        self.selected_map = "Ascent"
        self.team1 = []
        self.team2 = []
        self.captain1 = self.queue[0]
        self.captain2 = self.queue[1]
        self.player_mmr = {}
        self.player_names = {}
        self.match_channel = None
        self.match_role = None
        self.match_name = "test"
        self.current_signup_message = None


class FakeCtx:
    def __init__(self):
        self.messages = []
        # (content, kwargs) pairs, so tests can assert on view= sends.
        self.sent = []
        self.embeds = []
        self.guild = None
        self.channel = self

    async def send(self, content=None, **kwargs):
        self.messages.append(content)
        self.sent.append((content, kwargs))
        if kwargs.get("embed") is not None:
            self.embeds.append(kwargs["embed"])
        return types.SimpleNamespace(
            edit=types.MethodType(
                lambda self, **kw: asyncio.sleep(0), types.SimpleNamespace()
            ),
            delete=types.MethodType(
                lambda self: asyncio.sleep(0), types.SimpleNamespace()
            ),
        )


def make_draft_view(bot, single_pick=True):
    ctx = FakeCtx()
    view = CaptainsDraftingView(ctx, bot, single_pick)
    return ctx, view


async def demo():
    # --- SecondCaptainChoiceView: timeout makes a random decision ---
    bot = FakeBot()
    ctx = FakeCtx()
    choice_view = SecondCaptainChoiceView(ctx, bot)
    choice_view.view_message = None
    # Cancel the real 120s timer; we drive the post-timeout tail directly.
    choice_view.cancel_timeout_timer()
    choice_view.decision_finished = False

    # Simulate the post-timeout tail by invoking the random decision the
    # same way timeout_timer does (via start_draft).
    choice_view.decision_finished = True
    single_pick = True  # would come from random.choice
    await choice_view.start_draft(single_pick)

    assert any(
        "chosen! Starting draft phase..." in (m or "") for m in ctx.messages
    ), "timeout did not start the draft"
    # No match-cancel state teardown should have happened.
    assert bot.setup_generation == 1, "timeout cancelled the match instead of deciding"
    assert bot.signup_active, "timeout cancelled the match instead of deciding"
    assert bot.captain1 is not None and bot.captain2 is not None

    # --- CaptainsDraftingView: pick timeout auto-picks a random player ---
    bot = FakeBot()
    ctx, view = make_draft_view(bot, single_pick=True)
    assert len(view.remaining_players) == 8

    # Auto-pick for captain2 (first turn in single_pick order).
    captain_name = view._current_captain_name()
    await view._auto_pick_on_timeout(captain_name)

    assert any(
        "Randomly selected" in (m or "") for m in ctx.messages
    ), "no random-decision message was sent"
    assert view.pick_count == 1, "auto-pick did not advance the draft"
    assert not view.draft_finished, "draft should continue after an auto-pick"
    picked = bot.team2[-1] if len(bot.team2) > 1 else None
    assert picked is not None and picked["id"] not in (
        "0",
        "1",
    ), "auto-pick picked a captain"

    # Auto-pick until the draft exhausts: teams must fill to 5/5.
    for _ in range(len(view.pick_order) + 4):
        if view.draft_finished:
            break
        await view._auto_pick_on_timeout(view._current_captain_name() or "x")
    assert view.draft_finished, "draft did not finish after repeated auto-picks"
    assert (
        len(bot.team1) == 5 and len(bot.team2) == 5
    ), f"teams not full: {len(bot.team1)}/{len(bot.team2)}"
    assert len(view.remaining_players) == 0

    # --- Manual pick is rejected while an auto-pick is committing ---
    bot = FakeBot()
    ctx, view = make_draft_view(bot, single_pick=True)
    view.auto_pick_in_progress = True

    sent = []

    async def fake_send(*args, **kwargs):
        sent.append(args)

    interaction = types.SimpleNamespace(
        response=types.SimpleNamespace(send_message=fake_send),
        user=types.SimpleNamespace(id="2"),
        message=None,
    )
    view.player_select = types.SimpleNamespace(values=["2"])
    await view.select_callback(interaction)
    assert sent, "racing manual pick was not rejected during auto-pick"
    assert view.pick_count == 0, "racing manual pick mutated draft state"

    # --- Empty pool: auto-pick must finalize, not silently stop ---
    bot = FakeBot()
    ctx, view = make_draft_view(bot, single_pick=True)
    view.remaining_players.clear()
    await view._auto_pick_on_timeout("someone")
    assert view.draft_finished, "empty-pool auto-pick did not finish the draft"
    assert (
        bot.match_ongoing and bot.match_not_reported
    ), "empty-pool auto-pick skipped finalize_draft (teams never announced)"

    # --- Issue #235: the match flags flip before the teams embed is sent ---
    # The powerup notice opens as soon as teams are announced; /doubledown is
    # gated on match_ongoing, so that flag must be live before (not ~15s of
    # voice moves after) the announcement.
    bot = FakeBot()
    ctx, view = make_draft_view(bot, single_pick=True)
    view.remaining_players.clear()
    flags_during_announce = []
    _real_send = ctx.send

    async def flag_check_send(content=None, **kwargs):
        flags_during_announce.append((bot.match_ongoing, bot.match_not_reported))
        await _real_send(content, **kwargs)

    ctx.send = flag_check_send
    await view._auto_pick_on_timeout("someone")
    assert any(
        ongoing and reported for ongoing, reported in flags_during_announce
    ), f"match flags must be live by the time the teams embed is sent: {flags_during_announce}"

    # --- Issue #212: draft surfaces show rank mentions, never raw MMR -----
    bot = FakeBot()
    ctx, view = make_draft_view(bot, single_pick=True)
    bot.player_mmr = {
        "0": {"mmr": 150, "matches_played": 3, "wins": 2, "losses": 1},
        "1": {"mmr": 0, "matches_played": 0, "wins": 0, "losses": 0},
        "2": {"mmr": 500, "matches_played": 9, "wins": 6, "losses": 3},
        "3": {"mmr": 0, "matches_played": 0, "wins": 0, "losses": 0},
    }
    remaining = [p for p in view.remaining_players]
    assert {p["id"] for p in remaining} >= {"2", "3"}
    await view.send_current_draft_view()

    remaining_embed = next(
        (e for e in ctx.embeds if e.title == "Remaining Players"), None
    )
    assert remaining_embed is not None, [e.title for e in ctx.embeds]
    assert "MMR" not in remaining_embed.description, remaining_embed.description
    assert (
        "@Duck-Master Rank" in remaining_embed.description
    ), remaining_embed.description
    assert "Unranked" in remaining_embed.description, remaining_embed.description

    drafting_embed = next((e for e in ctx.embeds if e.title == "Current Draft"), None)
    assert drafting_embed is not None
    team_values = "\n".join(f.value for f in drafting_embed.fields)
    assert "MMR" not in team_values, team_values
    assert "@Stone Rank" in team_values, team_values
    assert "Unranked" in team_values, team_values

    # The pick dropdown labels carry the same rank text, but as plain text:
    # select labels cannot render a mention pill, so a raw <@&id> would leak.
    labels = [o.label for o in view.player_select.options]
    assert any("Duck-Master Rank" in l for l in labels), labels
    assert any("Unranked" in l for l in labels), labels
    assert not any("MMR" in l for l in labels), labels
    assert not any("<@&" in l for l in labels), labels

    # The finalized teams embed (finalize_draft) shows ranks too.
    await view.finalize_draft()
    teams_embed = next(
        (e for e in ctx.embeds if (e.title or "").startswith("Teams on ")), None
    )
    assert teams_embed is not None, [e.title for e in ctx.embeds]
    final_values = "\n".join(f.value for f in teams_embed.fields)
    assert "MMR" not in final_values, final_values
    assert (
        "@Stone Rank" in final_values or "@Duck-Master Rank" in final_values
    ), final_values
    assert "Unranked" in final_values, final_values

    # --- !cancel still works mid-draft (setup generation bumped) ---
    bot = FakeBot()
    ctx, view = make_draft_view(bot, single_pick=True)
    bot.setup_generation += 1
    await view._auto_pick_on_timeout("someone")
    assert (
        view.draft_finished and len(bot.team1) + len(bot.team2) == 2
    ), "cancelled setup should not auto-pick"

    # --- Last-player shortcut routes by the live turn, not the captured one ---
    # Regression for the 4/6 draft: while send_current_draft_view is suspended
    # on its message edits, the current captain can commit a pick from the
    # still-attached (stale) menu as an independent task. pick_count then
    # advances, and the last-player shortcut must assign the final player to
    # the *current* turn's captain (5/5), not the stale captured captain (4/6).
    bot = FakeBot()
    ctx, view = make_draft_view(bot, single_pick=False)
    pool = list(view.remaining_players)  # ids "2".."9"
    view.pick_count = 6  # double-pick turn 6 belongs to captain2
    bot.team1 = [bot.captain1] + pool[0:3]
    bot.team2 = [bot.captain2] + pool[3:6]
    view.remaining_players = pool[6:8]
    assert len(bot.team1) == 4 and len(bot.team2) == 4

    raced = {"fired": False}

    async def racing_pick():
        async def fake_send(*args, **kwargs):
            pass

        async def fake_defer(**kwargs):
            await asyncio.sleep(0)

        interaction = types.SimpleNamespace(
            response=types.SimpleNamespace(
                is_done=lambda: False, defer=fake_defer, send_message=fake_send
            ),
            user=types.SimpleNamespace(id="1"),  # captain2
            message=None,
        )
        view.player_select = types.SimpleNamespace(values=[pool[6]["id"]])
        await view.select_callback(interaction)

    class RacingMessage:
        async def edit(self, **kwargs):
            if not raced["fired"]:
                raced["fired"] = True
                # discord.py dispatches the button/select interaction as its
                # own task, so it runs concurrently with this suspended render.
                asyncio.get_event_loop().create_task(racing_pick())
            await asyncio.sleep(0)

        async def delete(self):
            await asyncio.sleep(0)

    view.remaining_players_message = RacingMessage()
    view.drafting_message = RacingMessage()
    view.captain_pick_message = RacingMessage()

    warnings = []

    class CaptureWarnings(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.WARNING:
                warnings.append(record.getMessage())

    draft_log = logging.getLogger("views.captains_drafting_view")
    handler = CaptureWarnings()
    draft_log.addHandler(handler)
    try:
        await view.send_current_draft_view()
    finally:
        draft_log.removeHandler(handler)

    assert view.draft_finished, "stale-render race left the draft unfinished"
    assert (
        len(bot.team1) == 5 and len(bot.team2) == 5
    ), f"stale-render race misrouted the last pick: {len(bot.team1)}/{len(bot.team2)}"
    assert not any(
        "unbalanced" in message for message in warnings
    ), f"shortcut misrouted the last pick; only the safety net fixed it: {warnings}"

    # --- Draft UI is not recreated after a racing pick finalized the draft ---
    # Regression for the stranded draft view: while send_current_draft_view is
    # suspended on its message edits, a racing pick commits the final pick and
    # finalizes (finalize_draft deletes the draft messages and posts the teams
    # embed). The resumed render's edits then raise NotFound; recreating the
    # UI there left a draft view (showing the last unpicked player) in the
    # match-# channel with a live select menu, a fresh 120s timer, and
    # nothing left to clean it up.
    bot = FakeBot()
    ctx, view = make_draft_view(bot, single_pick=False)
    pool = list(view.remaining_players)  # ids "2".."9"
    view.pick_count = 7  # final double-pick turn belongs to captain1
    bot.team1 = [bot.captain1] + pool[0:3]
    bot.team2 = [bot.captain2] + pool[3:6]
    view.remaining_players = pool[6:8]

    async def racing_finalize():
        # Mirror select_callback's tail: commit the final pick, then let
        # draft_next_player discover the draft is over and finalize.
        player_dict = pool[6]
        bot.team1.append(player_dict)
        view.pick_count += 1
        view.remaining_players.remove(player_dict)
        await view.draft_next_player()

    def _not_found():
        response = types.SimpleNamespace(status=404, reason="Not Found")
        return discord.NotFound(response, "Unknown Message")

    class RaceThenNotFoundMessage:
        """edit() runs the racing finalize first, then raises NotFound —
        exactly the state of a real render suspended on an edit while a
        concurrent interaction finalized and deleted the messages."""

        def __init__(self):
            self.raced = False

        async def edit(self, **kwargs):
            if not self.raced:
                self.raced = True
                await racing_finalize()
            raise _not_found()

        async def delete(self):
            await asyncio.sleep(0)

    view.remaining_players_message = RaceThenNotFoundMessage()
    view.drafting_message = RaceThenNotFoundMessage()
    view.captain_pick_message = RaceThenNotFoundMessage()

    await view.send_current_draft_view()

    assert view.draft_finished, "raced render did not see the finished draft"
    assert (
        len(bot.team1) == 5 and len(bot.team2) == 5
    ), f"raced render broke team balance: {len(bot.team1)}/{len(bot.team2)}"
    assert (
        view.remaining_players_message is None
        and view.drafting_message is None
        and view.captain_pick_message is None
    ), "finalize's message references were resurrected after the race"
    assert not any(
        "view" in kwargs for _, kwargs in ctx.sent
    ), "stranded draft view was recreated after the teams embed was sent"

    # --- Last-player shortcut survives a turn exhausted by a racing pick ---
    # A racing pick can commit the final turn while the render is suspended
    # on its edits without finalizing (as if suspended mid-select_callback).
    # pick_count is then past the end of pick_order; the shortcut must
    # finalize instead of raising IndexError and leaving the draft view
    # (and its just-armed timer) behind.
    bot = FakeBot()
    ctx, view = make_draft_view(bot, single_pick=False)
    pool = list(view.remaining_players)
    view.pick_count = 7
    bot.team1 = [bot.captain1] + pool[0:3]
    bot.team2 = [bot.captain2] + pool[3:6]
    view.remaining_players = pool[6:8]

    class CommitOnEditMessage:
        """First edit (any of the three) commits the final pick without
        finalizing — mimicking a suspended render racing a manual pick."""

        def __init__(self, state):
            self.state = state

        async def edit(self, **kwargs):
            if not self.state["committed"]:
                self.state["committed"] = True
                bot.team1.append(pool[6])
                view.pick_count += 1
                view.remaining_players.remove(pool[6])
            await asyncio.sleep(0)

        async def delete(self):
            await asyncio.sleep(0)

    shared = {"committed": False}
    view.remaining_players_message = CommitOnEditMessage(shared)
    view.drafting_message = CommitOnEditMessage(shared)
    view.captain_pick_message = CommitOnEditMessage(shared)

    await view.send_current_draft_view()

    assert view.draft_finished, "render crashed instead of finalizing"
    assert (
        len(bot.team1) == 5 and len(bot.team2) == 5
    ), f"unbalanced teams after shortcut finalize: {len(bot.team1)}/{len(bot.team2)}"
    assert view.draft_timer_task is None, "draft timer was left armed after finalize"

    print("all captain-timeout auto-decide self-checks passed")


if __name__ == "__main__":
    asyncio.run(demo())
