"""This view allows users to see a stats leaderboard of all the users currently in the database."""

import discord
from discord.ui import View, Button
import math
from database import users
from table2ascii import table2ascii as t2a, PresetStyle


class LeaderboardView(View):
    def __init__(self, ctx, bot, sorted_mmr, players_per_page=10, timeout = None):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.bot = bot
        self.sorted_mmr = sorted_mmr
        self.players_per_page = players_per_page
        self.current_page = 0
        self.total_pages = math.ceil(len(self.sorted_mmr) / self.players_per_page)

        # Initialize button states
        # Will be updated on each page change
        self.previous_button.disabled = True  # On first page, can't go back
        self.next_button.disabled = (self.total_pages == 1)  # If only one page, disable Next

    async def update_message(self, interaction: discord.Interaction):
        # Calculate the people on the page
        start_index = self.current_page * self.players_per_page
        end_index = start_index + self.players_per_page
        page_data = self.sorted_mmr[start_index:end_index]

        # make the leaderboard table for the page
        leaderboard_data = []
        names = []
        for player_id, stats in page_data:
            user_data = users.find_one({"discord_id": str(player_id)})
            if user_data:
                riot_name = user_data.get("name", "Unknown")
                riot_tag = user_data.get("tag", "Unknown")
                names.append(f"{riot_name}#{riot_tag}")
            else:
                names.append("Unknown")

        for idx, ((player_id, stats), name) in enumerate(zip(page_data, names), start=start_index + 1):
            mmr_value = stats["mmr"]
            wins = stats["wins"]
            losses = stats["losses"]
            matches_played = stats.get("matches_played", wins + losses)
            avg_cs = stats.get("average_combat_score", 0)
            kd_ratio = stats.get("kill_death_ratio", 0)
            win_percent = (wins / matches_played * 100) if matches_played > 0 else 0

            leaderboard_data.append([
                idx,
                name,
                mmr_value,
                wins,
                losses,
                f"{win_percent:.2f}",
                f"{avg_cs:.2f}",
                f"{kd_ratio:.2f}"
            ])

        table_output = t2a(
            header=["Rank", "User", "MMR", "Wins", "Losses", "Win%", "Avg ACS", "K/D"],
            body=leaderboard_data,
            first_col_heading=True,
            style=PresetStyle.thick_compact,
        )

        content = f"## MMR Leaderboard (Page {self.current_page + 1}/{self.total_pages}) ##\n```\n{table_output}\n```"

        # Update button based on the current page
        self.previous_button.disabled = (self.current_page == 0)
        self.next_button.disabled = (self.current_page == self.total_pages - 1)

        await interaction.response.edit_message(content=content, view=self)

    @discord.ui.button(style=discord.ButtonStyle.blurple, disabled=True, emoji="⏪")
    async def previous_button(self, interaction: discord.Interaction, button: Button):
        self.current_page -= 1
        await self.update_message(interaction)

    # Refresh the leaderboard
    @discord.ui.button(style=discord.ButtonStyle.blurple, emoji="🔄")
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.sorted_mmr = sorted(self.bot.player_mmr.items(), key=lambda x: x[1]["mmr"], reverse=True)

        # Recalculate total_pages if player count changed
        self.total_pages = math.ceil(len(self.sorted_mmr) / self.players_per_page)
        if self.current_page >= self.total_pages:
            self.current_page = max(0, self.total_pages - 1)

        await self.update_message(interaction)

    @discord.ui.button(style=discord.ButtonStyle.blurple, disabled=False, emoji="⏩")
    async def next_button(self, interaction: discord.Interaction, button: Button):
        self.current_page += 1
        await self.update_message(interaction)

