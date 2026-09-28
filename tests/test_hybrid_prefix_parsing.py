"""Self-check: hybrid commands parse their legacy `!` form like before the
slash conversion (issue #210).

Regression this pins: `/recoverseason` takes an optional attachment option,
but the callback annotated it `file: discord.Attachment = None` instead of
`discord.Attachment | None`. discord.py only treats the Optional/union form
as "no attachment is fine" on the prefix path — the bare class form raises
MissingRequiredAttachment for `!recoverseason confirm` with nothing attached,
an error the shared handler does not surface, so the user got silence
instead of the command's own "Attach the snapshot .json file" message.
"""

import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub the Mongo connection before any command module import.
_database_stub = types.ModuleType("database")
for _name in (
    "users",
    "mmr_collection",
    "seasons",
    "all_matches",
    "coin_escrow",
    "interests",
    "recent_queue",
):
    setattr(_database_stub, _name, types.SimpleNamespace())
_database_stub.client = types.SimpleNamespace(close=lambda: None)
sys.modules["database"] = _database_stub

import discord  # noqa: E402
from discord.ext import commands  # noqa: E402
from discord.ext.commands.view import StringView  # noqa: E402

import commands.maintenance_commands as mc  # noqa: E402


class _FakeState:
    pass


class _FakeMessage:
    _state = _FakeState()

    def __init__(self, content, attachments=()):
        self.content = content
        self.attachments = list(attachments)


class _FakeBot:
    user = types.SimpleNamespace(id=1)

    async def get_prefix(self, message):
        return "!"

    async def can_run(self, ctx, *, call_once=False):
        return True


class _FakeAttachment:
    filename = "snap.json"


async def parse_prefix(cmd, content, attachments=()):
    """Drive the real prefix parser, returning ctx.args/kwargs or the error."""
    view = StringView(content)
    view.skip_string("!")
    view.skip_ws()
    view.get_word()  # the invoked command name
    view.skip_ws()
    ctx = commands.Context(
        message=_FakeMessage(content, attachments),
        bot=_FakeBot(),
        view=view,
        prefix="!",
        command=cmd,
    )
    try:
        await cmd._parse_arguments(ctx)
    except Exception as e:  # noqa: BLE001 — the assertion is the point
        return e
    return ctx


async def demo():
    # No attachment: the option must resolve to None (the callback's own
    # "Attach the snapshot .json file" guard then handles it), not raise.
    ctx = await parse_prefix(
        mc.MaintenanceCommands.recoverseason, "!recoverseason confirm"
    )
    assert not isinstance(ctx, Exception), (
        "!recoverseason without an attachment must parse (file=None), "
        f"got {type(ctx).__name__}: {ctx}"
    )
    assert ctx.args[1] == "confirm", ctx.args
    assert ctx.args[2] is None, ctx.args

    # With an attachment the file still arrives.
    ctx = await parse_prefix(
        mc.MaintenanceCommands.recoverseason,
        "!recoverseason confirm",
        [_FakeAttachment()],
    )
    assert not isinstance(ctx, Exception), ctx
    assert isinstance(ctx.args[2], _FakeAttachment), ctx.args

    # The app-command side keeps the attachment optional too.
    from discord.ext.commands.hybrid import HybridCommand

    hybrid = HybridCommand(
        mc.MaintenanceCommands.recoverseason.callback,
        name="recoverseason",
        description="x",
    )
    required = {p.name: p.required for p in hybrid.app_command.parameters}
    assert required.get("file") is False, required

    # A representative required-argument command still reports its own
    # missing argument cleanly through the shared handler path.
    ctx = await parse_prefix(mc.MaintenanceCommands.resetseason, "!resetseason")
    assert isinstance(ctx, commands.MissingRequiredArgument), (
        "!resetseason without confirm must raise MissingRequiredArgument, "
        f"got {ctx!r}"
    )

    print("all hybrid prefix parsing self-checks passed")


if __name__ == "__main__":
    asyncio.run(demo())
