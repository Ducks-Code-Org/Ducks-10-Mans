"""This view allows users to see a stats leaderboard of all the users currently in the database."""

import math
import discord
from discord.ui import View, Button
from table2ascii import table2ascii as t2a, PresetStyle
from database import users, mmr_collection, tdm_mmr_collection
import wcwidth

def _has_played_normal(doc: dict) -> bool:
    # Normal mode
    mp = doc.get("matches_played")
    if isinstance(mp, (int, float)):
        return mp > 0
    return (doc.get("wins", 0) + doc.get("losses", 0)) > 0

def _has_played_tdm(doc: dict) -> bool:
    # TDM
    return (doc.get("tdm_wins", 0) + doc.get("tdm_losses", 0)) > 0

def truncate_by_display_width(original_string, max_width=15, ellipsis=True):
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

class LeaderboardView(discord.ui.View):
    def __init__(self, ctx, bot, sorted_data, players_per_page=10, timeout=None, mode="normal"):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.bot = bot
        self.players_per_page = players_per_page
        self.current_page = 0
        self.mode = mode  # "normal" or "tdm"
        self.sorted_data = sorted_data

        # Hide users with zero matches
        if self.mode == "tdm":
            self.sorted_data = [d for d in self.sorted_data if _has_played_tdm(d)]
        else:
            self.sorted_data = [d for d in self.sorted_data if _has_played_normal(d)]

        # compute pages after filtering
        self.total_pages = max(1, math.ceil(len(self.sorted_data) / self.players_per_page))

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
        self.toggle_mode_button = discord.ui.Button(
            label="Switch to TDM" if mode == "normal" else "Switch to Normal",
            style=discord.ButtonStyle.green
        )

        self.previous_button.callback = self.on_previous
        self.next_button.callback = self.on_next
        self.refresh_button.callback = self.on_refresh
        self.toggle_mode_button.callback = self.on_toggle_mode

        self.add_item(self.previous_button)
        self.add_item(self.refresh_button)
        self.add_item(self.next_button)
        self.add_item(self.toggle_mode_button)

        print(f"[LB] mode={self.mode} items={len(self.sorted_data)} per_page={self.players_per_page} pages={self.total_pages}")

    async def on_toggle_mode(self, interaction: discord.Interaction):
        new_mode = "tdm" if self.mode == "normal" else "normal"
        collection = tdm_mmr_collection if new_mode == "tdm" else mmr_collection
        
        if new_mode == "tdm":
            sorted_data = sorted(
                collection.find(),
                key=lambda x: x.get("tdm_mmr", 0),
                reverse=True
            )
            sorted_data = [d for d in sorted_data if _has_played_tdm(d)]
        else:
            sorted_data = sorted(
                collection.find(),
                key=lambda x: x.get("mmr", 0),
                reverse=True
            )
            sorted_data = [d for d in sorted_data if _has_played_normal(d)]

        new_view = LeaderboardView(
            self.ctx,
            self.bot,
            sorted_data,
            self.players_per_page,
            timeout=None,
            mode=new_mode
        )

        leaderboard_data = []
        start_index = 0
        end_index = min(self.players_per_page, len(sorted_data))
        
        for idx, player_data in enumerate(sorted_data[start_index:end_index], start=1):
            player_id = str(player_data["player_id"])
            user_data = users.find_one({"discord_id": player_id})
            
            if user_data:
                name = f"{user_data.get('name', 'Unknown')}#{user_data.get('tag', 'Unknown')}"
            else:
                name = "Unknown"

            if new_mode == "tdm":
                mmr = player_data.get("tdm_mmr", 1000)
                wins = player_data.get("tdm_wins", 0)
                losses = player_data.get("tdm_losses", 0)
                avg_kills = player_data.get("tdm_avg_kills", 0)
                kd_ratio = player_data.get("tdm_kd_ratio", 0)
                
                leaderboard_data.append([
                    idx,
                    name,
                    mmr,
                    wins,
                    losses,
                    f"{avg_kills:.1f}",
                    f"{kd_ratio:.2f}"
                ])
            else:
                mmr = player_data.get("mmr", 1000)
                wins = player_data.get("wins", 0)
                losses = player_data.get("losses", 0)
                avg_cs = player_data.get("average_combat_score", 0)
                kd_ratio = player_data.get("kill_death_ratio", 0)
                
                leaderboard_data.append([
                    idx,
                    name,
                    mmr,
                    wins,
                    losses,
                    f"{avg_cs:.2f}",
                    f"{kd_ratio:.2f}"
                ])

        # Create table
        if new_mode == "tdm":
            headers = ["Rank", "User", "TDM MMR", "Wins", "Losses", "Avg Kills", "K/D"]
        else:
            headers = ["Rank", "User", "MMR", "Wins", "Losses", "Avg ACS", "K/D"]

        table_output = t2a(
            header=headers,
            body=leaderboard_data,
            first_col_heading=True,
            style=PresetStyle.thick_compact
        )

        title = "TDM Leaderboard" if new_mode == "tdm" else "10 Mans Leaderboard"
        content = f"## {title} (Page 1/{new_view.total_pages}) ##\n```\n{table_output}\n```"

        # Update message
        await interaction.response.edit_message(content=content, view=new_view)

    async def update_message(self, interaction: discord.Interaction):
        # Calculate page indexes
        start_index = self.current_page * self.players_per_page
        end_index = start_index + self.players_per_page
        page_data = self.sorted_data[start_index:end_index]

        # Build leaderboard data
        leaderboard_data = []
        for idx, stats in enumerate(page_data, start=start_index + 1):
            user_data = users.find_one({"discord_id": str(stats["player_id"])})
            
            if user_data:
                name = f"{user_data.get('name', 'Unknown')}#{user_data.get('tag', 'Unknown')}"
            else:
                name = "Unknown"

            if self.mode == "tdm":
                mmr = stats.get("tdm_mmr", 1000)
                wins = stats.get("tdm_wins", 0)
                losses = stats.get("tdm_losses", 0)
                avg_kills = stats.get("tdm_avg_kills", 0)
                kd_ratio = stats.get("tdm_kd_ratio", 0)
                
                leaderboard_data.append([
                    idx,
                    name,
                    mmr,
                    wins,
                    losses,
                    f"{avg_kills:.1f}",
                    f"{kd_ratio:.2f}"
                ])
            else:
                mmr = stats.get("mmr", 1000)
                wins = stats.get("wins", 0)
                losses = stats.get("losses", 0)
                avg_cs = stats.get("average_combat_score", 0)
                kd_ratio = stats.get("kill_death_ratio", 0)
                
                leaderboard_data.append([
                    idx,
                    name,
                    mmr,
                    wins,
                    losses,
                    f"{avg_cs:.2f}",
                    f"{kd_ratio:.2f}"
                ])

        # Create table header based on mode
        if self.mode == "tdm":
            headers = ["Rank", "User", "TDM MMR", "Wins", "Losses", "Avg Kills", "K/D"]
        else:
            headers = ["Rank", "User", "MMR", "Wins", "Losses", "Avg ACS", "K/D"]

        table_output = t2a(
            header=headers,
            body=leaderboard_data,
            first_col_heading=True,
            style=PresetStyle.thick_compact
        )

        title = "TDM Leaderboard" if self.mode == "tdm" else "10 Mans Leaderboard"
        content = f"## {title} (Page {self.current_page + 1}/{self.total_pages}) ##\n```\n{table_output}\n```"

        # Update button states
        self.previous_button.disabled = (self.current_page == 0)
        self.next_button.disabled = (self.current_page >= self.total_pages - 1)

        await interaction.response.edit_message(content=content, view=self)

    async def on_previous(self, interaction: discord.Interaction):
        if self.current_page > 0:
            self.current_page -= 1
        await self.update_message(interaction)

    async def on_next(self, interaction: discord.Interaction):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
        await self.update_message(interaction)

    async def on_refresh(self, interaction: discord.Interaction):
        collection = tdm_mmr_collection if self.mode == "tdm" else mmr_collection
        
        if self.mode == "tdm":
            self.sorted_data = sorted(
                collection.find(),
                key=lambda x: x.get("tdm_mmr", 0),
                reverse=True
            )
        else:
            self.sorted_data = sorted(
                collection.find(),
                key=lambda x: x.get("mmr", 0),
                reverse=True
            )
        if self.mode == "tdm":
            self.sorted_data = [d for d in self.sorted_data if _has_played_tdm(d)]
        else:
            self.sorted_data = [d for d in self.sorted_data if _has_played_normal(d)]
            
        self.total_pages = math.ceil(len(self.sorted_data) / self.players_per_page)
        if self.current_page >= self.total_pages:
            self.current_page = max(0, self.total_pages - 1)
        await self.update_message(interaction)

