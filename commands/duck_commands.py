"""Prefix commands for Duck Coins: betting, doubledown, and map overrides (issue #34)."""

import logging

from discord.ext import commands

from commands import BotCommands
from game.duck_coins import command_available, doubledown, place_bet, setmap_override

log = logging.getLogger(__name__)


async def setup(bot):
    await bot.add_cog(DuckCommands(bot))


class DuckCommands(BotCommands):
    @commands.group(name="bet", invoke_without_command=True)
    async def bet(self, ctx: commands.Context):
        """!bet attackers|defenders <amount> — bet Duck Coins on the match."""
        await self._gated_send(
            ctx,
            lambda: "Pick a side: `!bet attackers <amount>` or `!bet defenders <amount>`.",
        )

    @bet.command(name="attackers")
    async def bet_attackers(self, ctx: commands.Context, amount: int):
        await self._gated_send(
            ctx,
            lambda: place_bet(self.bot, str(ctx.author.id), "attackers", amount),
        )

    @bet.command(name="defenders")
    async def bet_defenders(self, ctx: commands.Context, amount: int):
        await self._gated_send(
            ctx,
            lambda: place_bet(self.bot, str(ctx.author.id), "defenders", amount),
        )

    @commands.command(name="doubledown")
    async def doubledown_command(self, ctx: commands.Context):
        await self._gated_send(ctx, lambda: doubledown(self.bot, str(ctx.author.id)))

    @commands.command(name="setmap")
    async def setmap_command(self, ctx: commands.Context, *, args: str = ""):
        """!setmap <map> [amount] — wager coins to override the chosen map.

        Without an amount the cost escalates one coin over the last override
        (min 3). With an amount, you pay exactly that much and it must beat
        the previous override wager.
        """
        # Overrides must land after map voting and before teams are decided —
        # during the captains draft, or the same window in Balanced mode.
        parts = args.rsplit(" ", 1)
        map_name, amount = args, None
        if len(parts) == 2 and parts[1].isdigit():
            map_name, amount = parts[0], int(parts[1])
        await self._gated_send(
            ctx,
            lambda: setmap_override(self.bot, str(ctx.author.id), map_name, amount),
            requires_running_match=False,
        )

    async def _gated_send(
        self,
        ctx: commands.Context,
        message_factory,
        requires_running_match: bool = True,
    ):
        rejection = command_available(
            self.bot, requires_running_match=requires_running_match
        )
        if rejection:
            log.warning(
                "Duck Coins command rejected for %s: %s",
                ctx.author,
                rejection,
            )
            await ctx.send(rejection)
            return
        await ctx.send(message_factory())
