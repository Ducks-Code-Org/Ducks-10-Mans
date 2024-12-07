"""This file holds all bot commands. <prefix><function_name> is the full command for each function."""

import asyncio
import os
import copy  # To make a copy of player_mmr
import random

import discord
from discord.ext import commands
import requests
from table2ascii import table2ascii as t2a, PresetStyle

from database import users, all_matches, mmr_collection
from stats_helper import update_stats
from views.captains_drafting_view import CaptainsDraftingView
from views.mode_vote_view import ModeVoteView
from views.signup_view import SignupView
from views.leaderboard_view import (
    LeaderboardView,
    LeaderboardViewKD,
    LeaderboardViewACS,
    LeaderboardViewWins,
)

# Initialize API
api_key = os.getenv("api_key")
headers = {
    "Authorization": api_key,
}

# FOR TESTING ONLY, REMEMBER TO SET WINNER AND total_rounds
mock_match_data = {
    "players": [
        {
            "name": "Samurai",
            "tag": "Mai",
            "team_id": "Red",
            "stats": {"score": 7104, "kills": 23, "deaths": 16, "assists": 7},
        },
        {
            "name": "WaffIes",
            "tag": "NA1",
            "team_id": "Red",
            "stats": {"score": 5472, "kills": 18, "deaths": 22, "assists": 3},
        },
        {
            "name": "Konax",
            "tag": "5629",
            "team_id": "Red",
            "stats": {"score": 3984, "kills": 15, "deaths": 16, "assists": 1},
        },
        {
            "name": "Luh4r",
            "tag": "i0n",
            "team_id": "Red",
            "stats": {"score": 3672, "kills": 12, "deaths": 16, "assists": 12},
        },
        {
            "name": "mintychewinggum",
            "tag": "8056",
            "team_id": "Red",
            "stats": {"score": 2784, "kills": 11, "deaths": 18, "assists": 4},
        },
        {
            "name": "TreeTops",
            "tag": "IMH",
            "team_id": "Blue",
            "stats": {"score": 4560, "kills": 14, "deaths": 17, "assists": 10},
        },
        {
            "name": "mizu",
            "tag": "yor",
            "team_id": "Blue",
            "stats": {"score": 7968, "kills": 28, "deaths": 18, "assists": 3},
        },
        {
            "name": "Nisom",
            "tag": "zia",
            "team_id": "Blue",
            "stats": {"score": 7704, "kills": 25, "deaths": 14, "assists": 4},
        },
        {
            "name": "galaxy",
            "tag": "KUJG",
            "team_id": "Blue",
            "stats": {"score": 2952, "kills": 12, "deaths": 16, "assists": 2},
        },
        {
            "name": "dShocc1",
            "tag": "LNEUP",
            "team_id": "Blue",
            "stats": {"score": 2496, "kills": 9, "deaths": 14, "assists": 4},
        },
    ],
    "teams": [
        {"team_id": "Red", "won": True, "rounds_won": 13, "rounds_lost": 11},
        {"team_id": "Blue", "won": False, "rounds_won": 11, "rounds_lost": 13},
    ],
}


class BotCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.dev_mode = False
        # variables related to refreshing the leaderboard
        self.leaderboard_message = None
        self.leaderboard_view = None
        self.refresh_task = None
        self.leaderboard_message_kd = None
        self.leaderboard_view_kd = None
        self.refresh_task_kd = None
        self.leaderboard_message_wins = None
        self.leaderboard_view_wins = None
        self.refresh_task_wins = None
        self.leaderboard_message_acs = None
        self.leaderboard_view_acs = None
        self.refresh_task_acs = None

    # Signup Command
    @commands.command()
    async def signup(self, ctx):
        # Don't create a new signup if one is active
        if self.bot.signup_active:
            await ctx.send(
                "A signup is already in progress. Please wait for it to complete."
            )
            return

        if self.bot.match_not_reported:
            await ctx.send(
                "Report the last match before starting another one (credits to dshocc for bug testing)"
            )

        self.bot.signup_active = True
        self.bot.queue = []

        # Generate Match Name and Setup Match Channel Permissions
        self.bot.match_name = f"match-{random.randrange(1, 10**4):04}"
        self.bot.match_role = await ctx.guild.create_role(
            name=self.bot.match_name, hoist=True
        )
        await ctx.guild.edit_role_positions(positions={self.bot.match_role: 5})
        match_channel_permissions = {
            ctx.guild.default_role: discord.PermissionOverwrite(send_messages=False),
            self.bot.match_role: discord.PermissionOverwrite(send_messages=True),
        }

        # Generate Match Channel and Send Signup Message
        self.bot.match_channel = await ctx.guild.create_text_channel(
            name=self.bot.match_name,
            category=ctx.channel.category,
            position=0,
            overwrites=match_channel_permissions,
        )
        self.bot.current_signup_message = await self.bot.match_channel.send(
            "Click a button to manage your queue status!", view=self.bot.signup_view
        )

        await ctx.send(
            f"Queue started! Signup can be found here: <#{self.bot.match_channel.id}>"
        )

        # Check if we need to create the view
        if self.bot.signup_view is None:
            self.bot.signup_view = SignupView(ctx, self.bot)

    @commands.command()
    async def status(self, ctx):
        if self.bot.signup_view is None:
            await ctx.send("No signup is currently active.")
            return

        if not self.bot.signup_active:
            await ctx.send("No signup is currently active.")
            return

        riot_names = []
        for player in self.bot.queue:
            discord_id = player["id"]
            user_data = users.find_one({"discord_id": str(discord_id)})
            if user_data:
                riot_name = user_data.get("name", "Unknown")
                riot_names.append(riot_name)
            else:
                # INCASE SOMEONE HASN'T LINKED ACCOUNT
                riot_names.append("Unknown")

        queue_status = ", ".join(riot_names)
        await ctx.send(f"Current queue ({len(self.bot.queue)}/10): {queue_status}")
        await ctx.send(f"Match Channel: <#{self.bot.match_channel.id}>")

    # Report the match
    @commands.command()
    async def report(self, ctx):

        current_user = users.find_one({"discord_id": str(ctx.author.id)})
        if not current_user:
            await ctx.send(
                "You need to link your Riot account first using `!linkriot Name#Tag`"
            )
            return

        name = current_user.get("name")
        tag = current_user.get("tag")
        region = "na"
        platform = "pc"

        url = f"https://api.henrikdev.xyz/valorant/v4/matches/{region}/{platform}/{name}/{tag}"
        response = requests.get(url, headers=headers, timeout=30)
        match_data = response.json()
        match = match_data["data"][0]
        metadata = match.get("metadata", {})
        map_name = metadata.get("map", {}).get("name", "").lower()

        testing_mode = False  # TRUE WHILE TESTING

        if testing_mode:
            match = mock_match_data
            self.bot.match_ongoing = True

            # Reconstruct queue, team1, and team2 from mock_match_data
            queue = []
            team1 = []
            team2 = []
            self.bot.team1 = team1
            self.bot.team2 = team2

            for player_data in match["players"]:
                player_name = player_data["name"].lower()
                player_tag = player_data["tag"].lower()

                user = users.find_one({"name": player_name, "tag": player_tag})
                if user:
                    discord_id = int(user["discord_id"])
                    player = {"id": discord_id, "name": player_name}

                    queue.append(player)

                    if player_data["team_id"] == "Red":
                        team1.append(player)
                    else:
                        team2.append(player)

                    if discord_id not in self.bot.player_mmr:
                        self.bot.player_mmr[discord_id] = {
                            "mmr": 1000,
                            "wins": 0,
                            "losses": 0,
                        }
                    self.bot.player_names[discord_id] = player_name
                else:
                    await ctx.send(
                        f"Player {player_name}#{player_tag} is not linked to any Discord account."
                    )
                    return

            # For mocking match data, set to amount of rounds played
            total_rounds = 24
        else:
            if not self.bot.match_ongoing:
                await ctx.send(
                    "No match is currently active, use `!signup` to start one"
                )
                return

            if not self.bot.selected_map:
                await ctx.send("No map was selected for this match.")
                return

            if self.bot.selected_map.lower() != map_name:
                await ctx.send(
                    "Map doesn't match your most recent match. Unable to report it."
                )
                return

            if "data" not in match_data or not match_data["data"]:
                await ctx.send("Could not retrieve match data.")
                return

            match = match_data["data"][0]

            # Get total rounds played from the match data
            teams = match.get("teams", [])
            if teams:
                total_rounds = metadata.get("total_rounds")
            else:
                await ctx.send("No team data found in match data.")
                return

        match_players = match.get("players", [])
        if not match_players:
            await ctx.send("No players found in match data.")
            return

        queue_riot_ids = set()
        for player in self.bot.queue:
            user_data = users.find_one({"discord_id": str(player["id"])})
            if user_data:
                player_name = user_data.get("name").lower()
                player_tag = user_data.get("tag").lower()
                queue_riot_ids.add((player_name, player_tag))

        # get the list of players in the match
        match_player_names = set()
        for player in match_players:
            player_name = player.get("name", "").lower()
            player_tag = player.get("tag", "").lower()
            match_player_names.add((player_name, player_tag))

        if not queue_riot_ids.issubset(match_player_names):
            await ctx.send("The most recent match does not match the 10-man's match.")
            return

        # Determine which team won
        teams = match.get("teams", [])
        if not teams:
            await ctx.send("No team data found in match data.")
            return

        winning_team_id = None
        for team in teams:
            if team.get("won"):
                winning_team_id = team.get("team_id")
                break

        if not winning_team_id:
            await ctx.send("Could not determine the winning team.")
            return

        match_team_players = {"Red": set(), "Blue": set()}

        for player in match_players:
            team_id = player.get("team_id")
            player_name = player.get("name", "").lower()
            player_tag = player.get("tag", "").lower()
            if team_id in match_team_players:
                match_team_players[team_id].add((player_name, player_tag))

        team1_riot_ids = set()
        for player in self.bot.team1:
            user_data = users.find_one({"discord_id": str(player["id"])})
            if user_data:
                player_name = user_data.get("name", "").lower()
                player_tag = user_data.get("tag").lower()
                team1_riot_ids.add((player_name, player_tag))

        team2_riot_ids = set()
        for player in self.bot.team2:
            user_data = users.find_one({"discord_id": str(player["id"])})
            if user_data:
                player_name = user_data.get("name", "").lower()
                player_tag = user_data.get("tag").lower()
                team2_riot_ids.add((player_name, player_tag))

        winning_match_team_players = match_team_players.get(winning_team_id, set())

        if winning_match_team_players == team1_riot_ids:
            winning_team = self.bot.team1
            losing_team = self.bot.team2
        elif winning_match_team_players == team2_riot_ids:
            winning_team = self.bot.team2
            losing_team = self.bot.team1
        else:
            await ctx.send("Could not match the winning team to our teams.")
            return

        for player in winning_team + losing_team:
            self.bot.ensure_player_mmr(player["id"], self.bot.player_names)

        # Get top players
        pre_update_mmr = copy.deepcopy(self.bot.player_mmr)
        sorted_mmr_before = sorted(
            pre_update_mmr.items(), key=lambda x: x[1]["mmr"], reverse=True
        )
        top_mmr_before = sorted_mmr_before[0][1]["mmr"]
        top_players_before = [
            pid for pid, stats in sorted_mmr_before if stats["mmr"] == top_mmr_before
        ]

        # Adjust MMR
        self.bot.adjust_mmr(winning_team, losing_team)
        await ctx.send("MMR Updated!")

        # Update stats for each player
        for player_stats in match_players:
            update_stats(
                player_stats, total_rounds, self.bot.player_mmr, self.bot.player_names
            )

        # Get new top players
        sorted_mmr_after = sorted(
            self.bot.player_mmr.items(), key=lambda x: x[1]["mmr"], reverse=True
        )
        top_mmr_after = sorted_mmr_after[0][1]["mmr"]
        top_players_after = [
            pid for pid, stats in sorted_mmr_after if stats["mmr"] == top_mmr_after
        ]

        # Determine if theres a new top player
        new_top_players = set(top_players_after) - set(top_players_before)
        if new_top_players:
            for new_top_player_id in new_top_players:
                user_data = users.find_one({"discord_id": str(new_top_player_id)})
                if user_data:
                    riot_name = user_data.get("name", "Unknown")
                    riot_tag = user_data.get("tag", "Unknown")
                    await ctx.send(f"{riot_name}#{riot_tag} is now supersonic radiant!")

        # Now save all updates to the database
        self.bot.save_mmr_data()
        await ctx.send("Player stats updated!")

        # Record every match played in a new collection
        all_matches.insert_one(match)

        await asyncio.sleep(5)
        self.bot.match_not_reported = False
        self.bot.match_ongoing = False
        try:
            await self.bot.current_signup_message.delete()
            await self.bot.match_channel.delete()
            await self.bot.match_role.delete()
        except discord.NotFound:
            pass

    # Allow players to check their MMR and stats
    @commands.command()
    async def stats(self, ctx, *, riot_input=None):
        # Allows players to lookup the stats of other players
        if riot_input is not None:
            try:
                riot_name, riot_tag = riot_input.rsplit("#", 1)
            except ValueError:
                await ctx.send("Please provide your Riot ID in the format: `Name#Tag`")
                return
            player_data = users.find_one({"name": str(riot_name), "tag": str(riot_tag)})
            if player_data:
                player_id = int(player_data.get("discord_id"))
            else:
                await ctx.send(
                    "Could not find this player. Please check the name and tag and ensure they have played at least one match."
                )
                return
        else:
            player_id = ctx.author.id

        if player_id in self.bot.player_mmr:
            stats_data = self.bot.player_mmr[player_id]
            mmr_value = stats_data["mmr"]
            wins = stats_data["wins"]
            losses = stats_data["losses"]
            matches_played = stats_data.get("matches_played", wins + losses)
            total_rounds_played = stats_data.get("total_rounds_played", 0)
            avg_cs = stats_data.get("average_combat_score", 0)
            kd_ratio = stats_data.get("kill_death_ratio", 0)
            win_percent = (wins / matches_played) * 100 if matches_played > 0 else 0

            # Get Riot name and tag
            user_data = users.find_one({"discord_id": str(player_id)})
            if user_data:
                riot_name = user_data.get("name", "Unknown")
                riot_tag = user_data.get("tag", "Unknown")
                player_name = f"{riot_name}#{riot_tag}"
            else:
                player_name = ctx.author.name

            # Find leaderboard position
            total_players = len(self.bot.player_mmr)
            sorted_mmr = sorted(
                self.bot.player_mmr.items(), key=lambda x: x[1]["mmr"], reverse=True
            )
            position = None
            slash = "/"
            for idx, (pid, _) in enumerate(sorted_mmr, start=1):
                if pid == player_id:
                    position = idx
                    break

            # Rank 1 tag
            if position == 1:
                position = "*Supersonic Radiant!* (Rank 1)"
                total_players = ""
                slash = ""

            await ctx.send(
                f"**{player_name}'s Stats:**\n"
                f"MMR: {mmr_value}\n"
                f"Rank: {position}{slash}{total_players}\n"
                f"Wins: {wins}\n"
                f"Losses: {losses}\n"
                f"Win%: {win_percent:.2f}%\n"
                f"Matches Played: {matches_played}\n"
                f"Total Rounds Played: {total_rounds_played}\n"
                f"Average Combat Score: {avg_cs:.2f}\n"
                f"Kill/Death Ratio: {kd_ratio:.2f}"
            )
        else:
            await ctx.send(
                "You do not have an MMR yet. Participate in matches to earn one!"
            )

    # Display leaderboard
    @commands.command()
    async def leaderboard(self, ctx):
        sorted_mmr = sorted(
            self.bot.player_mmr.items(), key=lambda x: x[1]["mmr"], reverse=True
        )

        # Create leaderboard data
        leaderboard_data = []
        for idx, (player_id, stats) in enumerate(sorted_mmr[:10], start=1):
            mmr_value = stats["mmr"]
            wins = stats["wins"]
            losses = stats["losses"]
            matches_played = stats.get("matches_played", wins + losses)
            avg_cs = stats.get("average_combat_score", 0)
            kd_ratio = stats.get("kill_death_ratio", 0)
            win_percent = (wins / matches_played) * 100 if matches_played > 0 else 0
            user_data = users.find_one({"discord_id": str(player_id)})
            if user_data:
                riot_name = user_data.get("name", "Unknown")
                riot_tag = user_data.get("tag", "Unknown")
                player_name = f"{riot_name}#{riot_tag}"
            else:
                player_name = "Unknown"
            leaderboard_data.append(
                [
                    idx,
                    player_name,
                    mmr_value,
                    wins,
                    losses,
                    f"{win_percent:.2f}",
                    f"{avg_cs:.2f}",
                    f"{kd_ratio:.2f}",
                ]
            )

        table_output = t2a(
            header=["Rank", "User", "MMR", "Wins", "Losses", "Win%", "Avg ACS", "K/D"],
            body=leaderboard_data,
            first_col_heading=True,
            style=PresetStyle.thick_compact,
        )

        # Create the view
        self.leaderboard_view = LeaderboardView(
            ctx, self.bot, sorted_mmr, players_per_page=10, timeout=None
        )

        content = f"## MMR Leaderboard (Page {self.leaderboard_view.current_page+1}/{self.leaderboard_view.total_pages}) ##\n```\n{table_output}\n```"
        self.leaderboard_message = await ctx.send(
            content=content, view=self.leaderboard_view
        )  #########

        # Start the refresh
        if self.refresh_task is not None:
            self.refresh_task.cancel()
        self.refresh_task = asyncio.create_task(self.periodic_refresh())

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
                    # Just edit with the same content and view
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
                    # Just edit with the same content and view
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
                    # Just edit with the same content and view
                    await self.leaderboard_message_acs.edit(
                        content=self.leaderboard_message_acs.content,
                        view=self.leaderboard_view_acs,
                    )
                else:
                    break
        except asyncio.CancelledError:
            pass

    @commands.command()
    @commands.has_role("Owner")
    async def stop_leaderboard(self, ctx):
        # Stop the refresh
        if self.refresh_task:
            self.refresh_task.cancel()
            self.refresh_task = None
        if self.leaderboard_message:
            await self.leaderboard_message.edit(
                content="Leaderboard closed.", view=None
            )
            self.leaderboard_message = None
            self.leaderboard_view = None
        await ctx.send("Leaderboard closed and refresh stopped.")

    # leaderboard sorted by K/D
    @commands.command()
    async def leaderboard_KD(self, ctx):
        if not self.bot.player_mmr:
            await ctx.send("No MMR data available yet.")
            return

        # Sort all players by MMR
        sorted_kd = sorted(
            self.bot.player_mmr.items(),
            key=lambda x: x[1].get(
                "kill_death_ratio", 0.0
            ),  # Default to 0.0 if key is missing
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
        )  #########

        # Start the refresh
        if self.refresh_task_kd is not None:
            self.refresh_task_kd.cancel()
        self.refresh_task_kd = asyncio.create_task(self.periodic_refresh_kd())

    # Gives a leaderboard sorted by wins
    @commands.command()
    async def leaderboard_wins(self, ctx):
        if not self.bot.player_mmr:
            await ctx.send("No MMR data available yet.")
            return

        # Sort all players by wins
        sorted_wins = sorted(
            self.bot.player_mmr.items(),
            key=lambda x: x[1].get("wins", 0.0),  # Default to 0.0 if key is missing
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

    # Gives a leaderboard sorted by ACS
    @commands.command()
    async def leaderboard_ACS(self, ctx):
        if not self.bot.player_mmr:
            await ctx.send("No MMR data available yet.")
            return

        # Sort all players by ACS
        sorted_acs = sorted(
            self.bot.player_mmr.items(),
            key=lambda x: x[1].get(
                "average_combat_score", 0.0
            ),  # Default to 0.0 if key is missing
            reverse=True,
        )

        # Create the view for pages
        view = LeaderboardView(ctx, self.bot, sorted_acs, players_per_page=10)

        # Calculate the page indexes
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

        # Start the refresh
        if self.refresh_task_acs is not None:
            self.refresh_task_acs.cancel()
        self.refresh_task_acs = asyncio.create_task(self.periodic_refresh_acs())

    @commands.command()
    @commands.has_role("Owner")  # Restrict this command to admins
    async def initialize_rounds(self, ctx):
        result = mmr_collection.update_many(
            {}, {"$set": {"total_rounds_played": 0}}  # Update all documents
        )
        await ctx.send(
            f"Initialized total_rounds_played for {result.modified_count} players."
        )

    # To recalculate average combat score after bug
    @commands.command()
    @commands.has_role("Owner")
    async def recalculate(self, ctx):
        players = mmr_collection.find()
        updated_count = 0
        for player in players:
            player_id = int(player.get("player_id"))
            total_combat_score = player.get("total_combat_score", 0)
            total_rounds_played = player.get("total_rounds_played", 0)

            if total_rounds_played > 0:
                average_combat_score = total_combat_score / total_rounds_played
            else:
                average_combat_score = 0

            # Update the database
            mmr_collection.update_one(
                {"player_id": player_id},
                {"$set": {"average_combat_score": average_combat_score}},
            )

            # Update the in-memory player_mmr dictionary
            if player_id in self.bot.player_mmr:
                self.bot.player_mmr[player_id][
                    "average_combat_score"
                ] = average_combat_score
            else:
                # In case the player is not in player_mmr (should not happen)
                self.bot.player_mmr[player_id] = {
                    "average_combat_score": average_combat_score
                }

            updated_count += 1

        self.bot.load_mmr_data()

        await ctx.send(
            f"Recalculated average combat score for {updated_count} players."
        )

    # Simulate a queue
    @commands.command()
    async def simulate_queue(self, ctx):
        if self.bot.signup_view is None:
            self.bot.signup_view = SignupView(ctx, self.bot)
        if self.bot.signup_active:
            await ctx.send(
                "A signup is already in progress. Resetting queue for simulation."
            )
            self.bot.queue.clear()

        # Add 10 dummy players to the queue
        queue = [{"id": i, "name": f"Player{i}"} for i in range(1, 11)]

        # Assign default MMR to the dummy players and map IDs to names
        for player in queue:
            if player["id"] not in self.bot.player_mmr:
                self.bot.player_mmr[player["id"]] = {
                    "mmr": 1000,
                    "wins": 0,
                    "losses": 0,
                }
            self.bot.player_names[player["id"]] = player["name"]

        self.bot.save_mmr_data()

        self.bot.signup_active = True
        await ctx.send(
            f"Simulated full queue: {', '.join([player['name'] for player in queue])}"
        )

        # Proceed to the voting stage
        await ctx.send("The queue is now full, proceeding to the voting stage.")

        mode_vote = ModeVoteView(ctx, self.bot)
        await mode_vote.send_view()

    # Link Riot Account
    @commands.command()
    async def linkriot(self, ctx, *, riot_input):
        try:
            riot_name, riot_tag = riot_input.rsplit("#", 1)
        except ValueError:
            await ctx.send("Please provide your Riot ID in the format: `Name#Tag`")
            return

        data = requests.get(
            f"https://api.henrikdev.xyz/valorant/v1/account/{riot_name}/{riot_tag}",
            headers=headers,
            timeout=30,
        )
        user = data.json()

        if "data" not in user:
            await ctx.send(
                "Could not find your Riot account. Please check the name and tag."
            )
        else:
            user_data = {
                "discord_id": str(ctx.author.id),
                "name": riot_name,
                "tag": riot_tag,
            }
            users.update_one(
                {"discord_id": str(ctx.author.id)}, {"$set": user_data}, upsert=True
            )
            await ctx.send(
                f"Successfully linked {riot_name}#{riot_tag} to your Discord account."
            )

    # Set captain1
    @commands.command()
    @commands.has_role("blood")
    async def setcaptain1(self, ctx, *, riot_name_tag):
        try:
            riot_name, riot_tag = riot_name_tag.rsplit("#", 1)
        except ValueError:
            await ctx.send("Please provide the Riot ID in the format: `Name#Tag`")
            return

        # Find the player in the queue with matching Riot name and tag
        player_in_queue = None
        for player in self.bot.queue:
            user_data = users.find_one({"discord_id": str(player["id"])})
            if user_data:
                user_riot_name = user_data.get("name", "").lower()
                user_riot_tag = user_data.get("tag", "").lower()
                if (
                    user_riot_name == riot_name.lower()
                    and user_riot_tag == riot_tag.lower()
                ):
                    player_in_queue = player
                    break
        if not player_in_queue:
            await ctx.send(f"{riot_name}#{riot_tag} is not in the queue.")
            return

        if self.bot.captain2 and player_in_queue["id"] == self.bot.captain2["id"]:
            await ctx.send(f"{riot_name}#{riot_tag} is already selected as Captain 2.")
            return

        self.bot.captain1 = player_in_queue
        await ctx.send(f"Captain 1 set to {riot_name}#{riot_tag}")

    # Set captain2
    @commands.command()
    @commands.has_role("blood")
    async def setcaptain2(self, ctx, *, riot_name_tag):
        try:
            riot_name, riot_tag = riot_name_tag.rsplit("#", 1)
        except ValueError:
            await ctx.send("Please provide the Riot ID in the format: `Name#Tag`")
            return

        # Find the player in the queue with matching Riot name and tag
        player_in_queue = None
        for player in self.bot.queue:
            user_data = users.find_one({"discord_id": str(player["id"])})
            if user_data:
                user_riot_name = user_data.get("name", "").lower()
                user_riot_tag = user_data.get("tag", "").lower()
                if (
                    user_riot_name == riot_name.lower()
                    and user_riot_tag == riot_tag.lower()
                ):
                    player_in_queue = player
                    break
        if not player_in_queue:
            await ctx.send(f"{riot_name}#{riot_tag} is not in the queue.")
            return

        if self.bot.captain1 and player_in_queue["id"] == self.bot.captain1["id"]:
            await ctx.send(f"{riot_name}#{riot_tag} is already selected as Captain 1.")
            return

        self.bot.captain2 = player_in_queue
        await ctx.send(f"Captain 2 set to {riot_name}#{riot_tag}")

    # Set the bot to development mode
    @commands.command()
    @commands.has_role("blood")
    async def toggledev(self, ctx):
        if not self.dev_mode:
            self.dev_mode = True
            await ctx.send("Developer Mode Enabled")
            self.bot.command_prefix = "^"
            try:
                await self.bot.change_presence(
                    status=discord.Status.do_not_disturb,
                    activity=discord.Game(name="Bot Maintenance"),
                )
            except discord.HTTPException:
                pass
        else:
            self.dev_mode = False
            await ctx.send("Developer Mode Disabled")
            self.bot.command_prefix = "!"
            try:
                await self.bot.change_presence(
                    status=discord.Status.online, activity=discord.Game(name="10 Mans!")
                )
            except discord.HTTPException:
                pass

    # Stop the signup process, only owner can do this
    @commands.command()
    @commands.has_role("Owner")
    async def cancel(self, ctx):
        if not self.bot.signup_active:
            await ctx.send("No signup is active to cancel")
            return
        if self.bot.current_signup_message:
            self.bot.queue = []
            self.bot.current_signup_message = None
            self.bot.signup_view.cancel_signup_refresh()
            self.bot.signup_active = False
            self.bot.signup_view = None
            await ctx.send("Canceled Signup")
            try:
                await self.bot.match_channel.delete()
                await self.bot.match_role.delete()
            except discord.NotFound:
                pass
        else:
            await ctx.send("Nothing to cancel")

    @commands.command()
    async def force_draft(self, ctx):
        bot_queue = [
            {"name": "Player3", "id": 1},
            {"name": "Player4", "id": 2},
            {"name": "Player5", "id": 3},
            {"name": "Player6", "id": 4},
            {"name": "Player7", "id": 5},
            {"name": "Player8", "id": 6},
            {"name": "Player9", "id": 7},
            {"name": "Player10", "id": 8},
        ]
        for bot in bot_queue:
            self.bot.queue.append(bot)
        draft = CaptainsDraftingView(ctx, self.bot)
        await draft.send_current_draft_view()

    # Custom Help Command
    @commands.command()
    async def help(self, ctx):
        help_embed = discord.Embed(
            title="Help Menu",
            description="Duck's 10 Mans Commands:",
            color=discord.Color.green(),
        )

        # General Commands
        help_embed.add_field(
            name="General Commands",
            value=(
                "**!signup** - Start a signup session for matches.\n"
                "**!status** - View the current queue status.\n"
                "**!report** - Report the most recent match and update MMR.\n"
                "**!stats** - Check your MMR and match stats.\n"
                "**!linkriot** - Link or update your Riot account using `Name#Tag`.\n"
            ),
            inline=False,
        )

        # Leaderboard Commands
        help_embed.add_field(
            name="Leaderboard Commands",
            value=(
                "**!leaderboard** - View the MMR leaderboard.\n"
                "**!leaderboard_KD** - View the K/D leaderboard.\n"
                "**!leaderboard_wins** - View the wins leaderboard.\n"
                "**!leaderboard_ACS** - View the ACS leaderboard.\n"
            ),
            inline=False,
        )

        # Admin Commands
        help_embed.add_field(
            name="Admin Commands",
            value=(
                "**!setcaptain1** - Set Captain 1 using `Name#Tag`.\n"
                "**!setcaptain2** - Set Captain 2 using `Name#Tag`.\n"
                "**!cancel** - Cancel the current signup session.\n"
                "**!toggledev** - Toggle Developer Mode.\n"
                "**!initialize_rounds** - Reset total rounds played for all players.\n"
                "**!recalculate** - Recalculate average combat scores for all players.\n"
                "**!simulate_queue** - Simulate a full queue for testing.\n"
                "**!force_draft** - Force a drafting phase with bots.\n"
            ),
            inline=False,
        )

        # Owner Commands
        help_embed.add_field(
            name="Owner Commands",
            value=(
                "**!stop_leaderboard** - Stop the leaderboard refresh and close the leaderboard.\n"
            ),
            inline=False,
        )

        # Footer
        help_embed.set_footer(text="Use commands with the specified prefix (!).")

        # Send the embedded message
        await ctx.send(embed=help_embed)
