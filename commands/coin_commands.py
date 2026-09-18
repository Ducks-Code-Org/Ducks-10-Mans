"""Prefix commands for Duck Coins (issue #34, #193): balance, betting,
doubledown, and map overrides."""

import logging

from discord.ext import commands

from commands import BotCommands
from commands.stats import _resolve_player
from database import users
from game.duck_coins import (
    coins_of,
    command_available,
    duck_coins_enabled,
    duck_emote,
    doubledown,
    place_bet,
    setmap_override,
)

log = logging.getLogger(__name__)


async def setup(bot):
    await bot.add_cog(CoinCommands(bot))


class CoinCommands(BotCommands):
    # ------------------------------------------------------------------
    # !coins (from commands/coins.py, issue #193)
    # ------------------------------------------------------------------
    @commands.command(name="coins")
    async def coins(self, ctx, *, target: str = None):
        """Show a player's Duck Coin balance. Optional: Riot ID or @mention."""
        if not duck_coins_enabled():
            return

        player_id = _resolve_player(self.bot, ctx, target)
        if player_id is None:
            await ctx.send(
                "Could not find that player. Use a Riot ID (`Name#Tag`) or @mention."
            )
            return

        user_data = users.find_one({"discord_id": str(player_id)})
        display_name = (
            f"{user_data.get('name', '?')}#{user_data.get('tag', '?')}"
            if user_data
            else ctx.author.name
        )

        coins = coins_of(player_id)
        emote = duck_emote(self.bot)
        await ctx.reply(
            f"**{display_name}** has **{coins}** Duck Coins {emote}",
            mention_author=False,
        )
        log.info("Coins lookup for %s by %s: %s coins", player_id, ctx.author, coins)

    # ------------------------------------------------------------------
    # Betting / doubledown / map overrides (issue #34)
    # ------------------------------------------------------------------
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
        # during the captains draft, or the same window in Balanced mode, plus
        # the 2-minute grace after teams finalize.
        parts = args.rsplit(" ", 1)
        map_name, amount = args, None
        if len(parts) == 2 and parts[1].isdigit():
            map_name, amount = parts[0], int(parts[1])
        rejection = command_available(self.bot, requires_running_match=False)
        if rejection:
            log.warning(
                "Duck Coins command rejected for %s: %s",
                ctx.author,
                rejection,
            )
            await ctx.send(rejection)
            return
        await ctx.send(
            await setmap_override(self.bot, str(ctx.author.id), map_name, amount)
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