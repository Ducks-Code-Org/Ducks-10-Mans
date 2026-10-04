"""Self-checks for the interest-board refresh fix (issue #257).

Regression: in slash mode the posted board WAS the creation interaction's
response; the view stored that InteractionMessage and re-edited it through
the creation interaction's webhook token — dead 15 minutes later (401,
error 50027). Every click must now edit through the click's own
interaction.response.edit_message, the command posts the board already
fully rendered (no placeholder + fetch_message), and a click on a slot
whose time has passed retires the board (all buttons disabled, no change).
"""

import os
import sys
import types
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Database stub: one slot doc whose roster the membership ops maintain.
_slot = {"scheduled_at_utc": None, "interested_ids": []}


def _fou(query, update, **k):
    if "$addToSet" in update:
        uid = update["$addToSet"]["interested_ids"]
        if uid not in _slot["interested_ids"]:
            _slot["interested_ids"].append(uid)
        return dict(_slot)
    if "$pull" in update:
        uid = update["$pull"]["interested_ids"]
        _slot["interested_ids"] = [
            i for i in _slot["interested_ids"] if i != uid
        ]
        return dict(_slot)
    return dict(_slot)


interests_stub = types.SimpleNamespace(
    find_one=lambda q: dict(_slot),
    find_one_and_update=lambda q, u, **k: _fou(q, u, **k),
)
sys.modules["database"] = types.ModuleType("database")
sys.modules["database"].interests = interests_stub
sys.modules["database"].users = types.SimpleNamespace(find_one=lambda *a, **k: None)

_edited_via_response = []


class _Response:
    def __init__(self):
        self.calls = []

    async def edit_message(self, **kw):
        self.calls.append(("edit_message", kw))
        _edited_via_response.append(kw)


class _FakeInteraction:
    """A click's own interaction: fresh webhook, message is NOT stored."""

    def __init__(self, user_id="me"):
        self.user = types.SimpleNamespace(id=user_id)
        self.response = _Response()


_discord_stub = types.ModuleType("discord")
_discord_stub.ui = types.SimpleNamespace()


class _FakeViewBase:
    """Minimal View stand-in: collects items, accepts timeout."""

    def __init__(self, *a, **k):
        self.children = []

    def add_item(self, item):
        self.children.append(item)


class _FakeButton:
    def __init__(self, **k):
        self.label = k.get("label")
        self.disabled = False
        self.callback = None


_discord_stub.ui.View = type("View", (_FakeViewBase,), {})
_discord_stub.ui.Button = _FakeButton
_discord_stub.ButtonStyle = types.SimpleNamespace(
    success=1, secondary=2, primary=3
)
_discord_stub.Embed = lambda *a, **k: types.SimpleNamespace(description=k.get("description", ""))
_discord_stub.Color = types.SimpleNamespace(green=lambda: None)


def _fake_utils_get(iterable=None, **kw):
    return None


_discord_stub.utils = types.SimpleNamespace(get=_fake_utils_get)
_discord_stub.NotFound = type("NotFound", (Exception,), {})
_discord_stub.HTTPException = type("HTTPException", (Exception,), {})
_discord_stub.Interaction = type("Interaction", (), {})
sys.modules["discord"] = _discord_stub
sys.modules["discord.ui"] = _discord_stub.ui

import views.interest_view as iv  # noqa: E402
from views.interest_view import InterestView, slot_is_past  # noqa: E402


def make_view(slot_time):
    _slot["scheduled_at_utc"] = slot_time
    return InterestView(slot_time, timeout=None)


def buttons(view):
    return {b.label: b for b in view.children}


FUTURE = datetime.now(timezone.utc) + timedelta(days=1)
PAST = datetime.now(timezone.utc) - timedelta(minutes=30)


def demo():
    iv._slot = _slot

    # --- Join on a live slot: exactly one edit, via the click's own
    # interaction, carrying the updated roster; never via a stored message.
    view = make_view(FUTURE)
    inter = _FakeInteraction("me")
    import asyncio

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
    src = open(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "views", "interest_view.py")
    ).read()
    assert "self.message" not in src, "defunct stored-message edit path must be gone"
    assert "fetch_message" not in open(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "commands", "interest.py")
    ).read(), "placeholder+fetch dance must be gone"

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

    # --- Pure helper: both sides of the slot-time boundary.
    assert slot_is_past(PAST) and not slot_is_past(FUTURE)
    assert not slot_is_past(datetime.now(timezone.utc) + timedelta(seconds=1))

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

    # --- The command posts the board already fully rendered: no
    # placeholder step, no message fetch, no stored message.
    cmd_src = open(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "commands", "interest.py")
    ).read()
    assert "Creating interest slot…" not in cmd_src, "placeholder step must be gone"
    assert "_board_embed" in cmd_src, "board must be built before posting"

    print("all interest-board self-checks passed")


if __name__ == "__main__":
    demo()