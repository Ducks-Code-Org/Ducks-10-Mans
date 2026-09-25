"""Shared slash-command conversion helpers (issue #210).

All commands are hybrid commands: they register as /slash commands through the
bot's CommandTree and keep working as `!` prefix commands (the prefix is kept
registered for admins/emergencies, but users are directed to `/` forms).

Visibility rules confirmed on issue #210:
- Personal lookups (coins, leaderboard, stats, ranks, help, linkriot) reply
  hidden (ephemeral).
- Broadcast surfaces (signup, report, bet, doubledown, setmap, cancel,
  substitute, ...) reply publicly.
- Every error, permission failure, and usage message replies hidden
  (ephemeral) so channels stay clean.
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands.hybrid import HybridCommandError

log = logging.getLogger(__name__)

# Slash descriptions must be 1-100 characters; Discord rejects longer ones.
_MAX_DESCRIPTION = 100


def validate_description(description: str) -> str:
    """Clamp a slash description to Discord's 100-character limit."""
    return description[:_MAX_DESCRIPTION]


def guild_only(interaction_or_ctx) -> bool:
    """Whether this invocation happened inside a guild."""
    guild = getattr(interaction_or_ctx, "guild", None)
    return guild is not None


def is_interaction(ctx) -> bool:
    """True when the context came from a slash invocation."""
    interaction = getattr(ctx, "interaction", None)
    return interaction is not None and not interaction.is_expired()


def ephemeral_supported(ctx) -> bool:
    """Whether ctx.send(..., ephemeral=True) actually hides the reply."""
    return is_interaction(ctx)


async def reply_hidden(ctx, content=None, **kwargs):
    """Send a reply only visible to the invoker (slash) or a public reply
    (prefix fallback, where hiding is impossible)."""
    if ephemeral_supported(ctx):
        return await ctx.send(content, ephemeral=True, **kwargs)
    return await ctx.send(content, **kwargs)


async def reply_public(ctx, content=None, **kwargs):
    """Send a public reply in both invocation modes."""
    return await ctx.send(content, **kwargs)


def describe(**kwargs):
    """Re-export of app_commands.describe for parameter hints."""
    return app_commands.describe(**kwargs)


def admin_gate():
    """Check for `has_permissions(administrator=True)` on hybrid commands."""
    return commands.has_permissions(administrator=True)


def owner_gate():
    """Check for the Owner role."""
    return commands.has_role("Owner")


# ---------------------------------------------------------------------------
# Global dev-mode gate: while dev mode is active only administrators may use
# ANY command. Registered as a bot-level check so it applies to both the
# prefix and slash invocation paths (hybrid commands run bot.can_run too).
# ---------------------------------------------------------------------------


async def _dev_mode_gate(ctx) -> bool:
    bot = ctx.bot
    if not getattr(bot, "dev_mode", False):
        return True
    return ctx.permissions.administrator


async def _legacy_prefix_gate(ctx) -> bool:
    """Reject legacy `!` invocations when bot.ini disables them (issue #241).

    Slash invocations (ctx.interaction set) always pass; only the prefix
    fallback is gated. Raises with a message so the error handler can tell
    the invoker where the commands moved.
    """
    from globals import legacy_prefix_commands_enabled

    if is_interaction(ctx) or legacy_prefix_commands_enabled():
        return True
    raise commands.CheckFailure(
        "Legacy `!` commands are disabled. Use the /slash commands instead."
    )


async def _on_command_error_reply(ctx, error: Exception) -> None:
    """User-facing error replies, hidden (ephemeral) everywhere (issue #210)."""
    bot = ctx.bot

    # Unwrap hybrid wrappers so both invocation modes surface the same message.
    if isinstance(error, HybridCommandError):
        cause = error.__cause__
        error = cause if cause is not None else error

    if isinstance(error, app_commands.TransformerError):
        await reply_hidden(ctx, "Invalid argument value for that command.")
        return
    if isinstance(
        error, (app_commands.MissingPermissions, commands.MissingPermissions)
    ):
        await reply_hidden(ctx, "You don't have permission to use that command.")
        return
    if isinstance(
        error,
        (
            app_commands.MissingRole,
            commands.MissingRole,
            app_commands.MissingAnyRole,
            commands.MissingAnyRole,
        ),
    ):
        await reply_hidden(ctx, "You need a higher role to use that command.")
        return
    if isinstance(error, app_commands.NoPrivateMessage):
        await reply_hidden(ctx, "This command can only be used in a server.")
        return
    if isinstance(error, (app_commands.CheckFailure, commands.CheckFailure)):
        # A gate may attach its own user-facing reason (e.g. the legacy
        # prefix gate, issue #241); bare CheckFailures get the generic line.
        message = str(error).strip()
        if message:
            await reply_hidden(ctx, message)
        else:
            await reply_hidden(ctx, "You can't use that command right now.")
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await reply_hidden(ctx, f"Missing argument: `{error.param.name}`.")
        return
    if isinstance(error, (commands.BadArgument, commands.BadBoolArgument)):
        await reply_hidden(ctx, f"Bad argument: {error}")
        return
    if isinstance(error, commands.CommandOnCooldown):
        await reply_hidden(ctx, f"Slow down! Try again in {error.retry_after:.1f}s.")
        return

    # Anything else: log it; the tree's on_error already logged app errors.
    log.error(
        "Error in command %r by %s: %r",
        getattr(ctx.command, "qualified_name", ctx.command),
        ctx.author,
        error,
        exc_info=error,
    )


def register_error_handlers(bot) -> None:
    """Install the shared check + error handling on the bot (call once)."""

    @bot.check
    async def dev_mode_gate(ctx) -> bool:
        return await _dev_mode_gate(ctx)

    @bot.check
    async def legacy_prefix_gate(ctx) -> bool:
        return await _legacy_prefix_gate(ctx)

    # Extend (do not replace) the bot's existing on_command_error.
    original = bot.on_command_error

    async def on_command_error(ctx, error):
        handled = {
            commands.CommandNotFound,
            commands.MissingPermissions,
            commands.MissingRole,
            commands.MissingAnyRole,
            commands.CheckFailure,
            commands.MissingRequiredArgument,
            commands.BadArgument,
            commands.BadBoolArgument,
            commands.CommandOnCooldown,
            commands.NoPrivateMessage,
        }
        if type(error) in handled or isinstance(error, tuple(handled)):
            await _on_command_error_reply(ctx, error)
            return
        if isinstance(error, HybridCommandError):
            await _on_command_error_reply(ctx, error)
            return
        await original(ctx, error)

    bot.on_command_error = on_command_error