class LeaderboardViewKD(View): 
    def __init__(self, ctx, bot, sorted_kd, players_per_page=10, timeout = None):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.bot = bot
        self.sorted_mmr = sorted_kd
        self.players_per_page = players_per_page
        self.current_page = 0
        self.total_pages = math.ceil(len(self.sorted_mmr) / self.players_per_page)

        # Initialize button states
        # Will be updated on each page change
        self.previous_button.disabled = True  # On first page, can't go back
        self.next_button.disabled = (self.total_pages == 1)  # If only one page, disable Next

    async def update_message(self, interaction: discord.Interaction):
        # Calculate the people on the page
        start_index = self.current_page * self.players_per_page
        end_index = start_index + self.players_per_page
        page_data = self.sorted_mmr[start_index:end_index]

        # make the leaderboard table for the page
        leaderboard_data = []
        names = []
        for player_id, stats in page_data:
            user_data = users.find_one({"discord_id": str(player_id)})
            if user_data:
                riot_name = user_data.get("name", "Unknown")
                riot_tag = user_data.get("tag", "Unknown")
                names.append(f"{riot_name}#{riot_tag}")
            else:
                names.append("Unknown")

        for idx, ((player_id, stats), name) in enumerate(zip(page_data, names), start=start_index + 1):
            mmr_value = stats["mmr"]
            wins = stats["wins"]
            losses = stats["losses"]
            matches_played = stats.get("matches_played", wins + losses)
            avg_cs = stats.get("average_combat_score", 0)
            kd_ratio = stats.get("kill_death_ratio", 0)
            win_percent = (wins / matches_played * 100) if matches_played > 0 else 0

            leaderboard_data.append([
                idx,
                name,
                f"{kd_ratio:.2f}",
                mmr_value,
                wins,
                losses,
                f"{win_percent:.2f}",
                f"{avg_cs:.2f}"
            ])

        table_output = t2a(
            header=["Rank", "User", "K/D", "MMR", "Wins", "Losses", "Win%", "Avg ACS"],
            body=leaderboard_data,
            first_col_heading=True,
            style=PresetStyle.thick_compact,
        )

        content = f"## K/D Leaderboard (Page {self.current_page + 1}/{self.total_pages}) ##\n```\n{table_output}\n```"

        # Update button based on the current page
        self.previous_button.disabled = (self.current_page == 0)
        self.next_button.disabled = (self.current_page == self.total_pages - 1)

        await interaction.response.edit_message(content=content, view=self)

    @discord.ui.button(style=discord.ButtonStyle.blurple, disabled=True, emoji="⏪")
    async def previous_button(self, interaction: discord.Interaction, button: Button):
        self.current_page -= 1
        await self.update_message(interaction)

    # Refresh the leaderboard
    @discord.ui.button(style=discord.ButtonStyle.blurple, emoji="🔄")
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.sorted_mmr = sorted(self.bot.player_mmr.items(), key=lambda x: x[1]["mmr"], reverse=True)

        # Recalculate total_pages if player count changed
        self.total_pages = math.ceil(len(self.sorted_mmr) / self.players_per_page)
        if self.current_page >= self.total_pages:
            self.current_page = max(0, self.total_pages - 1)

        await self.update_message(interaction)

    @discord.ui.button(style=discord.ButtonStyle.blurple, disabled=False, emoji="⏩")
    async def next_button(self, interaction: discord.Interaction, button: Button):
        self.current_page += 1
        await self.update_message(interaction)

class LeaderboardViewWins(View): 
    def __init__(self, ctx, bot, sorted_wins, players_per_page=10, timeout = None):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.bot = bot
        self.sorted_mmr = sorted_wins
        self.players_per_page = players_per_page
        self.current_page = 0
        self.total_pages = math.ceil(len(self.sorted_mmr) / self.players_per_page)

        # Initialize button states
        # Will be updated on each page change
        self.previous_button.disabled = True  # On first page, can't go back
        self.next_button.disabled = (self.total_pages == 1)  # If only one page, disable Next

    async def update_message(self, interaction: discord.Interaction):
        # Calculate the people on the page
        start_index = self.current_page * self.players_per_page
        end_index = start_index + self.players_per_page
        page_data = self.sorted_mmr[start_index:end_index]

        # make the leaderboard table for the page
        leaderboard_data = []
        names = []
        for player_id, stats in page_data:
            user_data = users.find_one({"discord_id": str(player_id)})
            if user_data:
                riot_name = user_data.get("name", "Unknown")
                riot_tag = user_data.get("tag", "Unknown")
                names.append(f"{riot_name}#{riot_tag}")
            else:
                names.append("Unknown")

        for idx, ((player_id, stats), name) in enumerate(zip(page_data, names), start=start_index + 1):
            mmr_value = stats["mmr"]
            wins = stats["wins"]
            losses = stats["losses"]
            matches_played = stats.get("matches_played", wins + losses)
            avg_cs = stats.get("average_combat_score", 0)
            kd_ratio = stats.get("kill_death_ratio", 0)
            win_percent = (wins / matches_played * 100) if matches_played > 0 else 0

            leaderboard_data.append([
                idx,
                name,
                wins,
                mmr_value,
                losses,
                f"{win_percent:.2f}",
                f"{avg_cs:.2f}",
                f"{kd_ratio:.2f}"
            ])

        table_output = t2a(
            header=["Rank", "User", "Wins", "MMR", "Losses", "Win%", "Avg ACS", "K/D"],
            body=leaderboard_data,
            first_col_heading=True,
            style=PresetStyle.thick_compact,
        )

        content = f"## Wins Leaderboard (Page {self.current_page + 1}/{self.total_pages}) ##\n```\n{table_output}\n```"

        # Update button based on the current page
        self.previous_button.disabled = (self.current_page == 0)
        self.next_button.disabled = (self.current_page == self.total_pages - 1)

        await interaction.response.edit_message(content=content, view=self)

    @discord.ui.button(style=discord.ButtonStyle.blurple, disabled=True, emoji="⏪")
    async def previous_button(self, interaction: discord.Interaction, button: Button):
        self.current_page -= 1
        await self.update_message(interaction)

    # Refresh the leaderboard
    @discord.ui.button(style=discord.ButtonStyle.blurple, emoji="🔄")
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.sorted_mmr = sorted(self.bot.player_mmr.items(), key=lambda x: x[1]["mmr"], reverse=True)

        # Recalculate total_pages if player count changed
        self.total_pages = math.ceil(len(self.sorted_mmr) / self.players_per_page)
        if self.current_page >= self.total_pages:
            self.current_page = max(0, self.total_pages - 1)

        await self.update_message(interaction)

    @discord.ui.button(style=discord.ButtonStyle.blurple, disabled=False, emoji="⏩")
    async def next_button(self, interaction: discord.Interaction, button: Button):
        self.current_page += 1
        await self.update_message(interaction)

