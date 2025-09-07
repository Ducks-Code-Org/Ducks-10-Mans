"Commands related to displaying leaderboards."

from discord.ext import commands

from commands import BotCommands
from database import mmr_collection
from views.leaderboard_view import (
    LeaderboardView,
)


async def setup(bot):
    await bot.add_cog(LeaderboardCommand(bot))


class LeaderboardCommand(BotCommands):
    @staticmethod
    def generate_leaderboard(bot, ctx=None, sort_by: str = "mmr"):
        valid_sort_map = {
            "mmr": "mmr",
            "acs": "average_combat_score",
            "kd": "kill_death_ratio",
            "wins": "wins",
            "losses": "losses",
        }

        sort_by = sort_by.lower()
        if sort_by not in valid_sort_map:
            return None, "Invalid leaderboard type.", None

        sort_by_internal = valid_sort_map[sort_by]
        cursor = mmr_collection.find()
        sorted_data = list(cursor)
        sorted_data.sort(key=lambda x: x.get(sort_by_internal, 0), reverse=True)

        leaderboard_view = LeaderboardView(
            ctx,
            bot,
            sorted_data,
            sort_by_internal,
            players_per_page=10,
            timeout=None,
            mode="normal",
        )
        content = leaderboard_view.make_content(
            sorted_data, "normal", leaderboard_view.total_pages
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
        self.leaderboard_message = await ctx.send(
            content=content, view=leaderboard_view
        )
