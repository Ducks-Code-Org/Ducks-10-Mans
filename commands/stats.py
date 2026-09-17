"""Lookup and display MMR and stats for a player (issue #178)."""

import logging

import discord
from discord.ext import commands

from commands import BotCommands
from database import users
from game.ranks import SSR_NAME, role_mention, tier_for_player
from game.ranking import position_of
from game.stats_helper import DEFAULT_MMR, avg_rating_of

log = logging.getLogger(__name__)


async def setup(bot):
    await bot.add_cog(StatsCommand(bot))


def _resolve_player(bot, ctx, riot_input):
    """Resolve the lookup argument to a discord id, or None with a message sent."""
    if riot_input is None:
        return str(ctx.author.id)

    # Discord mention or raw user id
    query = riot_input.strip()
    if query.startswith("<@") and query.endswith(">"):
        query = query[2:-1].lstrip("!")
    if query.isdigit():
        return query

    # Riot ID (Name#Tag)
    try:
        riot_name, riot_tag = query.rsplit("#", 1)
    except ValueError:
        return None
    player_data = users.find_one(
        {"name": riot_name.lower().strip(), "tag": riot_tag.lower().strip()}
    )
    if player_data:
        return str(player_data.get("discord_id"))
    return None


class StatsCommand(BotCommands):
    @commands.command()
    async def stats(self, ctx, *, riot_input=None):
        # Allows players to lookup the stats of other players by Riot ID or @mention
        player_id = _resolve_player(self.bot, ctx, riot_input)
        if player_id is None:
            await ctx.send(
                "Could not find this player. Use a Riot ID (`Name#Tag`) or "
                "@mention a player. Please check the name and tag and ensure "
                "they have played at least one match."
            )
            return

        stats_data = self.bot.player_mmr.get(player_id)
        if not stats_data or (
            not stats_data.get("matches_played", 0)
            and not (stats_data.get("wins", 0) + stats_data.get("losses", 0))
        ):
            if riot_input is None:
                await ctx.send(
                    "You do not have an MMR yet. Participate in matches to earn one!"
                )
            else:
                await ctx.send(
                    "This player does not have an MMR yet. Participate in "
                    "matches to earn one!"
                )
            return

        member = ctx.guild.get_member(int(player_id)) if ctx.guild else None
        display_name = member.display_name if member else riot_input or ctx.author.name

        stats_data = self.bot.player_mmr[player_id]

        user_data = users.find_one({"discord_id": str(player_id)})
        if user_data:
            riot_name = user_data.get("name", "Unknown")
            riot_tag = user_data.get("tag", "Unknown")
        else:
            riot_name, riot_tag = ctx.author.name, ""
        full_riot_id = f"{riot_name}#{riot_tag}"

        mmr_value = stats_data.get("mmr", DEFAULT_MMR)
        wins = stats_data.get("wins", 0)
        losses = stats_data.get("losses", 0)
        matches_played = stats_data.get("matches_played", wins + losses)
        avg_cs = stats_data.get("average_combat_score", 0)
        kd_ratio = stats_data.get("kill_death_ratio", 0)
        # Round-weighted average VLR rating; None when never recorded.
        avg_rating = avg_rating_of(stats_data)
        avg_rating_display = (
            f"{avg_rating:.2f}" if isinstance(avg_rating, (int, float)) else "N/A"
        )
        win_percent = (wins / matches_played) * 100 if matches_played > 0 else 0

        # Leaderboard rank: placement among players who have played, plus the
        # player's rank role (issue #178). Rank 1 also holds Supersonic Radiant.
        position = position_of(self.bot.player_mmr, player_id)
        ranked_total = sum(
            1
            for stats in self.bot.player_mmr.values()
            if stats.get("matches_played", 0) > 0
            or (stats.get("wins", 0) + stats.get("losses", 0)) > 0
        )
        tier = tier_for_player(mmr_value, matches_played=matches_played)
        rank_parts = []
        if tier:
            rank_parts.append(role_mention(ctx.guild, tier))
        if position == 1:
            rank_parts.append(role_mention(ctx.guild, SSR_NAME))
        if position is not None:
            rank_parts.append(f"{position}/{ranked_total}")
        rank_line = ", ".join(rank_parts) if rank_parts else "Unranked"

        embed = discord.Embed(
            title=f"{display_name}'s Stats",
            color=discord.Color.blurple(),
        )
        embed.set_author(name=full_riot_id)
        embed.add_field(name="Rank", value=rank_line, inline=False)
        embed.add_field(name="MMR", value=str(mmr_value), inline=True)
        embed.add_field(name="Win/Loss", value=f"{wins}/{losses}", inline=True)
        embed.add_field(name="Win Rate", value=f"{win_percent:.2f}%", inline=True)
        embed.add_field(name="Avg. Rating", value=avg_rating_display, inline=True)
        embed.add_field(name="Avg. ACS", value=f"{avg_cs:.2f}", inline=True)
        embed.add_field(name="K/D Ratio", value=f"{kd_ratio:.2f}", inline=True)
        embed.set_footer(text="Use !stats @user or !stats Name#Tag")

        await ctx.send(embed=embed)
        log.info("Stats lookup: %s (%s) by %s", riot_name, riot_tag, ctx.author)
