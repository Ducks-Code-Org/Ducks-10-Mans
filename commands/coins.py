"""Display a player's Duck Coin balance (issue #193)."""

import logging

from discord.ext import commands

from commands import BotCommands
from commands.stats import _resolve_player
from database import users
from game.duck_coins import coins_of, duck_coins_enabled, duck_emote

log = logging.getLogger(__name__)


async def setup(bot):
    await bot.add_cog(CoinsCommand(bot))


class CoinsCommand(BotCommands):
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
