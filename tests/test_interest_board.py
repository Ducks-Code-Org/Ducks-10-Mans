"""Self-checks for the interest-board refresh fix (issue #257).

Regression: in slash mode the posted board WAS the creation interaction's
response; the view stored that InteractionMessage and re-edited it through
the creation interaction's webhook token — dead 15 minutes later (401,
error 50027). Every click must now edit through the click's own
interaction.response.edit_message, the command posts the board already
fully rendered (no placeholder + fetch_message), and a click on a slot
whose time has passed retires the board (all buttons disabled, no change).
"""

import asyncio
import os
import sys
import types
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Database stub: one slot doc whose roster the membership ops maintain.
_slot = {"scheduled_at_utc": None, "interested_ids": []}
_find_one_calls = []


def _fou(query, update, **k):
    if "$addToSet" in update:
        uid = update["$addToSet"]["interested_ids"]
        if uid not in _slot["interested_ids"]:
            _slot["interested_ids"].append(uid)
        return dict(_slot)
    if "$pull" in update:
        uid = update["$pull"]["interested_ids"]
        _slot["interested_ids"] = [i for i in _slot["interested_ids"] if i != uid]
        return dict(_slot)
    return dict(_slot)


def _find_one(q):
    _find_one_calls.append(q)
    return dict(_slot)


sys.modules["database"] = types.ModuleType("database")
sys.modules["database"].interests = types.SimpleNamespace(
    find_one=_find_one,
    find_one_and_update=lambda q, u, **k: _fou(q, u, **k),
)
sys.modules["database"].users = types.SimpleNamespace(find_one=lambda *a, **k: None)

_RESPONSE_EDITS = []


class _Response:
    def __init__(self):
        self.calls = []

    async def edit_message(self, **kw):
        self.calls.append(("edit_message", kw))
        _RESPONSE_EDITS.append(kw)


class _FakeInteraction:
    """A click's own interaction: fresh webhook, message is NOT stored."""

    def __init__(self, user_id="me"):
        self.user = types.SimpleNamespace(id=user_id)
        self.response = _Response()


from views.interest_view import InterestView, slot_is_past  # noqa: E402
from commands.interest import InterestCommand  # noqa: E402


def make_view(slot_time):
    _slot["scheduled_at_utc"] = slot_time
    return InterestView(slot_time, timeout=None)


FUTURE = datetime.now(timezone.utc) + timedelta(days=1)
PAST = datetime.now(timezone.utc) - timedelta(minutes=30)


def test_callbacks():
    # --- Join on a live slot: exactly one edit, via the click's own
    # interaction, carrying the updated roster; never via a stored message.
    _slot["interested_ids"] = []
    view = make_view(FUTURE)
    inter = _FakeInteraction("me")
    asyncio.run(view.join_callback(inter))
    assert _slot["interested_ids"] == ["me"], "join must record membership"
    eds = [c for c in inter.response.calls if c[0] == "edit_message"]
    assert len(eds) == 1, f"exactly one write, got {inter.response.calls}"
    desc = eds[0][1]["embed"].description
    assert "**Interested (1)**" in desc, desc
    assert "<@me>" in desc, desc
    # The edit carries the FULL post-vote view (buttons still enabled).
    assert all(not b.disabled for b in eds[0][1]["view"].children)
    # One shared render path: the callbacks never touch a message attribute.
    assert not hasattr(view, "message"), "stored message attr must be gone"

    # --- Leave: symmetric, one edit, roster updated.
    view2 = make_view(FUTURE)
    inter2 = _FakeInteraction("me")
    asyncio.run(view2.leave_callback(inter2))
    assert _slot["interested_ids"] == [], "leave must remove the member"
    eds2 = [c for c in inter2.response.calls if c[0] == "edit_message"]
    assert len(eds2) == 1
    assert "**Interested (0)**" in eds2[0][1]["embed"].description, eds2[0][1]

    # --- Refresh on a live slot: re-renders from the DB.
    view3 = make_view(FUTURE)
    inter3 = _FakeInteraction("me")
    asyncio.run(view3.refresh_callback(inter3))
    eds3 = [c for c in inter3.response.calls if c[0] == "edit_message"]
    assert len(eds3) == 1

    # --- Click on an expired slot: all three buttons gray out in the edit,
    # no membership change.
    _slot["interested_ids"] = []
    view4 = make_view(PAST)
    inter4 = _FakeInteraction("me")
    asyncio.run(view4.join_callback(inter4))
    assert _slot["interested_ids"] == [], "expired slot must change nothing"
    eds4 = [c for c in inter4.response.calls if c[0] == "edit_message"]
    assert len(eds4) == 1
    retired_view = eds4[0][1]["view"]
    assert all(b.disabled for b in retired_view.children), retired_view.children
    for cb in (view4.leave_callback, view4.refresh_callback):
        inter5 = _FakeInteraction("me")
        asyncio.run(cb(inter5))
        eds5 = [c for c in inter5.response.calls if c[0] == "edit_message"]
        assert len(eds5) == 1 and all(b.disabled for b in eds5[0][1]["view"].children)

    # --- Pure helper: the boundary decides retirement; at the exact slot
    # instant the slot is already past (>=), one second before it is not.
    assert slot_is_past(PAST) and not slot_is_past(FUTURE)
    assert slot_is_past(FUTURE, now=FUTURE), "exact slot instant counts as past"
    assert not slot_is_past(FUTURE + timedelta(seconds=1), now=FUTURE)


def test_command_posts_rendered_board():
    # --- /interest posts the board already fully rendered, using the roster
    # the upsert returned: no placeholder step, no re-query, no fetch.
    _slot["scheduled_at_utc"] = None
    _slot["interested_ids"] = ["existing"]
    sent = []

    class _Ctx:
        author = types.SimpleNamespace(id=456)

        async def send(self, **kw):
            sent.append(kw)

    before = len(_find_one_calls)
    # The hybrid_command wrapper stores the raw callback; invoking it is the
    # closest testable seam to a real /interest (both slash and prefix route
    # through ctx.send here).
    asyncio.run(InterestCommand.interest.callback(InterestCommand, _Ctx(), time="9pm"))

    assert _slot["interested_ids"] == ["existing", "456"], "creator must join"
    assert len(sent) == 1, f"exactly one message posted, got {sent}"
    desc = sent[0]["embed"].description
    assert "**Interested (2)**" in desc, desc
    assert "<@456>" in desc and "<@existing>" in desc, desc
    assert sent[0]["view"].scheduled_at_utc is not None
    assert (
        len(_find_one_calls) == before
    ), "command must render from the upsert's returned doc, not a re-query"


def source_contracts():
    # --- Story 7/8: the defunct placeholder/fetch/stored-message paths
    # cannot sneak back into the shipped source.
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cmd_src = open(os.path.join(root, "commands", "interest.py")).read()
    view_src = open(os.path.join(root, "views", "interest_view.py")).read()
    assert "Creating interest slot…" not in cmd_src, "placeholder step must be gone"
    assert "fetch_message" not in cmd_src, "placeholder+fetch dance must be gone"
    assert "self.message" not in view_src, "stored-message edit path must be gone"
    assert "defer(" not in view_src, "defer-then-edit flow must be gone"
    assert "board_embed" in cmd_src, "board must be built before posting"


def demo():
    test_callbacks()
    test_command_posts_rendered_board()
    source_contracts()
    print("all interest-board self-checks passed")


if __name__ == "__main__":
    demo()
