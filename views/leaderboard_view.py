"""This view allows users to see a stats leaderboard of all the users currently in the database."""

import math
import discord
from discord.ui import View, Button
from table2ascii import table2ascii as t2a, PresetStyle
from database import users
import wcwidth

def truncate_by_display_width(original_string, max_width=20, ellipsis=True):
    display_len = wcwidth.wcswidth(original_string)
    if display_len <= max_width:
        return original_string

    end_str = "..." if ellipsis else ""
    target_width = max_width - wcwidth.wcswidth(end_str) if ellipsis else max_width

    truncated = ""
    current_width = 0
    for ch in original_string:
        ch_width = wcwidth.wcwidth(ch)
        if not ch_width: 
            ch_width = 1  
        if current_width + ch_width > target_width:
            break
        truncated += ch
        current_width += ch_width

    return truncated + end_str

class LeaderboardView(View):
    def __init__(self, ctx, bot, sorted_mmr, players_per_page=10, timeout=None):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.bot = bot
        self.sorted_mmr = sorted_mmr
        self.players_per_page = players_per_page
        self.current_page = 0
        self.total_pages = math.ceil(len(self.sorted_mmr) / self.players_per_page)

        self.previous_button = Button(
            style=discord.ButtonStyle.blurple,
            emoji="⏪",
            disabled=(self.current_page == 0)
        )
        self.refresh_button = Button(
            style=discord.ButtonStyle.blurple,
            emoji="🔄",
            disabled=False
        )
        self.next_button = Button(
            style=discord.ButtonStyle.blurple,
            emoji="⏩",
            disabled=(self.total_pages == 1)
        )

        self.previous_button.callback = self.on_previous
        self.refresh_button.callback = self.on_refresh
        self.next_button.callback    = self.on_next

        # Add them in the correct order
        self.add_item(self.previous_button)
        self.add_item(self.refresh_button)
        self.add_item(self.next_button)

    async def on_previous(self, interaction: discord.Interaction):
        if self.current_page > 0:
            self.current_page -= 1
        await self.update_message(interaction)

    async def on_refresh(self, interaction: discord.Interaction):
        self.sorted_mmr = sorted(
            self.bot.player_mmr.items(), 
            key=lambda x: x[1]["mmr"], 
            reverse=True
        )
        self.total_pages = math.ceil(len(self.sorted_mmr) / self.players_per_page)
        if self.current_page >= self.total_pages:
            self.current_page = max(0, self.total_pages - 1)
        await self.update_message(interaction)

    async def on_next(self, interaction: discord.Interaction):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
        await self.update_message(interaction)

    async def update_message(self, interaction: discord.Interaction):
        # Build the content for the current page
        start_index = self.current_page * self.players_per_page
        end_index = start_index + self.players_per_page
        page_data = self.sorted_mmr[start_index:end_index]

        # Build the table
        leaderboard_data = []
        for idx, (player_id, stats) in enumerate(page_data, start=start_index+1):
            user_data = users.find_one({"discord_id": str(player_id)})
            if user_data:
                riot_name = user_data.get("name", "Unknown")
                riot_tag  = user_data.get("tag", "Unknown")
                # -- Use our custom truncate_by_display_width function:
                display_name = truncate_by_display_width(f"{riot_name}#{riot_tag}", 20, ellipsis=True)
            else:
                display_name = "Unknown"

            mmr_value   = stats["mmr"]
            wins        = stats["wins"]
            losses      = stats["losses"]
            matches_played = stats.get("matches_played", wins + losses)
            avg_cs      = stats.get("average_combat_score", 0)
            kd_ratio    = stats.get("kill_death_ratio", 0)
            win_percent = (wins / matches_played * 100) if matches_played > 0 else 0

            leaderboard_data.append([
                idx, 
                display_name, 
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
            style=PresetStyle.thick_compact
        )
        content = f"## MMR Leaderboard (Page {self.current_page+1}/{self.total_pages}) ##\n```\n{table_output}\n```"

        # Update button states
        self.previous_button.disabled = (self.current_page == 0)
        self.next_button.disabled = (self.current_page == self.total_pages - 1)

        await interaction.response.edit_message(
            content=content, 
            view=self
        )

class LeaderboardViewKD(View):
    def __init__(self, ctx, bot, sorted_kd, players_per_page=10, timeout=None):
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
        self.next_button.disabled = (
            self.total_pages == 1
        )  # If only one page, disable Next

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

        content = f"## K/D Leaderboard (Page {self.current_page + 1}/{self.total_pages}) ##\n```\n{table_output}\n```"

        # Update button based on the current page
        self.previous_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page == self.total_pages - 1

        await interaction.response.edit_message(content=content, view=self)

    @discord.ui.button(style=discord.ButtonStyle.blurple, disabled=True, emoji="⏪")
    async def previous_button(self, interaction: discord.Interaction):
        self.current_page -= 1
        await self.update_message(interaction)

    # Refresh the leaderboard
    @discord.ui.button(style=discord.ButtonStyle.blurple, emoji="🔄")
    async def refresh_button(self, interaction: discord.Interaction):
        self.sorted_mmr = sorted(
            self.bot.player_mmr.items(),
            key=lambda x: x[1].get(
                "kill_death_ratio", 0.0
            ),  # Default to 0.0 if key is missing
            reverse=True,
        )

        # Recalculate total_pages if player count changed
        self.total_pages = math.ceil(len(self.sorted_mmr) / self.players_per_page)
        if self.current_page >= self.total_pages:
            self.current_page = max(0, self.total_pages - 1)

        await self.update_message(interaction)

    @discord.ui.button(style=discord.ButtonStyle.blurple, disabled=False, emoji="⏩")
    async def next_button(self, interaction: discord.Interaction):
        self.current_page += 1
        await self.update_message(interaction)


class LeaderboardViewWins(View):
    def __init__(self, ctx, bot, sorted_wins, players_per_page=10, timeout=None):
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
        self.next_button.disabled = (
            self.total_pages == 1
        )  # If only one page, disable Next

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

        content = f"## Wins Leaderboard (Page {self.current_page + 1}/{self.total_pages}) ##\n```\n{table_output}\n```"

        # Update button based on the current page
        self.previous_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page == self.total_pages - 1

        await interaction.response.edit_message(content=content, view=self)

    @discord.ui.button(style=discord.ButtonStyle.blurple, disabled=True, emoji="⏪")
    async def previous_button(self, interaction: discord.Interaction):
        self.current_page -= 1
        await self.update_message(interaction)

    # Refresh the leaderboard
    @discord.ui.button(style=discord.ButtonStyle.blurple, emoji="🔄")
    async def refresh_button(self, interaction: discord.Interaction):
        self.sorted_mmr = sorted(
            self.bot.player_mmr.items(),
            key=lambda x: x[1].get("wins", 0.0),  # Default to 0.0 if key is missing
            reverse=True,
        )

        # Recalculate total_pages if player count changed
        self.total_pages = math.ceil(len(self.sorted_mmr) / self.players_per_page)
        if self.current_page >= self.total_pages:
            self.current_page = max(0, self.total_pages - 1)

        await self.update_message(interaction)

    @discord.ui.button(style=discord.ButtonStyle.blurple, disabled=False, emoji="⏩")
    async def next_button(self, interaction: discord.Interaction):
        self.current_page += 1
        await self.update_message(interaction)


class LeaderboardViewACS(View):
    def __init__(self, ctx, bot, sorted_acs, players_per_page=10, timeout=None):
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
        self.next_button.disabled = (
            self.total_pages == 1
        )  # If only one page, disable Next

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

        content = f"## ACS Leaderboard (Page {self.current_page + 1}/{self.total_pages}) ##\n```\n{table_output}\n```"

        # Update button based on the current page
        self.previous_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page == self.total_pages - 1

        await interaction.response.edit_message(content=content, view=self)

    @discord.ui.button(style=discord.ButtonStyle.blurple, disabled=True, emoji="⏪")
    async def previous_button(self, interaction: discord.Interaction):
        self.current_page -= 1
        await self.update_message(interaction)

    # Refresh the leaderboard
    @discord.ui.button(style=discord.ButtonStyle.blurple, emoji="🔄")
    async def refresh_button(self, interaction: discord.Interaction):
        self.sorted_mmr = sorted(
            self.bot.player_mmr.items(),
            key=lambda x: x[1].get(
                "average_combat_score", 0.0
            ),  # Default to 0.0 if key is missing
            reverse=True,
        )

        # Recalculate total_pages if player count changed
        self.total_pages = math.ceil(len(self.sorted_mmr) / self.players_per_page)
        if self.current_page >= self.total_pages:
            self.current_page = max(0, self.total_pages - 1)

        await self.update_message(interaction)

    @discord.ui.button(style=discord.ButtonStyle.blurple, disabled=False, emoji="⏩")
    async def next_button(self, interaction: discord.Interaction):
        self.current_page += 1
        await self.update_message(interaction)
