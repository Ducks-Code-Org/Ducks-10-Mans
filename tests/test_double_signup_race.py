"""Self-checks for the double-!signup race (issue: second !signup deleted the live setup).

Regression: between the queue filling (signup_active flips False in
finalize_signup) and the report (match_not_reported/match_ongoing set when the
map vote ends), every flag !signup checks is False. A second !signup in that
window fell into the stale-resource recovery in SignupCommand.signup and
deleted the LIVE match channel/role out from under the running match setup;
the stale cycle's finalize_match_setup then resurrected match_not_reported
over the new signup's state, deadlocking both signups. !signup must refuse
when the leftover resources belong to the CURRENT setup generation, and still
recover resources left by a crashed/legacy cycle.
"""

import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub modules with import-time side effects (Mongo connection) before
# importing commands.signup / commands.report.
_database_stub = types.ModuleType("database")
_database_stub.users = types.SimpleNamespace(
    find_one=lambda *a, **k: None,
    find=lambda *a, **k: [],
    update_one=lambda *a, **k: None,
)
_database_stub.mmr_collection = types.SimpleNamespace(
    update_one=lambda *a, **k: None,
    find_one=lambda *a, **k: None,
    find=lambda *a, **k: [],
)
_database_stub.seasons = types.SimpleNamespace(find_one=lambda *a, **k: None)
_database_stub.all_matches = types.SimpleNamespace(find_one=lambda *a, **k: None)
_database_stub.recent_queue = types.SimpleNamespace(update_one=lambda *a, **k: None)
_database_stub.coin_escrow = types.SimpleNamespace(
    update_one=lambda *a, **k: None, find_one=lambda *a, **k: None
)
sys.modules["database"] = _database_stub

sys.modules["globals"] = types.SimpleNamespace(
    API_KEY=None,
    URI_KEY=None,
    BOT_TOKEN=None,
    feature_enabled=lambda *a, **k: False,
    BOT_CONFIG=None,
    BOT_FEATURES=types.SimpleNamespace(getboolean=lambda *a, **k: None),
)


class _FakeEmbed:
    def __init__(self, *a, **k):
        self.title = a[0] if a else k.get("title")
        self.fields = []
        self.footer = None

    def add_field(self, **kw):
        self.fields.append(kw)

    def set_footer(self, **kw):
        self.footer = kw.get("text")


_discord_stub = types.ModuleType("discord")
_discord_stub.Embed = _FakeEmbed
_discord_stub.Color = types.SimpleNamespace(
    green=lambda: None,
    gold=lambda: None,
    blue=lambda: None,
    yellow=lambda: None,
    red=lambda: None,
    blurple=lambda: None,
)
_discord_stub.PermissionOverwrite = type(
    "PermissionOverwrite",
    (),
    {"__init__": lambda self, **kw: None},
)
_discord_stub.utils = types.SimpleNamespace(get=lambda *a, **k: None)
# Mirror discord.py: NotFound/Forbidden subclass HTTPException, so callers
# that catch HTTPException also catch them.
_discord_stub.HTTPException = type("HTTPException", (Exception,), {})
_discord_stub.NotFound = type("NotFound", (_discord_stub.HTTPException,), {})
_discord_stub.Forbidden = type("Forbidden", (_discord_stub.HTTPException,), {})
_discord_stub.Guild = type("Guild", (), {})
_discord_stub.Role = type("Role", (), {})
_discord_stub.Member = type("Member", (), {})
_discord_stub.Interaction = type("Interaction", (), {})
_discord_stub.ui = types.SimpleNamespace(
    View=type("View", (), {"__init__": lambda self, **kw: None}),
    Button=type("Button", (), {"__init__": lambda self, **kw: None}),
    Select=type("Select", (), {}),
)
sys.modules["discord.ui"] = _discord_stub.ui
_discord_stub.ext = types.SimpleNamespace()
_discord_stub.ext.commands = types.SimpleNamespace(
    command=lambda *a, **k: (lambda f: f),
    has_permissions=lambda **k: (lambda f: f),
    has_role=lambda *a, **k: (lambda f: f),
    Cog=type("Cog", (), {"__init_subclass__": classmethod(lambda cls, **kw: None)}),
)
sys.modules["discord"] = _discord_stub
sys.modules["discord.ext"] = _discord_stub.ext
sys.modules["discord.ext.commands"] = _discord_stub.ext.commands

import commands.report as report_mod
import commands.signup as signup_mod


