"""Self-checks for the /signup interaction-deadline fix and timeout-cancel cleanup.

Regression (10062 Unknown interaction): /signup did multi-second work (Riot
identity refresh, stale cleanup, channel/role creation) before its first
ctx.send, blowing Discord's 3-second interaction deadline — the token died and
every reply failed with "The application did not respond". The command must
defer BEFORE anything slow; every later send rides the followup webhook.

Regression (stale leftovers after a signup timeout): SignupView.cancel_signup
(the 10-min-empty / 2-hour-inactivity path) deleted the match channel/role but
left the bot attributes pointing at the deleted objects, so the next /signup
logged "Stale match resources found at signup" and paid extra delete
round-trips. The timeout path must null them like cleanup_match_resources.
"""

import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub modules with import-time side effects (Mongo connection) before
# importing commands.signup.
_database_stub = types.ModuleType("database")
_database_stub.users = types.SimpleNamespace(
    find_one=lambda *a, **k: None,
    find=lambda *a, **k: [],
    update_one=lambda *a, **k: None,
)
_database_stub.mmr_collection = types.SimpleNamespace(
    update_one=lambda *a, **k: None,
    find_one=lambda *a, **k: None,
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
    feature_enabled=lambda *a, **k: False,
    BOT_CONFIG=None,
    BOT_FEATURES=types.SimpleNamespace(getboolean=lambda *a, **k: None),
)

import commands.signup as signup_mod


class FakeCtx:
    def __init__(self):
        self.sent = []
        self.defer_calls = []
        self.interaction = object()  # non-None so is_interaction-ish checks pass
        self.author = types.SimpleNamespace(id=1, name="owner")
        self.guild = None
        self.channel = types.SimpleNamespace(category=None)

    async def defer(self, *, ephemeral=False):
        self.defer_calls.append(ephemeral)

    async def send(self, msg=None, **kw):
        self.sent.append(msg)


def make_bot():
    bot = types.SimpleNamespace()
    bot.signup_lock = asyncio.Lock()
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
    bot.match_channel = None
    bot.match_role = None
    bot.load_mmr_data = lambda: None
    return bot


# --- /signup defers before the first slow await -----------------------------
# Order is the whole bug: identity refresh (which stalls on the rate limiter)
# must not run before the interaction is acknowledged.
async def _run_defer_order():
    bot = make_bot()
    order = []

    from commands.signup import SignupCommand

    cog = SignupCommand.__new__(SignupCommand)
    cog.bot = bot
    ctx = FakeCtx()

    async def _spy_identity(discord_id):
        order.append("identity")
        return (True, "", {"discord_id": "1"})

    async def _perms(_ctx):
        order.append("perm-check")
        return True

    signup_mod.ensure_perms = _perms
    signup_mod.ensure_current_riot_identity = _spy_identity

    # Let identity (the slowest pre-reply await) run; the flow then exits via
    # guild=None failing create_role, which lands in the error path.
    await SignupCommand.signup.callback(cog, ctx)

    assert ctx.defer_calls == [
        True
    ], f"/signup must defer (ephemeral) exactly once first, got {ctx.defer_calls}"
    assert order == [
        "perm-check",
        "identity",
    ], f"defer must precede every slow await, got {order}"
    assert any(
        "Error setting up queue" in (m or "") for m in ctx.sent
    ), f"expected the error-path reply (it rides the deferred followup), got {ctx.sent}"


asyncio.run(_run_defer_order())


# --- A later gate still replies (via the followup) and returns --------------
async def _run_already_active():
    bot = make_bot()
    bot.signup_active = True

    from commands.signup import SignupCommand

    cog = SignupCommand.__new__(SignupCommand)
    cog.bot = bot
    ctx = FakeCtx()
    await SignupCommand.signup.callback(cog, ctx)

    assert ctx.defer_calls == [True], "defer must happen even for rejections"
    assert any(
        "already in progress" in (m or "") for m in ctx.sent
    ), f"expected the already-active reply, got {ctx.sent}"
    assert bot.queue == [], "no queue side effects on rejection"


asyncio.run(_run_already_active())


# --- cancel_signup nulls the deleted resources ------------------------------
# The 10-minute-empty timeout path must leave the refs None, like every other
# cancel path, or the next /signup pays for cleanup of deleted objects.
async def _run_timeout_cancels_clean():
    from views.signup_view import SignupView

    class _Role:
        async def delete(self):
            pass

    class _Channel:
        async def delete(self):
            pass

    bot = make_bot()
    bot.match_role = _Role()
    bot.match_channel = _Channel()
    bot.current_signup_message = object()
    bot.match_setup_generation = bot.setup_generation

    view = SignupView.__new__(SignupView)  # bare instance, no real ctx
    view.bot = bot
    view.ctx = FakeCtx()
    view.setup_generation = bot.setup_generation
    view.stop = lambda: None
    view.cancel_refresh_signup_task = lambda: None
    view.cancel_channel_rename_task = lambda: None
    view.cancel_signup_queue_task = lambda: None
    view.cancel_timeout_monitor_task = lambda: None

    await view.cancel_signup("Queue has been empty for more than 10 minutes.")

    assert bot.match_role is None, "timeout must null the deleted match role"
    assert bot.match_channel is None, "timeout must null the deleted match channel"
    assert (
        bot.current_signup_message is None
    ), "timeout must null the deleted signup message"
    assert (
        bot.match_setup_generation is None
    ), "leftovers must look stale to the next /signup"
    assert bot.signup_active is False and bot.queue == [], "queue state reset"
    assert any(
        "Signup cancelled" in (m or "") for m in view.ctx.sent
    ), "cancellation notice still sent"


asyncio.run(_run_timeout_cancels_clean())

print("all signup interaction-deadline self-checks passed")