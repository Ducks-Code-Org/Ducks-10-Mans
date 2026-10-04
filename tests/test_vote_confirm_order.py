"""Self-checks for confirm-after-reflect on all setup vote views (issue #258).

Regression: vote handlers REST-edited the public board and webhook-replied
the ephemeral "Voted X." confirmation on two independent Discord paths, so
the confirmation could become visible before the board showed the vote —
and the 1-second countdown edits re-asserted the full view, repainting
stale tallies over votes that just landed. Now each vote click defers
component-style, updates the board THROUGH the same interaction
(reflect_board -> edit_original_response, one atomic write that completes
the ack), and only then sends the ephemeral confirmation; countdown edits
pass no view.
"""

import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
_maps_stub.get_competitive_maps = lambda: ["Ascent", "Bind", "Haven", "Split"]
_maps_stub.get_standard_maps = lambda: ["Ascent", "Bind", "Haven", "Split"]
sys.modules["services.maps_service"] = _maps_stub


class _FakeViewBase:
    def __init__(self, *a, **k):
        self.children = []

    def add_item(self, item):
        self.children.append(item)

    def stop(self):
        pass


class _FakeButton:
    def __init__(self, **k):
        self.label = k.get("label")
        self.style = k.get("style")
        self.custom_id = k.get("custom_id")
        self.disabled = False
        self.callback = None


class _FakeInteraction:
    """Records the order and arguments of every write the handler performs."""

    def __init__(self, user_id="0", queue_ids=None, fail_board=False):
        self.user = types.SimpleNamespace(id=user_id)
        self._is_done = False
        self.order = []  # ("defer"|"edit_original"|"followup"|"send_message", payload)
        self.fail_board = fail_board
        self.response = types.SimpleNamespace(
            is_done=lambda: self._is_done,
            defer=self._defer,
            send_message=self._send_message,
            edit_message=self._edit_message,
        )
        self.followup = types.SimpleNamespace(send=self._followup_send)

    async def _defer(self, **kw):
        self.order.append(("defer", kw))
        self._is_done = True

    async def _send_message(self, *a, **kw):
        self.order.append(("send_message", (a, kw)))
        self._is_done = True

    async def _edit_message(self, **kw):
        self.order.append(("edit_message", kw))

    async def edit_original_response(self, **kw):
        if self.fail_board:
            raise _discord_stub.HTTPException("board edit failed")
        self.order.append(("edit_original", kw))

    async def _followup_send(self, *a, **kw):
        self.order.append(("followup", (a, kw)))


class _BoardMessage:
    """The public board; records timer edits so we can catch view repainting."""

    def __init__(self):
        self.edits = []

    async def edit(self, **kw):
        self.edits.append(kw)


_discord_stub = types.ModuleType("discord")


_discord_stub.ui = types.SimpleNamespace()
_discord_stub.ui.View = type("View", (_FakeViewBase,), {})
_discord_stub.ui.Button = _FakeButton
_discord_stub.ui.Select = type("Select", (_FakeViewBase,), {})
_discord_stub.ui.select = lambda *a, **k: (lambda f: f)
_discord_stub.SelectOption = lambda *a, **k: types.SimpleNamespace(
    label=None, value=None
)
_discord_stub.ButtonStyle = types.SimpleNamespace(
    success=1, secondary=2, primary=3, green=1, blurple=4
)
_discord_stub.Embed = lambda *a, **k: types.SimpleNamespace(
    title=k.get("title"), description=k.get("description", ""), fields=[]
)
_discord_stub.Color = types.SimpleNamespace(
    blue=lambda: None, gold=lambda: None, green=lambda: None, blurple=lambda: None
)
_discord_stub.utils = types.SimpleNamespace(get=lambda *a, **k: None)
_discord_stub.Interaction = type("Interaction", (), {})
_discord_stub.NotFound = type("NotFound", (Exception,), {})
_discord_stub.HTTPException = type("HTTPException", (Exception,), {})
_discord_stub.errors = types.SimpleNamespace(NotFound=_discord_stub.NotFound)
_discord_stub.ext = types.SimpleNamespace()
_discord_stub.ext.commands = types.SimpleNamespace(
    command=lambda *a, **k: (lambda f: f),
    hybrid_command=lambda *a, **k: (lambda f: f),
    has_permissions=lambda **k: (lambda f: f),
    has_role=lambda *a, **k: (lambda f: f),
    Cog=type("Cog", (), {"__init_subclass__": classmethod(lambda cls, **kw: None)}),
)
_discord_stub.app_commands = types.SimpleNamespace(
    describe=lambda **k: (lambda f: f), Attachment=object
)
sys.modules["discord"] = _discord_stub
sys.modules["discord.ui"] = _discord_stub.ui
sys.modules["discord.ext"] = _discord_stub.ext
sys.modules["discord.ext.commands"] = _discord_stub.ext.commands