class LeaderboardViewACS(View): 
    def __init__(self, ctx, bot, sorted_acs, players_per_page=10, timeout = None):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.bot = bot
        self.sorted_mmr = sorted_acs
        self.players_per_page = players_per_page
        self.current_page = 0
        self.total_pages = math.ceil(len(self.sorted_mmr) / self.players_per_page)

        # Initialize button states
        # Will be updated on each page change
        self.previous_button.disabled = True  # On first page, can't go back
        self.next_button.disabled = (self.total_pages == 1)  # If only one page, disable Next

    async def update_message(self, interaction: discord.Interaction):
        # Calculate the people on the page
        start_index = self.current_page * self.players_per_page
        end_index = start_index + self.players_per_page
        page_data = self.sorted_mmr[start_index:end_index]

        # make the leaderboard table for the page
        leaderboard_data = []
        names = []
        for player_id, stats in page_data:
            user_data = users.find_one({"discord_id": str(player_id)})
            if user_data:
                riot_name = user_data.get("name", "Unknown")
                riot_tag = user_data.get("tag", "Unknown")
                names.append(f"{riot_name}#{riot_tag}")
            else:
                names.append("Unknown")

        for idx, ((player_id, stats), name) in enumerate(zip(page_data, names), start=start_index + 1):
            mmr_value = stats["mmr"]
            wins = stats["wins"]
            losses = stats["losses"]
            matches_played = stats.get("matches_played", wins + losses)
            avg_cs = stats.get("average_combat_score", 0)
            kd_ratio = stats.get("kill_death_ratio", 0)
            win_percent = (wins / matches_played * 100) if matches_played > 0 else 0

            leaderboard_data.append([
                idx,
                name,
                f"{avg_cs:.2f}",
                mmr_value,
                wins,
                losses,
                f"{win_percent:.2f}",
                f"{kd_ratio:.2f}"
            ])

        table_output = t2a(
            header=["Rank", "User", "Avg ACS", "MMR", "Wins", "Losses", "Win%", "K/D"],
            body=leaderboard_data,
            first_col_heading=True,
            style=PresetStyle.thick_compact,
        )

        content = f"## ACS Leaderboard (Page {self.current_page + 1}/{self.total_pages}) ##\n```\n{table_output}\n```"

        # Update button based on the current page
        self.previous_button.disabled = (self.current_page == 0)
        self.next_button.disabled = (self.current_page == self.total_pages - 1)

        await interaction.response.edit_message(content=content, view=self)

    @discord.ui.button(style=discord.ButtonStyle.blurple, disabled=True, emoji="⏪")
    async def previous_button(self, interaction: discord.Interaction, button: Button):
        self.current_page -= 1
        await self.update_message(interaction)

    # Refresh the leaderboard
    @discord.ui.button(style=discord.ButtonStyle.blurple, emoji="🔄")
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.sorted_mmr = sorted(self.bot.player_mmr.items(), key=lambda x: x[1]["mmr"], reverse=True)

        # Recalculate total_pages if player count changed
        self.total_pages = math.ceil(len(self.sorted_mmr) / self.players_per_page)
        if self.current_page >= self.total_pages:
            self.current_page = max(0, self.total_pages - 1)

        await self.update_message(interaction)

    @discord.ui.button(style=discord.ButtonStyle.blurple, disabled=False, emoji="⏩")
    async def next_button(self, interaction: discord.Interaction, button: Button):
        self.current_page += 1
        await self.update_message(interaction)