"""Display the current rank thresholds (issues #194, #210, #214)."""

import logging

import discord
from discord.ext import commands

from commands import BotCommands
from game.ranks import SSR_NAME, RANKS, role_mention

log = logging.getLogger(__name__)


async def setup(bot):
    await bot.add_cog(RanksCommand(bot))


def ranks_embed(guild) -> discord.Embed:
    """Rank threshold embed derived from game.ranks.RANKS, role-mentioned."""
    embed = discord.Embed(
        title="Rank Roles & MMR Thresholds",
        color=discord.Color.green(),
    )
    ordered = sorted(RANKS, key=lambda r: r[0])
    lines = []
    for i, (threshold, name, _) in enumerate(ordered):
        upper = ordered[i + 1][0] - 1 if i + 1 < len(ordered) else None
        if upper is None:
            span = f"({threshold}+ MMR)"
        else:
            span = f"({threshold}-{upper} MMR)"
        lines.append(f"{role_mention(guild, name)} {span}")
    lines.append(f"{role_mention(guild, SSR_NAME)} (Rank 1)")
    embed.description = "\n".join(lines)
    return embed


class RanksCommand(BotCommands):
    @commands.hybrid_command(
        name="ranks",
        description="Show every rank role and its MMR threshold range (hidden reply)",
    )
    async def ranks(self, ctx):
        """Show every rank role and its MMR threshold range."""
        # Issue #214: post the same thresholds inside an embed (hidden reply
        # per issue #210). Content is unchanged — only the wrapper changed.
        await ctx.send(embed=ranks_embed(ctx.guild), ephemeral=True)
        log.info("Rank thresholds shown to %s", ctx.author)