from views.captains_drafting_view import (  # noqa: E402
    SecondCaptainChoiceView,
)
from views.map_type_vote_view import MapTypeVoteView  # noqa: E402
from views.map_vote_view import MapVoteView  # noqa: E402
from views.mode_vote_view import ModeVoteView  # noqa: E402


class FakeBot:
    def __init__(self):
        self.setup_generation = 1
        self.queue = [{"id": str(i), "name": f"p{i}"} for i in range(3)]
        self.signup_active = False
        self.match_ongoing = False
        self.match_not_reported = False
        self.chosen_mode = None
        self.selected_map = None
        self.team1 = []
        self.team2 = []
        self.captain1 = {"id": "1", "name": "c1"}
        self.captain2 = {"id": "2", "name": "c2"}
        self.player_mmr = {}
        self.match_channel = None
        self.match_name = "test"
        self.current_signup_message = None


class FakeCtx:
    def __init__(self):
        self.messages = []
        self.embeds = []
        self.guild = None
        self.channel = self

    async def send(self, content=None, **kwargs):
        self.messages.append(content)
        if kwargs.get("embed") is not None:
            self.embeds.append(kwargs["embed"])
        return _BoardMessage()


def labels_of(view):
    return [b.label for b in view.children if hasattr(b, "label")]


def board_payload(interaction):
    """The view payload of the recorded edit_original_response call."""
    payloads = [kw for k, kw in interaction.order if k == "edit_original"]
    assert payloads, interaction.order
    return payloads[-1]


def followup_texts(interaction):
    return [payload[0][0] for k, payload in interaction.order if k == "followup"]


async def drive(view, interaction, *args):
    """Run one click through the view's real callback + queue machinery."""
    if args and hasattr(args[0], "callback"):
        button, rest = args[0], args[1:]
        await button.callback(interaction, *rest)
    elif args and not hasattr(view, "vote_callback"):
        callback, rest = args[0], args[1:]
        await callback(interaction, *rest)
    else:
        await view.vote_callback(interaction, *args)


def stop(view):
    view.cancel_interaction_queue_task()
    view.cancel_timeout_timer()


async def build_mode_vote():
    ctx = FakeCtx()
    bot = FakeBot()
    view = ModeVoteView(ctx, bot)
    view.view_message = await ctx.send("vote")
    return ctx, bot, view


async def drive_timer(view, module, patch_loop=False):
    """Run the view's real timer to completion with fast sleep.

    Returns the per-second tick edits (the close/0s tail edit legitimately
    passes the view, so it is excluded). The patched asyncio is restored in
    a finally so a failing assertion can't leak the fake into later checks.
    """
    real_asyncio = module.asyncio
    real_sleep = real_asyncio.sleep
    fake_loop = types.SimpleNamespace()
    step = [0.0]

    def fake_time():
        step[0] += 1.0
        return step[0]

    async def fast_sleep(_):
        await real_sleep(0)

    if patch_loop:
        module.asyncio = types.SimpleNamespace(
            sleep=fast_sleep, get_event_loop=lambda: fake_loop
        )
        fake_loop.time = fake_time
    else:
        module.asyncio = types.SimpleNamespace(
            sleep=fast_sleep, get_event_loop=real_asyncio.get_event_loop
        )
    try:
        await view.timeout_timer()
    finally:
        module.asyncio = real_asyncio
    return [
        e
        for e in view.view_message.edits
        if str(e.get("content", "")).endswith("s)") and "(0s)" not in e["content"]
    ]