class _Channel:
    def __init__(self, name):
        self.name = name
        self.id = 1234
        self.deleted = False

    async def delete(self):
        self.deleted = True

    async def send(self, *a, **kw):
        return _Message()


class _Role:
    def __init__(self, name):
        self.name = name
        self.deleted = False
        self.members = []

    async def delete(self):
        self.deleted = True


class _Message:
    def __init__(self):
        self.deleted = False

    async def delete(self):
        self.deleted = True


class _FakeView:
    def __init__(self):
        self.cleaned = False

    def cleanup(self):
        self.cleaned = True


class _DefaultRole:
    pass


class FakeCtx:
    def __init__(self):
        self.sent = []
        self.author = types.SimpleNamespace(id=1, name="owner")
        self.guild = None
        self.channel = types.SimpleNamespace(category=None, send=None)

    async def send(self, msg=None, **kw):
        self.sent.append(msg)


def make_bot():
    bot = types.SimpleNamespace()
    bot.signup_lock = asyncio.Lock()
    bot.report_lock = asyncio.Lock()
    bot.signup_active = False
    bot.signup_view = None
    bot.background_purge_task = None
    bot.setup_generation = 0
    bot.match_setup_generation = None
    bot.match_ongoing = False
    bot.match_not_reported = False
    bot.selected_map = None
    bot.chosen_mode = None
    bot.current_teams_message = None
    bot.current_signup_message = None
    bot.queue = []
    bot.team1 = []
    bot.team2 = []
    bot.captain1 = None
    bot.captain2 = None
    bot.match_channel = None
    bot.match_role = None
    bot.match_name = "match-0001"
    bot.player_mmr = {}
    bot.player_names = {}
    bot.double_downs = set()
    bot.bet_session = None
    bot.map_override_last = 0
    bot.map_override_last_by = None
    bot.map_override_deadline = None
    bot.map_override_chain = []
    bot.load_mmr_data = lambda: None
    bot.wait_until_ready = lambda: asyncio.sleep(0)
    return bot


# --- A LIVE setup must be refused, never deleted ---------------------------
# Simulates the window between queue-full and report: signup_active False,
# match flags False, but channel/role still held by the current cycle.
async def _run_live_setup_refusal():
    bot = make_bot()
    bot.signup_active = False  # finalize_signup already flipped it
    bot.setup_generation = 5
    bot.match_setup_generation = 5  # the LIVE cycle owns these resources
    bot.match_channel = _Channel("match-0001")
    bot.match_role = _Role("match-0001")

    _cleanup_calls = []
    _orig_cleanup = report_mod.cleanup_match_resources

    async def _spy_cleanup(b, cancelled=False):
        _cleanup_calls.append(cancelled)
        await _orig_cleanup(b, cancelled=cancelled)

    report_mod.cleanup_match_resources = _spy_cleanup

    from commands.signup import SignupCommand

    cog = SignupCommand.__new__(SignupCommand)
    cog.bot = bot
    ctx = FakeCtx()

    async def _ensure_perms(_ctx):
        return True

    _orig_identity = signup_mod.ensure_current_riot_identity

    async def _ok_identity(_discord_id):
        return (True, "", {"discord_id": "1"})

    signup_mod.ensure_perms = _ensure_perms
    signup_mod.ensure_current_riot_identity = _ok_identity
    try:
        await cog.signup(ctx)
    finally:
        signup_mod.ensure_current_riot_identity = _orig_identity
        report_mod.cleanup_match_resources = _orig_cleanup

    assert _cleanup_calls == [], "cleanup_match_resources must not run for a LIVE setup"
    assert bot.match_channel is not None, "the live match channel must survive"
    assert bot.match_role is not None, "the live match role must survive"
    assert bot.signup_active is False
    assert bot.setup_generation == 5, "setup_generation must not be bumped"
    assert bot.match_setup_generation == 5
    assert any(
        "being set up" in (m or "") for m in ctx.sent
    ), f"expected a refusal message, got {ctx.sent}"
    # And the queue state of the running setup must be untouched.
    assert bot.queue == []
    assert bot.team1 == [] and bot.team2 == []


asyncio.run(_run_live_setup_refusal())


