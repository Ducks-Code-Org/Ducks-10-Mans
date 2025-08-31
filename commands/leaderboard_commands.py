"Commands related to displaying leaderboards."

import asyncio
from discord.ext import commands
from commands import BotCommands
from database import users, mmr_collection
from views.leaderboard_view import (
    LeaderboardView,
)
import wcwidth
from table2ascii import table2ascii as t2a, PresetStyle


async def setup(bot):
    await bot.add_cog(LeaderboardCommands(bot))


class LeaderboardCommands(BotCommands):
    @commands.command()
    async def leaderboard(self, ctx, sort_by: str = "mmr"):
        valid_sort_map = {
            "mmr": "mmr",
            "acs": "average_combat_score",
            "kd": "kill_death_ratio",
            "wins": "wins",
            "losses": "losses",
        }

        sort_by = sort_by.lower()

        if sort_by not in valid_sort_map:
            await ctx.send("Invalid leaderboard type.")
            return

        sort_by_internal = valid_sort_map[sort_by]

        cursor = mmr_collection.find()
        sorted_data = list(cursor)
        sorted_data.sort(key=lambda x: x.get(sort_by_internal, 0), reverse=True)

        self.leaderboard_view = LeaderboardView(
            ctx,
            self.bot,
            sorted_data,
            sort_by_internal,
            players_per_page=10,
            timeout=None,
            mode="normal",
        )

        self.leaderboard_message = await ctx.send(
            content=self.leaderboard_view.make_content(
                sorted_data, "normal", self.leaderboard_view.total_pages
            ),
            view=self.leaderboard_view,
        )
