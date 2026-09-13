import asyncio
import os
import sys
import types

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

# Stub modules with import-time side effects (Mongo connection, map pools)
# so this self-check can run without a database or API access.
_database_stub = types.ModuleType("database")
_database_stub.users = types.SimpleNamespace(find_one=lambda *a, **k: None)
_database_stub.mmr_collection = types.SimpleNamespace()
_database_stub.seasons = types.SimpleNamespace()
_database_stub.all_matches = types.SimpleNamespace()
_database_stub.recent_queue = types.SimpleNamespace()
sys.modules["database"] = _database_stub

_maps_stub = types.ModuleType("maps_service")
_maps_stub.get_competitive_maps = lambda: ["Ascent", "Bind", "Haven", "Split"]
_maps_stub.get_standard_maps = lambda: ["Ascent", "Bind", "Haven", "Split"]
sys.modules["maps_service"] = _maps_stub

from views.map_type_vote_view import MapTypeVoteView
from views.map_vote_view import MapVoteView
from views.mode_vote_view import ModeVoteView


class FakeBot:
    def __init__(self):
        self.setup_generation = 1
        self.queue = [{"id": str(i), "name": f"p{i}"} for i in range(10)]
        self.signup_active = False
        self.match_ongoing = False
        self.match_not_reported = False
        self.chosen_mode = None
        self.selected_map = None
        self.team1 = []
        self.team2 = []
        self.captain1 = None
        self.captain2 = None
        self.player_mmr = {}
        self.match_channel = None
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
            )
        )


def all_voted(view):
    view.voters = {p["id"] for p in view.bot.queue}


async def run_mode_vote(votes, voters):
    ctx = FakeCtx()
    bot = FakeBot()
    view = ModeVoteView(ctx, bot)
    view.view_message = await ctx.send("vote")
    view.votes.update(votes)
    view.voters = set(voters)
    await view.check_for_winner()
    ended = view.voting_phase_ended
    mode = bot.chosen_mode
    view.cancel_interaction_queue_task()
    view.cancel_timeout_timer()
    return ctx, ended, mode


async def run_map_type_vote(votes, voters):
    ctx = FakeCtx()
    bot = FakeBot()
    view = MapTypeVoteView(ctx, bot)
    view.view_message = await ctx.send("vote")
    view.map_pool_votes.update(votes)
    view.voters = set(voters)
    await view.check_for_winner()
    ended = view.voting_phase_ended
    view.cancel_interaction_queue_task()
    view.cancel_timeout_timer()
    return ctx, ended


async def run_map_vote(votes, voters):
    ctx = FakeCtx()
    bot = FakeBot()
    bot.chosen_mode = "Balanced"
    view = MapVoteView(ctx, bot, ["Ascent", "Bind", "Haven"])
    view.view_message = await ctx.send("vote")
    view.map_votes.update(votes)
    view.voters = set(voters)
    await view.check_for_winner()
    ended = view.voting_phase_ended
    chosen = bot.selected_map
    view.cancel_interaction_queue_task()
    view.cancel_timeout_timer()
    return ctx, ended, chosen


async def demo():
    # Majority (>5) still ends the vote early.
    ctx, ended, mode = await run_mode_vote(
        {"Balanced": 6, "Captains": 0}, ["0", "1", "2", "3", "4", "5"]
    )
    assert ended and mode == "Balanced", "majority path broken"
    assert any("majority" in (m or "") for m in ctx.messages)

    # Partial voting with no majority must NOT end the vote.
    ctx, ended, mode = await run_mode_vote(
        {"Balanced": 3, "Captains": 2}, ["0", "1", "2", "3", "4"]
    )
    assert not ended, "vote ended early without majority or full participation"

    # Everyone voted with a 5-5 split: end immediately via coin flip.
    ctx, ended, mode = await run_mode_vote(
        {"Balanced": 5, "Captains": 5}, [p["id"] for p in FakeBot().queue]
    )
    assert ended and mode in ("Balanced", "Captains"), "all-voted tie did not end vote"
    assert any("Tie" in (m or "") for m in ctx.messages)

    # Everyone voted 6-4: strict majority path still wins with its own wording.
    ctx, ended, mode = await run_mode_vote(
        {"Balanced": 6, "Captains": 4}, [p["id"] for p in FakeBot().queue]
    )
    assert ended and mode == "Balanced", "all-voted winner did not end vote"
    assert any("majority" in (m or "") for m in ctx.messages)

    # Map type vote: everyone voted, split 5-5.
    ctx, ended = await run_map_type_vote(
        {"Competitive": 5, "All": 5}, [p["id"] for p in FakeBot().queue]
    )
    assert ended, "map type all-voted tie did not end vote"

    # Map vote: everyone voted, 4-3-3 (no majority) must still end, and the
    # winner message says "final vote" rather than "timeout".
    ctx, ended, chosen = await run_map_vote(
        {"Ascent": 4, "Bind": 3, "Haven": 3}, [p["id"] for p in FakeBot().queue]
    )
    assert ended and chosen == "Ascent", "map all-voted lead did not end vote"
    assert any("final vote" in (m or "") for m in ctx.messages)
    assert not any("timeout" in (m or "") for m in ctx.messages)

    # Map vote: everyone voted, three-way tie picks one randomly.
    ctx, ended, chosen = await run_map_vote(
        {"Ascent": 3, "Bind": 3, "Haven": 4}, [p["id"] for p in FakeBot().queue]
    )
    assert ended and chosen in ("Ascent", "Bind", "Haven")

    print("all vote skip-wait self-checks passed")


if __name__ == "__main__":
    asyncio.run(demo())
