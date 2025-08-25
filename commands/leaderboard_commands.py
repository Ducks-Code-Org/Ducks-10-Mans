"Commands related to displaying leaderboards."

import asyncio
from discord.ext import commands
from commands import BotCommands
from database import users, mmr_collection
from views.leaderboard_view import (
    LeaderboardView,
    LeaderboardViewKD,
    LeaderboardViewWins,
    LeaderboardViewACS,
)
import wcwidth
from table2ascii import table2ascii as t2a, PresetStyle


async def setup(bot):
    await bot.add_cog(LeaderboardCommands(bot))


class LeaderboardCommands(BotCommands):
    @commands.command()
    async def leaderboard(self, ctx):
        cursor = mmr_collection.find()
        sorted_data = list(cursor)
        sorted_data.sort(key=lambda x: x.get("mmr", 0), reverse=True)

        self.leaderboard_view = LeaderboardView(
            ctx, self.bot, sorted_data, players_per_page=10, timeout=None, mode="normal"
        )

        start_index = 0
        end_index = min(
            self.leaderboard_view.players_per_page,
            len(self.leaderboard_view.sorted_data),
        )
        page_data = self.leaderboard_view.sorted_data[start_index:end_index]

        # Create initial leaderboard table
        leaderboard_data = []
        for idx, stats in enumerate(page_data, start=1):
            user_data = users.find_one({"discord_id": str(stats["player_id"])})
            if user_data:
                full_name = f"{user_data.get('name', 'Unknown')}#{user_data.get('tag', 'Unknown')}"
                if wcwidth.wcswidth(full_name) > 20:
                    name = full_name[:17] + "..."
                else:
                    name = full_name.ljust(20)
            else:
                name = "Unknown"

            leaderboard_data.append(
                [
                    idx,
                    name,
                    stats.get("mmr", 1000),
                    stats.get("wins", 0),
                    stats.get("losses", 0),
                    f"{stats.get('average_combat_score', 0):.2f}",
                    f"{stats.get('kill_death_ratio', 0):.2f}",
                ]
            )

        table_output = t2a(
            header=["Rank", "User", "MMR", "Wins", "Losses", "Avg ACS", "K/D"],
            body=leaderboard_data,
            first_col_heading=True,
            style=PresetStyle.thick_compact,
        )

        content = f"## MMR Leaderboard (Page 1/{self.leaderboard_view.total_pages}) ##\n```\n{table_output}\n```"

        self.leaderboard_message = await ctx.send(
            content=content, view=self.leaderboard_view
        )

        if self.refresh_task is not None:
            self.refresh_task.cancel()
        self.refresh_task = asyncio.create_task(self.periodic_refresh())

    @commands.command()
    async def leaderboard_KD(self, ctx):
        if not self.bot.player_mmr:
            await ctx.send("No MMR data available yet.")
            return

        # Sort all players by MMR
        sorted_kd = sorted(
            self.bot.player_mmr.items(),
            key=lambda x: x[1].get("kill_death_ratio", 0.0),
            reverse=True,
        )

        # Create the view for pages
        view = LeaderboardView(ctx, self.bot, sorted_kd, players_per_page=10)

        # Calculate the page indexes
        start_index = view.current_page * view.players_per_page
        end_index = start_index + view.players_per_page
        page_data = sorted_kd[start_index:end_index]

        names = []
        leaderboard_data = []
        for player_id, stats in page_data:
            user_data = users.find_one({"discord_id": str(player_id)})
            if user_data:
                riot_name = user_data.get("name", "Unknown")
                riot_tag = user_data.get("tag", "Unknown")
                names.append(f"{riot_name}#{riot_tag}")
            else:
                names.append("Unknown")

        # Stats for leaderboard
        for idx, ((player_id, stats), name) in enumerate(
            zip(page_data, names), start=start_index + 1
        ):
            mmr_value = stats["mmr"]
            wins = stats["wins"]
            losses = stats["losses"]
            matches_played = stats.get("matches_played", wins + losses)
            avg_cs = stats.get("average_combat_score", 0)
            kd_ratio = stats.get("kill_death_ratio", 0)
            win_percent = (wins / matches_played * 100) if matches_played > 0 else 0

            leaderboard_data.append(
                [
                    idx,
                    name,
                    f"{kd_ratio:.2f}",
                    mmr_value,
                    wins,
                    losses,
                    f"{win_percent:.2f}",
                    f"{avg_cs:.2f}",
                ]
            )

        table_output = t2a(
            header=["Rank", "User", "K/D", "MMR", "Wins", "Losses", "Win%", "Avg ACS"],
            body=leaderboard_data,
            first_col_heading=True,
            style=PresetStyle.thick_compact,
        )

        self.leaderboard_view_kd = LeaderboardViewKD(
            ctx, self.bot, sorted_kd, players_per_page=10, timeout=None
        )

        content = f"## K/D Leaderboard (Page {self.leaderboard_view_kd.current_page+1}/{self.leaderboard_view_kd.total_pages}) ##\n```\n{table_output}\n```"
        self.leaderboard_message_kd = await ctx.send(
            content=content, view=self.leaderboard_view_kd
        )

        # Start the refresh
        if self.refresh_task_kd is not None:
            self.refresh_task_kd.cancel()
        self.refresh_task_kd = asyncio.create_task(self.periodic_refresh_kd())

    @commands.command()
    async def leaderboard_wins(self, ctx):
        if not self.bot.player_mmr:
            await ctx.send("No MMR data available yet.")
            return

        # Sort all players by wins
        sorted_wins = sorted(
            self.bot.player_mmr.items(),
            key=lambda x: x[1].get("wins", 0.0),
            reverse=True,
        )

        # Create the view for pages
        view = LeaderboardView(ctx, self.bot, sorted_wins, players_per_page=10)

        # Calculate the page indexes
        start_index = view.current_page * view.players_per_page
        end_index = start_index + view.players_per_page
        page_data = sorted_wins[start_index:end_index]

        names = []
        leaderboard_data = []
        for player_id, stats in page_data:
            user_data = users.find_one({"discord_id": str(player_id)})
            if user_data:
                riot_name = user_data.get("name", "Unknown")
                riot_tag = user_data.get("tag", "Unknown")
                names.append(f"{riot_name}#{riot_tag}")
            else:
                names.append("Unknown")

        for idx, ((player_id, stats), name) in enumerate(
            zip(page_data, names), start=start_index + 1
        ):
            mmr_value = stats["mmr"]
            wins = stats["wins"]
            losses = stats["losses"]
            matches_played = stats.get("matches_played", wins + losses)
            avg_cs = stats.get("average_combat_score", 0)
            kd_ratio = stats.get("kill_death_ratio", 0)
            win_percent = (wins / matches_played * 100) if matches_played > 0 else 0

            leaderboard_data.append(
                [
                    idx,
                    name,
                    wins,
                    mmr_value,
                    losses,
                    f"{win_percent:.2f}",
                    f"{avg_cs:.2f}",
                    f"{kd_ratio:.2f}",
                ]
            )

        table_output = t2a(
            header=["Rank", "User", "Wins", "MMR", "Losses", "Win%", "Avg ACS", "K/D"],
            body=leaderboard_data,
            first_col_heading=True,
            style=PresetStyle.thick_compact,
        )

        self.leaderboard_view_wins = LeaderboardViewWins(
            ctx, self.bot, sorted_wins, players_per_page=10, timeout=None
        )

        content = f"## Wins Leaderboard (Page {self.leaderboard_view_wins.current_page+1}/{self.leaderboard_view_wins.total_pages}) ##\n```\n{table_output}\n```"
        self.leaderboard_message_wins = await ctx.send(
            content=content, view=self.leaderboard_view_wins
        )  #########

        # Start the refresh
        if self.refresh_task_wins is not None:
            self.refresh_task_wins.cancel()
        self.refresh_task_wins = asyncio.create_task(self.periodic_refresh_wins())

    @commands.command()
    async def leaderboard_ACS(self, ctx):
        if not self.bot.player_mmr:
            await ctx.send("No MMR data available yet.")
            return

        # Sort all players by ACS
        sorted_acs = sorted(
            self.bot.player_mmr.items(),
            key=lambda x: x[1].get("average_combat_score", 0.0),
            reverse=True,
        )

        # Create the view for pages
        view = LeaderboardView(ctx, self.bot, sorted_acs, players_per_page=10)

        start_index = view.current_page * view.players_per_page
        end_index = start_index + view.players_per_page
        page_data = sorted_acs[start_index:end_index]

        names = []
        leaderboard_data = []
        for player_id, stats in page_data:
            user_data = users.find_one({"discord_id": str(player_id)})
            if user_data:
                riot_name = user_data.get("name", "Unknown")
                riot_tag = user_data.get("tag", "Unknown")
                names.append(f"{riot_name}#{riot_tag}")
            else:
                names.append("Unknown")

        for idx, ((player_id, stats), name) in enumerate(
            zip(page_data, names), start=start_index + 1
        ):
            mmr_value = stats["mmr"]
            wins = stats["wins"]
            losses = stats["losses"]
            matches_played = stats.get("matches_played", wins + losses)
            avg_cs = stats.get("average_combat_score", 0)
            kd_ratio = stats.get("kill_death_ratio", 0)
            win_percent = (wins / matches_played * 100) if matches_played > 0 else 0

            leaderboard_data.append(
                [
                    idx,
                    name,
                    f"{avg_cs:.2f}",
                    mmr_value,
                    wins,
                    losses,
                    f"{win_percent:.2f}",
                    f"{kd_ratio:.2f}",
                ]
            )

        table_output = t2a(
            header=["Rank", "User", "Avg ACS", "MMR", "Wins", "Losses", "Win%", "K/D"],
            body=leaderboard_data,
            first_col_heading=True,
            style=PresetStyle.thick_compact,
        )

        self.leaderboard_view_acs = LeaderboardViewACS(
            ctx, self.bot, sorted_acs, players_per_page=10, timeout=None
        )

        content = f"## ACS Leaderboard (Page {self.leaderboard_view_acs.current_page+1}/{self.leaderboard_view_acs.total_pages}) ##\n```\n{table_output}\n```"
        self.leaderboard_message_acs = await ctx.send(
            content=content, view=self.leaderboard_view_acs
        )  #########

        if self.refresh_task_acs is not None:
            self.refresh_task_acs.cancel()
        self.refresh_task_acs = asyncio.create_task(self.periodic_refresh_acs())

    async def periodic_refresh(self):
        await self.bot.wait_until_ready()
        try:
            while True:
                await asyncio.sleep(30)
                if self.leaderboard_message and self.leaderboard_view:
                    # Just edit with the same content and view
                    await self.leaderboard_message.edit(
                        content=self.leaderboard_message.content,
                        view=self.leaderboard_view,
                    )
                else:
                    break
        except asyncio.CancelledError:
            pass

    async def periodic_refresh_kd(self):
        await self.bot.wait_until_ready()
        try:
            while True:
                await asyncio.sleep(30)
                if self.leaderboard_message_kd and self.leaderboard_view_kd:
                    # Just edit message
                    await self.leaderboard_message_kd.edit(
                        content=self.leaderboard_message_kd.content,
                        view=self.leaderboard_view_kd,
                    )
                else:
                    break
        except asyncio.CancelledError:
            pass

    async def periodic_refresh_wins(self):
        await self.bot.wait_until_ready()
        try:
            while True:
                await asyncio.sleep(30)
                if self.leaderboard_message_wins and self.leaderboard_view_wins:
                    await self.leaderboard_message_wins.edit(
                        content=self.leaderboard_message_wins.content,
                        view=self.leaderboard_view_wins,
                    )
                else:
                    break
        except asyncio.CancelledError:
            pass

    async def periodic_refresh_acs(self):
        await self.bot.wait_until_ready()
        try:
            while True:
                await asyncio.sleep(30)
                if self.leaderboard_message_acs and self.leaderboard_view_acs:
                    await self.leaderboard_message_acs.edit(
                        content=self.leaderboard_message_acs.content,
                        view=self.leaderboard_view_acs,
                    )
                else:
                    break
        except asyncio.CancelledError:
            pass
