"""Self-check: every command registers as a /slash command with the right
ephemeral behaviour and permission gates (issue #210)."""

import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Stub the Mongo connection before any command module import.
class _Coll:
    def __getattr__(self, name):
        return lambda *a, **k: None

    def find(self, *a, **k):
        return []

    def find_one(self, *a, **k):
        return None


_database_stub = types.ModuleType("database")
for name in (
    "users",
    "mmr_collection",
    "seasons",
    "all_matches",
    "coin_escrow",
    "interests",
    "recent_queue",
):
    setattr(_database_stub, name, _Coll())
_database_stub.client = types.SimpleNamespace(close=lambda: None)
sys.modules["database"] = _database_stub

import discord  # noqa: E402
from discord.ext import commands  # noqa: E402


def build_bot():
    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    intents.members = True
    bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

    from commands.slash_helpers import register_error_handlers

    register_error_handlers(bot)
    return bot


async def demo():
    bot = build_bot()
    extensions = [
        "commands.admin_commands",
        "commands.bug",
        "commands.coin_commands",
        "commands.help",
        "commands.interest",
        "commands.leaderboard",
        "commands.linkriot",
        "commands.maintenance_commands",
        "commands.ranks",
        "commands.report",
        "commands.signup",
        "commands.stats",
    ]
    for ext in extensions:
        await bot.load_extension(ext)

    tree = {c.qualified_name for c in bot.tree.get_commands()}
    expected = {
        "signup",
        "report",
        "interest",
        "pingrecent",
        "stats",
        "linkriot",
        "ranks",
        "leaderboard",
        "coins",
        "bet",
        "doubledown",
        "dodge",
        "setmap",
        "help",
        "bug",
        "newseason",
        "initialize_rounds",
        "simulate_queue",
        "setcaptain",
        "toggledev",
        "cancel",
        "rollback",
        "editplayer",
        "substitute",
        "fixmap",
        "setconfig",
        "showconfig",
        "adminhelp",
        "matchinfo",
        "addcoins",
        "resetplayer",
        "forcereport",
        "resetseason",
        "snapshotseason",
        "recoverseason",
    }
    missing = expected - tree
    assert not missing, f"missing slash commands: {missing}"
    extra = tree - expected
    # /bet subcommands live inside the bet group; nothing unexpected at top level.
    assert not extra, f"unexpected slash commands: {extra}"

    # /bet group children include attackers/defenders + the usage fallback.
    bet_group = next(c for c in bot.tree.get_commands() if c.qualified_name == "bet")
    children = {c.name for c in bet_group.commands}
    assert {"attackers", "defenders", "help"} <= children, children

    # Dev-mode gate is registered as a bot-level check (applies to both paths).
    assert bot._checks, "dev-mode global check must be registered"

    # Hidden commands reply ephemeral: verify via the module source contract.
    import inspect

    import commands.coin_commands as cc
    import commands.leaderboard as lb_mod
    import commands.admin_commands as ac

    coins_src = inspect.getsource(cc.CoinCommands.coins.callback)
    assert "ephemeral=True" in coins_src, "/coins must reply hidden"
    lb_src = inspect.getsource(lb_mod.LeaderboardCommand.leaderboard.callback)
    assert "ephemeral=True" in lb_src, "/leaderboard must reply hidden"

    toggle_src = inspect.getsource(ac.AdminCommands.toggledev.callback)
    assert "ephemeral=True" not in toggle_src, "/toggledev must stay public"

    # Issue #228: drive the gate end-to-end in both invocation modes. Source
    # text can't prove the gate fires — replacing the gate body with
    # `return True`, or reverting toggledev to a cog-local flag, must fail.
    from unittest import mock

    toggle = ac.AdminCommands.toggledev.callback
    admin_cog = bot.get_cog("AdminCommands")
    bot.change_presence = mock.AsyncMock()
    toggle_ctx = mock.Mock(author="self-check", send=mock.AsyncMock())

    def gate_ctx(mode, admin):
        perms = discord.Permissions(administrator=admin)
        ctx = mock.Mock(bot=bot, permissions=perms, guild=mock.Mock(id=1))
        if mode == "slash":
            interaction = mock.Mock(client=bot, permissions=perms)
            ctx.interaction = interaction
            interaction._baton = ctx
        else:
            ctx.interaction = None
        return ctx

    async def gate_allows(mode, admin):
        try:
            return await bot.get_command("coins").can_run(gate_ctx(mode, admin))
        except commands.CheckFailure:
            return False

    for mode in ("slash", "prefix"):
        assert await gate_allows(mode, False), f"{mode}: normal mode must not gate"
        await toggle(admin_cog, toggle_ctx)
        assert not await gate_allows(
            mode, False
        ), f"{mode}: dev mode must block non-admins"
        assert await gate_allows(mode, True), f"{mode}: dev mode must keep admins"
        await toggle(admin_cog, toggle_ctx)
        assert await gate_allows(mode, False), f"{mode}: disabling must reopen the gate"

    # Issue #210 follow-up: /bet, /doubledown, and /setmap reply publicly.
    # Rejections/errors stay hidden; only the result reply flips public.
    for cmd in (
        cc.CoinCommands.bet_attackers.callback,
        cc.CoinCommands.bet_defenders.callback,
        cc.CoinCommands.doubledown_command.callback,
    ):
        src = inspect.getsource(cmd)
        assert "public=True" in src, f"/{cmd.__name__} must reply publicly"
    setmap_src = inspect.getsource(cc.CoinCommands.setmap_command.callback)
    assert "setmap_override" in setmap_src
    assert (
        "ephemeral=True" not in setmap_src.split("setmap_override", 1)[1]
    ), "/setmap result reply must stay public"
    gated_src = inspect.getsource(cc.CoinCommands._gated_send)
    assert (
        "ephemeral=not public" in gated_src
    ), "_gated_send must hide rejections but allow public results"

    # toggledev no longer switches the prefix (slash commands make it moot).
    assert "command_prefix" not in toggle_src, "toggledev must not switch prefixes"

    # Issue #260: /dodge is a real command that must AWAIT the engine (a bare
    # lambda would send the coroutine object) and reply at all. Drive it
    # behaviorally against the loaded cog with fake ctx/bot: rejection replies
    # hidden, no teardown on rejection.
    import game.duck_coins as dc

    dodge_cmd = bot.get_command("dodge")
    assert dodge_cmd is not None, "/dodge must be registered"
    cog = bot.get_cog("CoinCommands")

    class _FakeGuild:
        id = 1
        text_channels = []

    class _DodgeBot:
        report_lock = asyncio.Lock()
        # Match running but no powerup window yet: command_available passes,
        # the engine is reached, and it rejects on the missing window.
        match_ongoing = True
        match_channel = None
        match_name = "match"
        map_override_deadline = None
        ten_mans_channel = None

    sent = []

    class _FakeCtx:
        author = types.SimpleNamespace(id=99)
        channel = types.SimpleNamespace(id=1, name="match-260")
        guild = _FakeGuild()

        async def send(self, content=None, **kw):
            sent.append((content, kw))

    dodge_bot = _DodgeBot()
    orig_bot = cog.bot
    cog.bot = dodge_bot
    try:
        await dodge_cmd.callback(cog, _FakeCtx())
    finally:
        cog.bot = orig_bot
    assert sent, "/dodge must reply even when it rejects"
    rejected, kw = sent[-1]
    assert "only available after teams are announced" in rejected, rejected
    assert kw.get("ephemeral") is True, "a rejected /dodge must stay hidden"
    assert "<coroutine" not in rejected, "the engine coroutine must be awaited"

    # Channel gate: a live match with a known match channel rejects a dodge
    # from anywhere else, hidden, with no engine call (no charge).
    from game.duck_coins import DODGE_COST

    dodge_bot.match_channel = types.SimpleNamespace(id=777, name="match-260")
    before = dc.coins_of(99)
    sent.clear()
    cog.bot = dodge_bot
    try:
        await dodge_cmd.callback(cog, _FakeCtx())
    finally:
        cog.bot = orig_bot
    assert sent, "an off-channel /dodge must still reply"
    off_channel, kw2 = sent[-1]
    assert "match channel" in off_channel, off_channel
    assert kw2.get("ephemeral") is True, "the channel rejection must stay hidden"
    assert dc.coins_of(99) == before, "a rejected dodge must charge nothing"
    # (coins_of(99) is 0 in the fake store; the assertion documents no charge.)
    assert before == 0 and DODGE_COST > 0

    print("all slash-conversion self-checks passed")


if __name__ == "__main__":
    asyncio.run(demo())
