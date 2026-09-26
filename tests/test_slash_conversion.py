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

    print("all slash-conversion self-checks passed")


if __name__ == "__main__":
    asyncio.run(demo())
