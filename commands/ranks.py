"""Display the current rank thresholds (issue #194)."""

import logging

from discord.ext import commands

from commands import BotCommands
from game.ranks import SSR_NAME, RANKS, role_mention

log = logging.getLogger(__name__)


async def setup(bot):
    await bot.add_cog(RanksCommand(bot))


def ranks_text(guild) -> str:
    """Rank threshold list derived from game.ranks.RANKS, role-mentioned."""
    lines = []
    ordered = sorted(RANKS, key=lambda r: r[0])
    for i, (threshold, name, _) in enumerate(ordered):
        upper = ordered[i + 1][0] - 1 if i + 1 < len(ordered) else None
        if upper is None:
            span = f"({threshold}+ MMR)"
        else:
            span = f"({threshold}-{upper} MMR)"
        lines.append(f"{role_mention(guild, name)} {span}")
    lines.append(f"{role_mention(guild, SSR_NAME)} (Rank 1)")
    return "\n".join(lines)


class RanksCommand(BotCommands):
    @commands.command(name="ranks")
    async def ranks(self, ctx):
        """Show every rank role and its MMR threshold range."""
        await ctx.reply(ranks_text(ctx.guild), mention_author=False)
        log.info("Rank thresholds shown to %s", ctx.author)