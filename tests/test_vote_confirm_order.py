"""Self-checks for confirm-after-reflect on all setup vote views (issue #258).

Regression: vote handlers REST-edited the public board and webhook-replied
the ephemeral "Voted X." confirmation on two independent Discord paths, so
the confirmation could become visible before the board showed the vote —
and the 1-second countdown edits re-asserted the full view, repainting
stale tallies over votes that just landed. Now each vote click defers
component-style, updates the board THROUGH the same interaction
(edit_original_response — one atomic write that completes the ack), and
only then sends the ephemeral confirmation; countdown edits pass no view.
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

    def __init__(self, user_id="0", queue_ids=None):
        self.user = types.SimpleNamespace(id=user_id)
        self._is_done = False
        self.order = []  # ("defer"|"edit_original"|"followup", payload)
        self.response = types.SimpleNamespace(
            is_done=lambda: self._is_done,
            defer=self._defer,
            send_message=self._send_message,
            edit_message=self._edit_message,
        )
        self.followup = types.SimpleNamespace(send=self._followup_send)
        self._in_queue = queue_ids or [user_id]

    async def _defer(self, **kw):
        self.order.append(("defer", kw))
        self._is_done = True

    async def _send_message(self, *a, **kw):
        self.order.append(("send_message", (a, kw)))
        self._is_done = True

    async def _edit_message(self, **kw):
        self.order.append(("edit_message", kw))

    async def edit_original_response(self, **kw):
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


class _FakeViewBaseDiscord(_FakeViewBase):
    pass


_discord_stub.ui = types.SimpleNamespace()
_discord_stub.ui.View = type("View", (_FakeViewBase,), {})
_discord_stub.ui.Button = _FakeButton
_discord_stub.ui.Select = type("Select", (_FakeViewBase,), {})
_discord_stub.ui.select = lambda *a, **k: (lambda f: f)
_discord_stub.SelectOption = lambda *a, **k: types.SimpleNamespace(label=None, value=None)
# CaptainsDraftingView builds pick dropdowns at construction; stub its send
# path instead of exercising it — #258 is about the confirm ordering only.
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
_discord_stub.errors = types.SimpleNamespace(
    NotFound=type("NotFound", (Exception,), {})
)
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

from views.map_type_vote_view import MapTypeVoteView  # noqa: E402
from views.map_vote_view import MapVoteView  # noqa: E402
from views.mode_vote_view import ModeVoteView  # noqa: E402
from views.captains_drafting_view import SecondCaptainChoiceView  # noqa: E402


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


def fresh_labels(view):
    return [b.label for b in view.children if hasattr(b, "label")]


async def drive(view, interaction, *args):
    """Run one click through the view's real queue machinery.

    Does NOT stop the queue task: a second click in the same scenario must
    still be consumable. MapVoteView's map buttons wire their own callbacks
    (the view has no vote_callback); pass the button as the first arg to
    route through it.
    """
    if args and hasattr(args[0], "callback"):
        button, rest = args[0], args[1:]
        await button.callback(interaction, *rest)
    elif args and not hasattr(view, "vote_callback"):
        callback, rest = args[0], args[1:]
        await callback(interaction, *rest)
    else:
        await view.vote_callback(interaction, *args)
    # The queue task processes asynchronously; give it a bounded chance.
    for _ in range(200):
        if interaction.order and interaction.order[-1][0] in (
            "edit_original",
            "followup",
        ):
            break
        await asyncio.sleep(0.01)


def drive_sync(view, interaction, *args):
    _ = asyncio.get_event_loop().create_task(view.vote_callback(interaction, *args))


def stop(view):
    view.cancel_interaction_queue_task()
    view.cancel_timeout_timer()


async def build_mode_vote():
    ctx = FakeCtx()
    bot = FakeBot()
    view = ModeVoteView(ctx, bot)
    view.view_message = await ctx.send("vote")
    return ctx, bot, view


async def demo():
    # --- Valid vote: exactly one board write, initiated BEFORE the
    # confirmation; the deferred component ack precedes both.
    ctx, bot, view = await build_mode_vote()
    inter = _FakeInteraction(user_id="0")
    await drive(view, inter, "Balanced")
    order = inter.order
    kinds = [k for k, _ in order]
    assert kinds[0] == "defer", kinds
    assert kinds.count("edit_original") == 1, kinds
    assert "followup" in kinds, kinds
    assert kinds.index("edit_original") < kinds.index("followup"), kinds
    # The edit carries the full post-vote state — the new tally on the board.
    assert "Balanced Teams (1)" in fresh_labels(view), fresh_labels(view)
    followup_args, followup_kw = next(
        payload for k, payload in order if k == "followup"
    )
    assert followup_kw.get("ephemeral") is True
    assert "Voted Balanced!" in followup_args[0]

    # --- A second click by the same voter: "Already voted!", NO board write.
    ctx, bot, view = await build_mode_vote()
    await drive(view, _FakeInteraction(user_id="0"), "Balanced")
    again = _FakeInteraction(user_id="0")
    votes_before = dict(view.votes)
    await drive(view, again, "Balanced")
    stop(view)
    kinds2 = [k for k, _ in again.order]
    assert "edit_original" not in kinds2, again.order
    # The rejection reply must have gone out (ephemeral), counts unchanged.
    rejections = [
        (a, kw)
        for k, payload in again.order
        if k in ("followup", "send_message")
        and any("Already voted!" in str(x) for x in payload[0])
        for a, kw in [payload]
    ]
    assert rejections, again.order
    assert all(kw.get("ephemeral") for _, kw in rejections), rejections
    assert dict(view.votes) == votes_before, view.votes

    # --- Non-queue click: "Must be in queue!", no board write, no vote.
    ctx, bot, view = await build_mode_vote()
    outsider = _FakeInteraction(user_id="99")
    await drive(view, outsider, "Balanced")
    stop(view)
    kinds3 = [k for k, _ in outsider.order]
    assert "edit_original" not in kinds3, outsider.order
    assert view.votes == {"Balanced": 0, "Captains": 0}

    # --- Timer tick's edit carries NO view: the count-reversion regression
    # (mutation-verified: restoring view= fails this). The timer is driven
    # to completion (fast sleep), and only the countdown ticks count.
    ctx, bot, view = await build_mode_vote()
    board = view.view_message
    import views.mode_vote_view as mvv

    real_sleep = asyncio.sleep

    async def fast_sleep(_):
        await real_sleep(0)

    mvv.asyncio.sleep = fast_sleep
    try:
        await view.timeout_timer()
    except Exception:
        pass  # post-timeout winner handling may walk into view paths we stub
    mvv.asyncio.sleep = real_sleep
    stop(view)
    # Every per-second tick (content ends in "s)") must omit the view kwarg;
    # the close-vote edit legitimately passes the view but is not a tick.
    ticks = [e for e in board.edits if str(e.get("content", "")).endswith("s)")]
    assert ticks, "timer must keep the countdown updated"
    assert all(
        "view" not in e for e in ticks
    ), f"timer tick edits must not pass the view: {[e for e in ticks if 'view' in e]}"
    # A vote lands mid-countdown and then the timer edits again: fresh
    # labels survive the tick (no stale repaint).
    view.children[0].label = "Balanced Teams (9)"
    await board.edit(content="Vote how teams should be chosen: (23s)")
    assert "Balanced Teams (9)" in fresh_labels(view)

    # --- Expired interactions (NotFound on defer) are dropped silently and
    # never queued.
    ctx, bot, view = await build_mode_vote()
    dead = _FakeInteraction(user_id="0")
    dead.response.defer = self_boom = types.SimpleNamespace()

    async def _boom(**kw):
        raise _discord_stub.errors.NotFound(dead)

    dead.response = types.SimpleNamespace(is_done=lambda: False, defer=_boom)
    await view.vote_callback(dead, "Balanced")
    stop(view)
    assert dead.order == [], "expired interaction must not reach the handler"
    assert view.votes == {"Balanced": 0, "Captains": 0}

    # --- MapTypeVoteView: same pattern.
    ctx = FakeCtx()
    bot = FakeBot()
    view = MapTypeVoteView(ctx, bot)
    view.view_message = await ctx.send("vote")
    mt = _FakeInteraction(user_id="0")
    await drive(view, mt, "Competitive")
    stop(view)
    kinds4 = [k for k, _ in mt.order]
    assert kinds4[0] == "defer" and kinds4.count("edit_original") == 1, mt.order
    assert kinds4.index("edit_original") < kinds4.index("followup"), mt.order
    assert "Competitive Maps (1)" in fresh_labels(view)

    # --- MapVoteView: same pattern.
    ctx = FakeCtx()
    bot = FakeBot()
    bot.chosen_mode = "Balanced"
    view = MapVoteView(ctx, bot, ["Ascent", "Bind", "Haven"])
    # MapVoteView.__init__ never initializes .timeout (only check_for_winner
    # reads it); give the fake a value like a matured view would carry.
    view.timeout = False
    await view.setup()
    view.view_message = await ctx.send("vote")
    for b in view.map_buttons:
        view.add_item(b)
    mv = _FakeInteraction(user_id="0")
    ascent_button = next(
        b for b in view.children if getattr(b, "label", "").startswith("Ascent")
    )
    await drive(view, mv, ascent_button, "Ascent")
    kinds5 = [k for k, _ in mv.order]
    assert kinds5[0] == "defer" and kinds5.count("edit_original") == 1, mv.order
    assert kinds5.index("edit_original") < kinds5.index("followup"), mv.order
    assert "Ascent (1)" in fresh_labels(view), fresh_labels(view)
    # Its timer too: no view kwarg on countdown edits (driven to completion
    # with fast sleep, like the mode-vote timer check).
    stop(view)
    ctx = FakeCtx()
    bot2 = FakeBot()
    bot2.chosen_mode = "Balanced"
    view2 = MapVoteView(ctx, bot2, ["Ascent", "Bind", "Haven"])
    view2.timeout = False
    board = view2.view_message = await ctx.send("vote")
    import views.map_vote_view as mvv2

    mvv2.asyncio.sleep = fast_sleep
    try:
        await view2.timeout_timer()
    except Exception:
        pass
    mvv2.asyncio.sleep = real_sleep
    stop(view2)
    ticks2 = [e for e in board.edits if str(e.get("content", "")).endswith("s)")]
    assert ticks2, "map-vote timer must keep the countdown updated"
    assert all("view" not in e for e in ticks2), ticks2

    # --- SecondCaptainChoiceView: defer -> disabling edit via the
    # interaction -> confirmation followup (never the reverse).
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
    kinds6 = [k for k, _ in cap2.order]
    assert kinds6[0] == "defer", cap2.order
    assert kinds6.count("edit_original") == 1, cap2.order
    assert kinds6.index("edit_original") < kinds6.index("followup"), cap2.order
    edited_view = cap2.order[1][1]["view"]
    assert all(b.disabled for b in edited_view.children), edited_view.children
    f_args, f_kw = next(payload for k, payload in cap2.order if k == "followup")
    assert f_kw.get("ephemeral") is True
    assert "First pick selected!" in f_args[0]
    choice.cancel_timeout_timer()

    print("all vote confirm-order self-checks passed")


if __name__ == "__main__":
    asyncio.run(demo())