async def noop(*a, **k):
    return None


async def demo():
    # --- Valid vote: exactly one board write, initiated BEFORE the
    # confirmation; the deferred component ack precedes both. The recorded
    # edit payload carries the new tally (not just in-memory state).
    ctx, bot, view = await build_mode_vote()
    inter = _FakeInteraction(user_id="0")
    await drive(view, inter, "Balanced")
    kinds = [k for k, _ in inter.order]
    assert kinds[0] == "defer", kinds
    assert kinds.count("edit_original") == 1, kinds
    assert kinds.index("edit_original") < kinds.index("followup"), kinds
    edited_view = board_payload(inter)["view"]
    assert "Balanced Teams (1)" in labels_of(edited_view), labels_of(edited_view)
    assert inter.order[-1][1][1].get("ephemeral") is True, inter.order
    assert "Voted Balanced!" in followup_texts(inter), inter.order
    stop(view)

    # --- A failed board write suppresses the confirmation (never confirm
    # before the board can show the vote).
    ctx, bot, view = await build_mode_vote()
    inter = _FakeInteraction(user_id="0", fail_board=True)
    await drive(view, inter, "Balanced")
    kinds = [k for k, _ in inter.order]
    assert "edit_original" not in kinds, kinds
    assert "followup" not in kinds, kinds
    stop(view)

    # --- A second click by the same voter: "Already voted!", NO board write.
    ctx, bot, view = await build_mode_vote()
    await drive(view, _FakeInteraction(user_id="0"), "Balanced")
    again = _FakeInteraction(user_id="0")
    votes_before = dict(view.votes)
    await drive(view, again, "Balanced")
    stop(view)
    kinds2 = [k for k, _ in again.order]
    assert "edit_original" not in kinds2, again.order
    assert any("Already voted!" in t for t in followup_texts(again)), again.order
    assert dict(view.votes) == votes_before, view.votes

    # --- Non-queue click: "Must be in queue!", no board write, no vote.
    ctx, bot, view = await build_mode_vote()
    outsider = _FakeInteraction(user_id="99")
    await drive(view, outsider, "Balanced")
    stop(view)
    kinds3 = [k for k, _ in outsider.order]
    assert "edit_original" not in kinds3, outsider.order
    assert any(
        "Must be in queue!" in t for t in followup_texts(outsider)
    ), outsider.order
    assert view.votes == {"Balanced": 0, "Captains": 0}

    # --- Timer tick's edit carries NO view (mutation-verified: restoring
    # view= fails this). The real timer runs with fast sleep.
    import views.mode_vote_view as mvv

    ctx, bot, view = await build_mode_vote()
    view.check_for_winner = noop
    ticks = await drive_timer(view, mvv)
    stop(view)
    assert ticks, "timer must keep the countdown updated"
    assert all("view" not in e for e in ticks), ticks

    # --- Expired interactions (NotFound on defer) are dropped silently and
    # never queued.
    ctx, bot, view = await build_mode_vote()
    dead = _FakeInteraction(user_id="0")

    async def _boom(**kw):
        raise _discord_stub.NotFound("expired")

    dead.response = types.SimpleNamespace(is_done=lambda: False, defer=_boom)
    await view.vote_callback(dead, "Balanced")
    stop(view)
    assert dead.order == [], "expired interaction must not reach the handler"
    assert view.interaction_request_queue.empty(), "expired click must not queue"
    assert view.votes == {"Balanced": 0, "Captains": 0}

    # --- MapTypeVoteView: same pattern + timer.
    import views.map_type_vote_view as mtv

    ctx = FakeCtx()
    bot = FakeBot()
    view = MapTypeVoteView(ctx, bot)
    view.view_message = await ctx.send("vote")
    mt = _FakeInteraction(user_id="0")
    await drive(view, mt, "Competitive")
    kinds4 = [k for k, _ in mt.order]
    assert kinds4[0] == "defer" and kinds4.count("edit_original") == 1, mt.order
    assert kinds4.index("edit_original") < kinds4.index("followup"), mt.order
    edited_view = board_payload(mt)["view"]
    assert "Competitive Maps (1)" in labels_of(edited_view), labels_of(edited_view)
    view.check_for_winner = noop
    ticks4 = await drive_timer(view, mtv)
    stop(view)
    assert ticks4, "map-type timer must keep the countdown updated"
    assert all("view" not in e for e in ticks4), ticks4

    # --- MapVoteView: same pattern + timer.
    import views.map_vote_view as mvv2

    ctx = FakeCtx()
    bot = FakeBot()
    bot.chosen_mode = "Balanced"
    view = MapVoteView(ctx, bot, ["Ascent", "Bind", "Haven"])
    # MapVoteView.__init__ never initializes .timeout (only
    # check_for_winner reads it); give the fake a value like a matured
    # view would carry.
    view.timeout = False
    await view.setup()
    view.view_message = await ctx.send("vote")
    for b in view.map_buttons:
        view.add_item(b)
    mv = _FakeInteraction(user_id="0")
    ascent_button = next(
        b for b in view.map_buttons if getattr(b, "label", "").startswith("Ascent")
    )
    await drive(view, mv, ascent_button, "Ascent")
    kinds5 = [k for k, _ in mv.order]
    assert kinds5[0] == "defer" and kinds5.count("edit_original") == 1, mv.order
    assert kinds5.index("edit_original") < kinds5.index("followup"), mv.order
    edited_view = board_payload(mv)["view"]
    assert "Ascent (1)" in labels_of(edited_view), labels_of(edited_view)
    view.check_for_winner = noop
    ticks5 = await drive_timer(view, mvv2)
    stop(view)
    assert ticks5, "map-vote timer must keep the countdown updated"
    assert all("view" not in e for e in ticks5), ticks5

    # --- SecondCaptainChoiceView: defer -> disabling edit via the
    # interaction -> confirmation followup (never the reverse).
    import views.captains_drafting_view as cdv

    ctx = FakeCtx()
    bot = FakeBot()
    choice = SecondCaptainChoiceView(ctx, bot)
    choice.view_message = await ctx.send("choose")
    cap2 = _FakeInteraction(user_id="2")
    drafted = []

    async def _fake_start_draft(single_pick):
        drafted.append(single_pick)

    choice.start_draft = _fake_start_draft
    await choice.first_pick_callback(cap2)
    choice.cancel_timeout_timer()
    kinds6 = [k for k, _ in cap2.order]
    assert kinds6[0] == "defer", cap2.order
    assert kinds6.count("edit_original") == 1, cap2.order
    assert kinds6.index("edit_original") < kinds6.index("followup"), cap2.order
    edited_view = board_payload(cap2)["view"]
    assert all(b.disabled for b in edited_view.children), edited_view.children
    assert cap2.order[-1][1][1].get("ephemeral") is True, cap2.order
    assert "First pick selected!" in followup_texts(cap2), cap2.order
    assert drafted == [True], drafted

    # Board-write failure: no confirmation, but the draft still starts.
    ctx = FakeCtx()
    bot = FakeBot()
    choice = SecondCaptainChoiceView(ctx, bot)
    choice.view_message = await ctx.send("choose")
    cap2 = _FakeInteraction(user_id="2", fail_board=True)
    drafted = []

    async def _fake_start_draft2(single_pick):
        drafted.append(single_pick)

    choice.start_draft = _fake_start_draft2
    await choice.double_pick_callback(cap2)
    choice.cancel_timeout_timer()
    kinds7 = [k for k, _ in cap2.order]
    assert "edit_original" not in kinds7, cap2.order
    assert "followup" not in kinds7, cap2.order
    assert drafted == [False], drafted

    # Its timer: per-second ticks carry no view (the 0s tail edit does).
    ctx = FakeCtx()
    bot = FakeBot()
    choice = SecondCaptainChoiceView(ctx, bot)
    choice.view_message = await ctx.send("choose")
    choice.start_draft = noop
    ticks6 = await drive_timer(choice, cdv, patch_loop=True)
    choice.cancel_timeout_timer()
    assert ticks6, "second-captain timer must keep the countdown updated"
    assert all("view" not in e for e in ticks6), ticks6

    print("all vote confirm-order self-checks passed")


if __name__ == "__main__":
    asyncio.run(demo())