# --- Stale leftovers (crashed cycle) must still be cleaned up --------------
async def _run_stale_cleanup_still_works():
    bot = make_bot()
    # The bot restarted mid-match: generation moved on, leftovers didn't.
    bot.setup_generation = 9
    bot.match_setup_generation = None  # e.g. restarted from scratch
    bot.match_channel = _Channel("match-0001")
    bot.match_role = _Role("match-0001")
    bot.current_signup_message = _Message()
    bot.current_teams_message = object()
    old_channel, old_role = bot.match_channel, bot.match_role

    from commands.signup import SignupCommand

    cog = SignupCommand.__new__(SignupCommand)
    cog.bot = bot

    async def _ensure_perms(_ctx):
        return True

    async def _ok_identity(_discord_id):
        return (True, "", {"discord_id": "1"})

    signup_mod.ensure_perms = _ensure_perms
    signup_mod.ensure_current_riot_identity = _ok_identity

    # The channel/role creation part of the command: fake it, we only care
    # that recovery cleanup ran and the new cycle was stamped.
    _created = []

    class _FakeGuild:
        default_role = _DefaultRole()

        async def create_role(self, **kw):
            role = _Role(kw.get("name", "role"))
            _created.append(role)
            return role

        async def edit_role_positions(self, positions):
            pass

        async def create_text_channel(self, **kw):
            ch = _Channel(kw.get("name", "channel"))
            _created.append(ch)
            return ch

    ctx = FakeCtx()
    ctx.guild = _FakeGuild()
    ctx.channel = None
    ctx.author = types.SimpleNamespace(id=1, name="owner")
    ctx.channel = types.SimpleNamespace(category=None)

    class _FakeSignupView:
        def __init__(self, ctx_, bot_):
            self.bot = bot_
            self.get_signup_embed = lambda: None

        async def signup_player(self, *a, **k):
            bot.queue.append({"id": a[0], "name": a[1]})

    orig_view = signup_mod.SignupView
    signup_mod.SignupView = _FakeSignupView
    try:
        await cog.signup(ctx)
    finally:
        signup_mod.SignupView = orig_view

    # The stale channel/role were deleted, not the fresh ones.
    assert old_channel.deleted, "stale match channel must be deleted"
    assert old_role.deleted, "stale match role must be deleted"
    fresh = [c for c in _created if not getattr(c, "deleted", True)]
    assert len(fresh) == 2, "a fresh channel and role must have been created"
    # The new cycle was stamped with the current generation.
    assert (
        bot.match_setup_generation == bot.setup_generation
    ), "the new signup must stamp its setup generation"
    assert bot.match_setup_generation == 10
    # Old message deleted by cleanup, signup_active True for the queue phase.
    assert old_message_deleted
    assert bot.signup_active is True


old_message_deleted = None


def _patch_message_delete():
    global old_message_deleted
    orig = report_mod._delete_signup_message_safely

    async def spy(msg):
        global old_message_deleted
        await orig(msg)
        old_message_deleted = msg.deleted

    report_mod._delete_signup_message_safely = spy


_patch_message_delete()
asyncio.run(_run_stale_cleanup_still_works())


# --- finalize_match_setup must not resurrect flags over a newer signup -----
# The stale cycle's finalization raced a new signup: by the time it reaches
# its flag writes, setup_generation moved on and it must bail out instead of
# setting match_not_reported/match_ongoing (the deadlock tail).
async def _run_finalize_flag_guard():
    from views.map_vote_view import MapVoteView

    bot = make_bot()
    bot.team1 = [{"id": "1", "name": "p1"}]
    bot.team2 = [{"id": "2", "name": "p2"}]
    ctx = FakeCtx()
    ctx.guild = None

    view = MapVoteView.__new__(MapVoteView)
    view.bot = bot
    view.ctx = ctx
    view.setup_generation = 1  # captured generation
    view.winning_map = "Ascent"
    view.children = []
    bot.setup_generation = 2  # bumped by the second !signup / !cancel

    await view.finalize_match_setup()

    assert (
        bot.match_not_reported is False
    ), "a superseded finalization must not set match_not_reported"
    assert (
        bot.match_ongoing is False
    ), "a superseded finalization must not set match_ongoing"


asyncio.run(_run_finalize_flag_guard())


# --- cleanup_match_resources clears the ownership stamp --------------------
async def _run_cleanup_clears_stamp():
    bot = make_bot()
    bot.setup_generation = 3
    bot.match_setup_generation = 3
    bot.match_channel = _Channel("match-0001")
    bot.match_role = _Role("match-0001")
    bot.match_not_reported = True
    bot.match_ongoing = True

    await report_mod.cleanup_match_resources(bot)

    assert (
        bot.match_setup_generation is None
    ), "cleanup must clear the ownership stamp so leftovers look stale"
    assert bot.match_channel is None and bot.match_role is None


asyncio.run(_run_cleanup_clears_stamp())

print("all double-signup race self-checks passed")
