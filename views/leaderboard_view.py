"""This view allows users to see a stats leaderboard of all the users currently in the database."""

import math

import discord
from discord.ui import View, Button
from table2ascii import table2ascii as t2a, PresetStyle
import wcwidth

from database import users, mmr_collection, tdm_mmr_collection


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


class LeaderboardView(View):
    def __init__(
        self,
        ctx,
        bot,
        sorted_data,
        sort_by,
        players_per_page=10,
        timeout=None,
        mode="normal",
    ):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.bot = bot
        self.sorted_data = sorted_data
        self.sort_by = sort_by
        self.players_per_page = players_per_page
        self.current_page = 0
        self.mode = mode  # "normal" or "tdm"

        # Hide users with zero matches
        if self.mode == "tdm":
            self.sorted_data = [d for d in self.sorted_data if _has_played_tdm(d)]
        else:
            self.sorted_data = [d for d in self.sorted_data if _has_played_normal(d)]

        # compute pages after filtering
        self.total_pages = max(
            1, math.ceil(len(self.sorted_data) / self.players_per_page)
        )
        self.previous_button = Button(
            style=discord.ButtonStyle.blurple,
            emoji="⏪",
            disabled=(self.current_page == 0),
        )
        self.refresh_button = Button(
            style=discord.ButtonStyle.blurple, emoji="🔄", disabled=False
        )
        self.next_button = Button(
            style=discord.ButtonStyle.blurple,
            emoji="⏩",
            disabled=(self.total_pages == 1),
        )
        self.toggle_mode_button = discord.ui.Button(
            label="Switch to TDM" if mode == "normal" else "Switch to Normal",
            style=discord.ButtonStyle.green,
        )

        self.previous_button.callback = self.on_previous
        self.next_button.callback = self.on_next
        self.refresh_button.callback = self.on_refresh
        self.toggle_mode_button.callback = self.on_toggle_mode

        self.add_item(self.previous_button)
        self.add_item(self.refresh_button)
        self.add_item(self.next_button)
        self.add_item(self.toggle_mode_button)

        print(
            f"[LB] mode={self.mode} items={len(self.sorted_data)} per_page={self.players_per_page} pages={self.total_pages}"
        )

    def make_content(self, data, mode, page_count):
        sort_by_to_title = {
            "mmr": "MMR",
            "average_combat_score": "ACS",
            "kill_death_ratio": "K/D",
            "wins": "Wins",
            "losses": "Losses",
            "tdm_mmr": "MMR",
        }

        if mode == "tdm":
            headers = ["Rank", "User", "TDM MMR", "Wins", "Losses", "Avg Kills", "K/D"]
        else:
            headers = ["Rank", "User", "MMR", "Wins", "Losses", "Avg ACS", "K/D"]

        leaderboard_data = []
        start_index = self.current_page * self.players_per_page
        end_index = min((self.current_page + 1) * self.players_per_page, len(data))

        for idx, player_data in enumerate(data[start_index:end_index], start=1):
            player_id = str(player_data["player_id"])
            user_data = users.find_one({"discord_id": player_id})

            if user_data:
                name = f"{user_data.get('name', 'Unknown')}#{user_data.get('tag', 'Unknown')}"
            else:
                name = "Unknown"

            if mode == "tdm":
                mmr = player_data.get("tdm_mmr", 1000)
                wins = player_data.get("tdm_wins", 0)
                losses = player_data.get("tdm_losses", 0)
                avg_kills = player_data.get("tdm_avg_kills", 0)
                kd_ratio = player_data.get("tdm_kd_ratio", 0)

                leaderboard_data.append(
                    [
                        idx + start_index,
                        name,
                        mmr,
                        wins,
                        losses,
                        f"{avg_kills:.1f}",
                        f"{kd_ratio:.2f}",
                    ]
                )
            else:
                mmr = player_data.get("mmr", 1000)
                wins = player_data.get("wins", 0)
                losses = player_data.get("losses", 0)
                avg_cs = player_data.get("average_combat_score", 0)
                kd_ratio = player_data.get("kill_death_ratio", 0)

                leaderboard_data.append(
                    [
                        idx + start_index,
                        name,
                        mmr,
                        wins,
                        losses,
                        f"{avg_cs:.2f}",
                        f"{kd_ratio:.2f}",
                    ]
                )

        table_output = t2a(
            header=headers,
            body=leaderboard_data,
            first_col_heading=True,
            style=PresetStyle.thick_compact,
        )

        title = (
            "TDM Leaderboard"
            if mode == "tdm"
            else f"10 Mans {sort_by_to_title[self.sort_by]} Leaderboard"
        )

        if not leaderboard_data:
            return (
                f"## {title}\n_Play a match for leaderboard statistics to appear here._"
            )

        content = f"## {title} (Page {self.current_page+1}/{page_count}) ##\n```\n{table_output}\n```"
        return content

    async def on_toggle_mode(self, interaction: discord.Interaction):
        new_mode = "tdm" if self.mode == "normal" else "normal"
        collection = tdm_mmr_collection if new_mode == "tdm" else mmr_collection

        if new_mode == "tdm":
            sorted_data = sorted(
                collection.find(), key=lambda x: x.get("tdm_mmr", 0), reverse=True
            )
            sorted_data = [d for d in sorted_data if _has_played_tdm(d)]
        else:
            sorted_data = sorted(
                collection.find(), key=lambda x: x.get(self.sort_by, 0), reverse=True
            )
            sorted_data = [d for d in sorted_data if _has_played_normal(d)]

        new_view = LeaderboardView(
            self.ctx,
            self.bot,
            sorted_data,
            self.sort_by,
            self.players_per_page,
            timeout=None,
            mode=new_mode,
        )

        self.current_page = new_view.current_page

        # Update message
        await interaction.response.edit_message(
            content=self.make_content(sorted_data, new_mode, new_view.total_pages),
            view=new_view,
        )

    async def update_message(self, interaction: discord.Interaction):
        # Update button states
        self.previous_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page >= self.total_pages - 1

        await interaction.response.edit_message(
            content=self.make_content(self.sorted_data, self.mode, self.total_pages),
            view=self,
        )

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
                collection.find(), key=lambda x: x.get("tdm_mmr", 0), reverse=True
            )
        else:
            self.sorted_data = sorted(
                collection.find(), key=lambda x: x.get(self.sort_by, 0), reverse=True
            )
        if self.mode == "tdm":
            self.sorted_data = [d for d in self.sorted_data if _has_played_tdm(d)]
        else:
            self.sorted_data = [d for d in self.sorted_data if _has_played_normal(d)]

        self.total_pages = math.ceil(len(self.sorted_data) / self.players_per_page)
        if self.current_page >= self.total_pages:
            self.current_page = max(0, self.total_pages - 1)
        await self.update_message(interaction)