class LeaderboardViewKD(View):
    def __init__(self, ctx, bot, sorted_kd, players_per_page=10, timeout=None):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.bot = bot
        self.sorted_mmr = sorted_kd
        self.players_per_page = players_per_page
        self.current_page = 0
        self.total_pages = math.ceil(len(self.sorted_mmr) / self.players_per_page)

        self.previous_button.disabled = True  
        self.next_button.disabled = (
            self.total_pages == 1
        )  # If only one page, disable next

    async def update_message(self, interaction: discord.Interaction):
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
            ),
            reverse=True,
        )

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


        self.previous_button.disabled = True # can't go back
        self.next_button.disabled = (
            self.total_pages == 1
        )  

    async def update_message(self, interaction: discord.Interaction):
        start_index = self.current_page * self.players_per_page
        end_index = start_index + self.players_per_page
        page_data = self.sorted_mmr[start_index:end_index]

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

        self.previous_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page == self.total_pages - 1

        await interaction.response.edit_message(content=content, view=self)

    @discord.ui.button(style=discord.ButtonStyle.blurple, disabled=True, emoji="⏪")
    async def previous_button(self, interaction: discord.Interaction):
        self.current_page -= 1
        await self.update_message(interaction)

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


        self.previous_button.disabled = True 
        self.next_button.disabled = (
            self.total_pages == 1
        )  

    async def update_message(self, interaction: discord.Interaction):
        start_index = self.current_page * self.players_per_page
        end_index = start_index + self.players_per_page
        page_data = self.sorted_mmr[start_index:end_index]

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

        self.previous_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page == self.total_pages - 1

        await interaction.response.edit_message(content=content, view=self)

    @discord.ui.button(style=discord.ButtonStyle.blurple, disabled=True, emoji="⏪")
    async def previous_button(self, interaction: discord.Interaction):
        self.current_page -= 1
        await self.update_message(interaction)

    @discord.ui.button(style=discord.ButtonStyle.blurple, emoji="🔄")
    async def refresh_button(self, interaction: discord.Interaction):
        self.sorted_mmr = sorted(
            self.bot.player_mmr.items(),
            key=lambda x: x[1].get(
                "average_combat_score", 0.0
            ), 
            reverse=True,
        )

        self.total_pages = math.ceil(len(self.sorted_mmr) / self.players_per_page)
        if self.current_page >= self.total_pages:
            self.current_page = max(0, self.total_pages - 1)

        await self.update_message(interaction)

    @discord.ui.button(style=discord.ButtonStyle.blurple, disabled=False, emoji="⏩")
    async def next_button(self, interaction: discord.Interaction):
        self.current_page += 1
        await self.update_message(interaction)
