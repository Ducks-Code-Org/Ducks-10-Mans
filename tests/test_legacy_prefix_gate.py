"""Self-check for the legacy `!` prefix gate (issue #241).

A bot.ini `legacy_prefix_commands` flag must let admins disable the legacy
`!` prefix commands while slash invocations keep working. Source-text
asserts can't prove a gate fires, so this drives the real bot-level check
through HybridCommand.can_run on both invocation paths across the full
flag cycle.
"""

import asyncio
import configparser
import os
import sys
import types
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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
import globals  # noqa: E402
from discord.ext import commands  # noqa: E402

import commands.slash_helpers as slash_helpers  # noqa: E402


def fake_features(**flags):
    cp = configparser.ConfigParser()
    cp.read_dict({"features": flags})
    return cp["features"]


async def demo():
    original = globals.BOT_FEATURES
    try:
        bot = commands.Bot(
            command_prefix="!",
            intents=discord.Intents.default(),
            help_command=None,
        )
        slash_helpers_bot = bot
        register = slash_helpers_bot  # alias for clarity
        from commands.slash_helpers import register_error_handlers

        register_error_handlers(bot)
        await bot.load_extension("commands.help")

        coins = bot.get_command("help")
        assert coins is not None, "a real command is needed to drive can_run"

        def gate_ctx(mode):
            perms = discord.Permissions(administrator=True)
            ctx = mock.Mock(bot=bot, permissions=perms, guild=mock.Mock(id=1))
            if mode == "slash":
                interaction = mock.Mock(client=bot, permissions=perms)
                interaction.is_expired.return_value = False
                ctx.interaction = interaction
                interaction._baton = ctx
            else:
                ctx.interaction = None
            return ctx

        async def allows(mode):
            try:
                return await coins.can_run(gate_ctx(mode))
            except commands.CheckFailure as e:
                # can_run re-raises the gate's CheckFailure with its message.
                globals._last_gate_error = str(e)
                return False

        # Missing flag defaults to ON: shipped bot.ini behavior unchanged.
        globals.BOT_FEATURES = fake_features()
        assert globals.legacy_prefix_commands_enabled() is True
        assert await allows("prefix"), "default must keep !commands working"
        assert await allows("slash"), "slash always works"

        # Flag off: prefix invocations rejected with a helpful reply, slash
        # invocations untouched.
        globals.BOT_FEATURES = fake_features(legacy_prefix_commands="false")
        assert globals.legacy_prefix_commands_enabled() is False
        assert not await allows("prefix"), "disabled flag must block !commands"
        assert "disabled" in globals._last_gate_error, globals._last_gate_error
        assert await allows("slash"), "disabled flag must not touch slash"

        # Flag on again: everything reopens.
        globals.BOT_FEATURES = fake_features(legacy_prefix_commands="true")
        assert await allows("prefix") and await allows("slash")

        # The error handler surfaces the gate's message as the reply.
        from commands.slash_helpers import _on_command_error_reply

        ctx = gate_ctx("prefix")
        ctx.send = mock.AsyncMock()
        await _on_command_error_reply(
            ctx, commands.CheckFailure("Legacy `!` commands are disabled.")
        )
        assert ctx.send.await_count == 1
        kwargs = ctx.send.await_args.kwargs
        assert "disabled" in (ctx.send.await_args.args or ("",))[0] or (
            "disabled" in str(kwargs)
        ), "the gate's reason must reach the invoker"

        # Shipped bot.ini ships the flag enabled.
        globals.BOT_FEATURES = original
        assert (
            globals.legacy_prefix_commands_enabled() is True
        ), "shipped bot.ini must keep legacy commands on"
    finally:
        globals.BOT_FEATURES = original

    print("all legacy-prefix gate self-checks passed")


if __name__ == "__main__":
    asyncio.run(demo())
