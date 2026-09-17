"Commands related to displaying leaderboards."

import logging

from discord.ext import commands

from commands import BotCommands
from database import mmr_collection
from game.stats_helper import avg_rating_of
from views.leaderboard_view import (
    LeaderboardView,
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

        def sort_key(doc):
            if sort_by_internal == "avg_rating":
                # Round-weighted average VLR rating; unplayed players (no
                # rounds recorded) sort to the bottom.
                rating = avg_rating_of(doc)
                return rating if rating is not None else float("-inf")
            return doc.get(sort_by_internal, 0)

        sorted_data.sort(key=sort_key, reverse=True)

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

    @commands.command()
    async def leaderboard(self, ctx, sort_by: str = "mmr"):
        leaderboard_view, content, error = LeaderboardCommand.generate_leaderboard(
            self.bot, ctx, sort_by
        )
        if error:
            await ctx.send(error)
            return
        self.leaderboard_view = leaderboard_view
        # Reply directly to the invoker so concurrent users don't pile onto
        # the same button set (issue #183). Each invocation gets its own view.
        self.leaderboard_message = await ctx.reply(
            content=content, view=leaderboard_view
        )
