import asyncio
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

    async def send(self, content=None, **kwargs):
        self.messages.append(content)
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

    # --- !cancel still works mid-draft (setup generation bumped) ---
    bot = FakeBot()
    ctx, view = make_draft_view(bot, single_pick=True)
    bot.setup_generation += 1
    await view._auto_pick_on_timeout("someone")
    assert (
        view.draft_finished and len(bot.team1) + len(bot.team2) == 2
    ), "cancelled setup should not auto-pick"

    print("all captain-timeout auto-decide self-checks passed")


if __name__ == "__main__":
    asyncio.run(demo())
