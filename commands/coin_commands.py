"""Duck Coins slash commands (issue #34, #193, #210): balance, betting,
doubledown, and map overrides. Balance lookups are hidden (ephemeral);
bet, doubledown, and setmap all reply publicly so the whole channel sees
the action (issue #210 follow-up)."""

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
from tracker_links import display_name_for

log = logging.getLogger(__name__)


async def setup(bot):
    await bot.add_cog(CoinCommands(bot))


class CoinCommands(BotCommands):
    # ------------------------------------------------------------------
    # /coins (issue #193)
    # ------------------------------------------------------------------
    @commands.hybrid_command(
        name="coins",
        description="Show a player's Duck Coin balance (hidden reply)",
    )
    async def coins(
        self,
        ctx,
        *,
        target: str = None,
    ):
        """Show a player's Duck Coin balance. Optional: Riot ID or @mention."""
        if not duck_coins_enabled():
            return

        player_id = _resolve_player(self.bot, ctx, target)
        if player_id is None:
            await ctx.send(
                "Could not find that player. Use a Riot ID (`Name#Tag`) or @mention.",
                ephemeral=True,
            )
            return

        user_data = users.find_one({"discord_id": str(player_id)})
        # Linked players show their Riot ID; unlinked players (dead Riot
        # account, stats preserved) fall back to their Discord display name.
        display_name = display_name_for(
            user_data, guild=ctx.guild, discord_id=str(player_id)
        )

        coins = coins_of(player_id)
        emote = duck_emote(self.bot)
        await ctx.send(
            f"**{display_name}** has **{coins}** Duck Coins {emote}", ephemeral=True
        )
        log.info("Coins lookup for %s by %s: %s coins", player_id, ctx.author, coins)

    # ------------------------------------------------------------------
    # Betting / doubledown / map overrides (issue #34)
    # ------------------------------------------------------------------
    @commands.hybrid_group(
        name="bet",
        description="Bet Duck Coins on the current match (public reply)",
        fallback="help",
    )
    async def bet(self, ctx: commands.Context):
        """Pick a side: `/bet attackers <amount>` or `/bet defenders <amount>`."""
        await self._gated_send(
            ctx,
            lambda: "Pick a side: `/bet attackers <amount>` or `/bet defenders <amount>`.",
        )

    @bet.command(name="attackers", description="Bet coins on the Attackers")
    async def bet_attackers(self, ctx: commands.Context, amount: int):
        await self._gated_send(
            ctx,
            lambda: place_bet(self.bot, str(ctx.author.id), "attackers", amount),
            public=True,
        )

    @bet.command(name="defenders", description="Bet coins on the Defenders")
    async def bet_defenders(self, ctx: commands.Context, amount: int):
        await self._gated_send(
            ctx,
            lambda: place_bet(self.bot, str(ctx.author.id), "defenders", amount),
            public=True,
        )

    @commands.hybrid_command(
        name="doubledown",
        description="Spend 5 Duck Coins to double your MMR change for this match (public reply)",
    )
    async def doubledown_command(self, ctx: commands.Context):
        # Powerup: only usable inside the generated match-# channel.
        await self._gated_send(
            ctx,
            lambda: doubledown(self.bot, str(ctx.author.id)),
            channel=ctx.channel,
            public=True,
        )

    @commands.hybrid_command(
        name="setmap",
        description="Wager Duck Coins to override the chosen map (public reply)",
    )
    async def setmap_command(
        self, ctx: commands.Context, map_name: str, amount: int = None
    ):
        """Wager coins to override the chosen map (min 3, must outbid).

        Without an amount the cost escalates one coin over the last override
        (min 3). With an amount, you pay exactly that much and it must beat
        the previous override wager.
        """
        # Overrides must land after map voting and before teams are decided —
        # during the captains draft, or the same window in Balanced mode, plus
        # the 2-minute grace after teams finalize. Powerup: match-# channel only.
        rejection = command_available(
            self.bot, requires_running_match=False, channel=ctx.channel
        )
        if rejection:
            log.warning(
                "Duck Coins command rejected for %s: %s",
                ctx.author,
                rejection,
            )
            await ctx.send(rejection, ephemeral=True)
            return
        await ctx.send(
            await setmap_override(self.bot, str(ctx.author.id), map_name, amount)
        )

    async def _gated_send(
        self,
        ctx: commands.Context,
        message_factory,
        requires_running_match: bool = True,
        channel=None,
        public: bool = False,
    ):
        rejection = command_available(
            self.bot,
            requires_running_match=requires_running_match,
            channel=channel,
        )
        if rejection:
            log.warning(
                "Duck Coins command rejected for %s: %s",
                ctx.author,
                rejection,
            )
            await ctx.send(rejection, ephemeral=True)
            return
        await ctx.send(message_factory(), ephemeral=not public)
