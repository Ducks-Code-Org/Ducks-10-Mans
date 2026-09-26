"Commands related to displaying leaderboards."

import logging
from typing import Literal

from discord.ext import commands

from commands import BotCommands
from database import mmr_collection
from views.leaderboard_view import (
    LeaderboardView,
    sort_key_for,
)

log = logging.getLogger(__name__)


async def setup(bot):
    await bot.add_cog(LeaderboardCommand(bot))


class LeaderboardCommand(BotCommands):
    @staticmethod
    def generate_leaderboard(bot, ctx=None, sort_by: str = "mmr"):
        valid_sort_map = {
            "mmr": "mmr",
            "rating": "avg_rating",
            "avg_rating": "avg_rating",
            "acs": "average_combat_score",
            "kd": "kill_death_ratio",
            "wins": "wins",
            "losses": "losses",
            "coins": "duck_coins",
            "duckcoins": "duck_coins",
            "ducks": "duck_coins",
        }

        sort_by = sort_by.lower()
        if sort_by not in valid_sort_map:
            log.warning("Invalid leaderboard type requested: %r", sort_by)
            return None, "Invalid leaderboard type.", None

        sort_by_internal = valid_sort_map[sort_by]
        cursor = mmr_collection.find()
        sorted_data = list(cursor)
        sorted_data.sort(key=sort_key_for(sort_by_internal), reverse=True)

        leaderboard_view = LeaderboardView(
            ctx,
            bot,
            sorted_data,
            sort_by_internal,
            players_per_page=10,
            timeout=None,
        )
        content = leaderboard_view.make_content(
            sorted_data, leaderboard_view.total_pages
        )
        return leaderboard_view, content, None

    @commands.hybrid_command(
        name="leaderboard",
        description="View the 10 mans leaderboard (hidden reply)",
    )
    async def leaderboard(
        self,
        ctx,
        sort_by: Literal[
            "mmr", "rating", "wins", "losses", "kd", "acs", "coins"
        ] = "mmr",
    ):
        leaderboard_view, content, error = LeaderboardCommand.generate_leaderboard(
            self.bot, ctx, sort_by
        )
        if error:
            await ctx.send(error, ephemeral=True)
            return
        self.leaderboard_view = leaderboard_view
        # Hidden (ephemeral) per issue #210: the persistent public board posts
        # to #leaderboard on startup; a manual invocation is personal. Each
        # invocation still gets its own view (issue #183).
        self.leaderboard_message = await ctx.send(
            content=content, view=leaderboard_view, ephemeral=True
        )
