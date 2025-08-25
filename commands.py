"""This file holds all bot commands. <prefix><function_name> is the full command for each function."""

import asyncio
import os
import copy 
import random

import discord
from discord.ext import commands
import requests
from table2ascii import table2ascii as t2a, PresetStyle
import wcwidth
import unicodedata

from database import users, all_matches, mmr_collection, tdm_mmr_collection, seasons, interests
from views.interest_view import InterestView

from stats_helper import update_stats
from views.captains_drafting_view import CaptainsDraftingView
from views.mode_vote_view import ModeVoteView
from views.signup_view import SignupView
from views.leaderboard_view import (
    LeaderboardView,
    LeaderboardViewKD,
    LeaderboardViewACS,
    LeaderboardViewWins,
    truncate_by_display_width
)

from zoneinfo import ZoneInfo
from datetime import datetime, timezone, timedelta

from urllib.parse import quote
from pymongo import ReturnDocument

import aiohttp
from riot_api import get_account_by_puuid, get_account_by_riot_id
from identity import ensure_current_riot_identity

# Initialize API
api_key = os.getenv("api_key")
headers = {
    "Authorization": api_key,
}

def _aware_utc(dt):
        if not dt:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

def canonical_riot(name: str, tag: str) -> str:
    def norm(s: str) -> str:
        return unicodedata.normalize("NFKC", (s or "")).casefold().strip()
    return f"{norm(name)}#{norm(tag)}"

# FOR TESTING ONLY, REMEMBER TO SET WINNER AND total_rounds
mock_match_data = {
    "players": [
        {
            "name": "Samurai",
            "tag": "Mai",
            "team_id": "red",
            "stats": {"score": 8136, "kills": 29, "deaths": 16, "assists": 8},
        },
        {
            "name": "WaffIes",
            "tag": "NA1",
            "team_id": "red",
            "stats": {"score": 6048, "kills": 20, "deaths": 20, "assists": 6},
        },
        {
            "name": "DeagleG",
            "tag": "Y33T",
            "team_id": "red",
            "stats": {"score": 5928, "kills": 24, "deaths": 14, "assists": 13},
        },
        {
            "name": "TheAlphaEw0k",
            "tag": "MST",
            "team_id": "red",
            "stats": {"score": 5688, "kills": 21, "deaths": 18, "assists": 3},
        },
        {
            "name": "dShocc1",
            "tag": "LNEUP",
            "team_id": "red",
            "stats": {"score": 1368, "kills": 3, "deaths": 15, "assists": 12},
        },
        {
            "name": "Nisom",
            "tag": "zia",
            "team_id": "blue",
            "stats": {"score": 8424, "kills": 30, "deaths": 19, "assists": 5},
        },
        {
            "name": "mizu",
            "tag": "yor",
            "team_id": "blue",
            "stats": {"score": 7368, "kills": 26, "deaths": 20, "assists": 3},
        },
        {
            "name": "Duck",
            "tag": "MST",
            "team_id": "blue",
            "stats": {"score": 3528, "kills": 11, "deaths": 19, "assists": 5},
        },
        {
            "name": "twentytwo",
            "tag": "4249",
            "team_id": "blue",
            "stats": {"score": 3240, "kills": 12, "deaths": 16, "assists": 3},
        },
        {
            "name": "mintychewinggum",
            "tag": "8056",
            "team_id": "blue",
            "stats": {"score": 1656, "kills": 4, "deaths": 21, "assists": 11},
        },
    ],
    "teams": [
        {"team_id": "red", "won": True, "rounds_won": 13, "rounds_lost": 11},
        {"team_id": "blue", "won": False, "rounds_won": 11, "rounds_lost": 13},
    ],
}


class BotCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.dev_mode = False
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

        self.bot.chosen_mode = None
        self.bot.selected_map = None
        self.bot.match_not_reported = False
        self.bot.match_ongoing = False
        self.bot.player_names = {}
        self.bot.signup_active = False
        self.bot.queue = []
        self.bot.captain1 = None
        self.bot.captain2 = None
        self.bot.team1 = []
        self.bot.team2 = []
        print("[DEBUG] Checking the last match document in 'matches' DB for total rounds via 'rounds' array:")
        last_match_doc = all_matches.find_one(sort=[("_id", -1)])  
        if last_match_doc:
            rounds_array = last_match_doc.get("rounds", [])
            print(f"  [DEBUG DB] The last match in 'matches' had {len(rounds_array)} rounds (via last_match_doc['rounds']).")
        else:
            print("  [DEBUG DB] No matches found in the 'matches' collection.")
    
    async def _ensure_perms(self, ctx) -> bool:
        me = ctx.guild.me
        missing = []
        if not me.guild_permissions.manage_roles:
            missing.append("Manage Roles")
        if not me.guild_permissions.manage_channels:
            missing.append("Manage Channels")
        if missing:
            await ctx.send(f"I need the following permissions in this server: {', '.join(missing)}")
            return False
        return True
    
    @commands.command()
    async def signup(self, ctx):
        if not await self._ensure_perms(ctx):
            return
        
        if self.bot.signup_active:
            await ctx.send("A signup is already in progress.")
            return

        if self.bot.match_not_reported:
            await ctx.send("Report the last match before starting another one.")
            return
        
        ok, msg, db_user = await ensure_current_riot_identity(ctx.author.id)
        if not ok:
            await ctx.send(msg)
            return

        self.bot.load_mmr_data()
        print("[DEBUG] Reloaded MMR data at start of signup")

        # Clear any existing signup view and state
        if self.bot.signup_view is not None:
            self.bot.signup_view.cancel_signup_refresh()
            self.bot.signup_view = None

        # Reset all match related states
        self.bot.signup_active = True
        self.bot.queue = []
        self.bot.captain1 = None
        self.bot.captain2 = None
        self.bot.team1 = []
        self.bot.team2 = []
        self.bot.chosen_mode = None
        self.bot.selected_map = None

        self.bot.match_name = f"match-{random.randrange(1, 10**4):04}"

        try:
            self.bot.match_role = await ctx.guild.create_role(name=self.bot.match_name, hoist=True)
            await ctx.guild.edit_role_positions(positions={self.bot.match_role: 5})

            match_channel_permissions = {
                ctx.guild.default_role: discord.PermissionOverwrite(send_messages=False),
                self.bot.match_role: discord.PermissionOverwrite(send_messages=True),
            }

            self.bot.match_channel = await ctx.guild.create_text_channel(
                name=self.bot.match_name,
                category=ctx.channel.category,
                position=0,
                overwrites=match_channel_permissions,
            )

            # Create new signup view
            from views.signup_view import SignupView
            self.bot.signup_view = SignupView(ctx, self.bot)

            self.bot.current_signup_message = await self.bot.match_channel.send(
                "Click a button to manage your queue status!", view=self.bot.signup_view
            )

            await ctx.send(f"Queue started! Signup: <#{self.bot.match_channel.id}>")
        except Exception as e:
            # Cleanup
            self.bot.signup_active = False
            if hasattr(self.bot, 'match_role') and self.bot.match_role:
                try:
                    await self.bot.match_role.delete()
                except:
                    pass
            if hasattr(self.bot, 'match_channel') and self.bot.match_channel:
                try:
                    await self.bot.match_channel.delete()
                except:
                    pass
            await ctx.send(f"Error setting up queue: {str(e)}")

    def _parse_when_to_utc(self, text: str):
        if not text:
            return None, "Provide a time, e.g. `!interest 9pm` or `!interest tomorrow 7`."

        tz = ZoneInfo("America/Chicago")
        now_local = datetime.now(tz)

        s = text.strip().lower()
        # Relative: "in 2h", "in 45m"
        if s.startswith("in "):
            parts = s[3:].strip()
            mins = 0
            try:
                if parts.endswith("h"):
                    hrs = float(parts[:-1].strip())
                    mins = int(hrs * 60)
                elif parts.endswith("m"):
                    mins = int(parts[:-1].strip())
                else:
                    hrs, mins_part = 0, 0
                    if "h" in parts:
                        h_chunk, rest = parts.split("h", 1)
                        hrs = int(h_chunk.strip() or 0)
                        parts = rest.strip()
                    if parts.endswith("m"):
                        mins_part = int(parts[:-1].strip() or 0)
                    mins = hrs * 60 + mins_part
                target_local = now_local + timedelta(minutes=mins)
                return target_local.astimezone(timezone.utc), None
            except Exception:
                return None, "Couldn’t parse relative time. Try `in 2h` or `in 45m`."

        # Normalize helper functions
        def try_formats(candidate, fmts):
            for f in fmts:
                try:
                    return datetime.strptime(candidate, f)
                except Exception:
                    pass
            return None

        tokens = s.split()
        base_date = now_local.date()

        # Handle "today" / "tomorrow"
        if tokens and tokens[0] in {"today", "tomorrow"}:
            if tokens[0] == "tomorrow":
                base_date = base_date + timedelta(days=1)
            time_str = " ".join(tokens[1:]).strip()
            if not time_str:
                return None, "Add a time after today/tomorrow, e.g. `tomorrow 9pm`."

            t_try = try_formats(
                time_str,
                ["%I%p", "%I:%M%p", "%H:%M", "%H"]
            )
            if not t_try:
                return None, "Couldn’t parse time. Try formats like `9pm`, `9:30pm`, `21:00`."
            dt_local = datetime(
                base_date.year, base_date.month, base_date.day,
                t_try.hour, t_try.minute, tzinfo=tz
            )
            return dt_local.astimezone(timezone.utc), None

        only_time = try_formats(
            s,
            ["%I%p", "%I:%M%p", "%H:%M", "%H"]
        )
        if only_time:
            dt_local = datetime(
                base_date.year, base_date.month, base_date.day,
                only_time.hour, only_time.minute, tzinfo=tz
            )
            if dt_local < now_local:
                dt_local = dt_local + timedelta(days=1)
            return dt_local.astimezone(timezone.utc), None

        default_hour, default_minute = 21, 0

        dt = try_formats(s, ["%Y-%m-%d %H:%M", "%Y-%m-%d %I:%M%p", "%Y-%m-%d %I%p", "%Y-%m-%d"])
        if dt:
            if dt.hour == 0 and len(s.strip().split()) == 1:
                dt = dt.replace(hour=default_hour, minute=default_minute)
            return dt.replace(tzinfo=tz).astimezone(timezone.utc), None
        
        for date_sep in ["/", "-"]:
            try:
                if " " in s:
                    date_part, time_part = s.split(" ", 1)
                else:
                    date_part, time_part = s, ""

                if date_sep in date_part:
                    m, d = [int(x) for x in date_part.split(date_sep)]
                    y = now_local.year
                    
                    base = datetime(y, m, d, tzinfo=tz)
                    if not time_part:
                        dt_local = base.replace(hour=default_hour, minute=default_minute)
                    else:
                        t_try = try_formats(time_part.strip(), ["%I%p", "%I:%M%p", "%H:%M", "%H"])
                        if not t_try:
                            return None, "Couldn’t parse the time. Try `8/22 9pm` or `8-22 21:00`."
                        dt_local = datetime(y, m, d, t_try.hour, t_try.minute, tzinfo=tz)
                    return dt_local.astimezone(timezone.utc), None
            except Exception:
                pass
        return None, "Couldn’t understand that time. Examples: `9pm`, `tomorrow 7`, `8/22 9:30pm`, `in 2h`."

    @commands.command(name="interest")
    async def interest(self, ctx, *, when: str = None):
        """
        Plan a time to run Duck's 10 Mans and open a Join/Leave interest view.

        Usage:
          !interest 9pm
          !interest tomorrow 7
          !interest 8/22 9:30pm
          !interest in 2h
          !interest list
        """
        if when is None:
            await ctx.send("Usage: `!interest <when>` (e.g., `!interest 9pm`) or `!interest list`.")
            return

        if when.strip().lower() in {"list", "ls"}:
            now_utc = datetime.now(timezone.utc)
            upcoming = list(interests.find({"scheduled_at_utc": {"$gte": now_utc}}).sort("scheduled_at_utc", 1).limit(8))
            if not upcoming:
                await ctx.send("No upcoming interest slots yet. Create one with `!interest 9pm`.")
                return

            tz = ZoneInfo("America/Chicago")
            lines = []
            for doc in upcoming:
                t_utc = doc.get("scheduled_at_utc")
                stamp = int(t_utc.timestamp())
                count = len(doc.get("interested_ids", []))
                t_local = t_utc.astimezone(tz)
                lines.append(f"• **{t_local.strftime('%Y-%m-%d %I:%M %p %Z')}** — <t:{stamp}:R>  (**{count}** interested)")
            await ctx.send("**Upcoming 10 Mans interest slots:**\n" + "\n".join(lines))
            return

        dt_utc, err = self._parse_when_to_utc(when)
        if err:
            await ctx.send(err)
            return

        # Round to 5 minutes
        rounded = dt_utc.replace(second=0, microsecond=0)
        minute = (rounded.minute // 5) * 5
        rounded = rounded.replace(minute=minute)

        # Ensure the doc exists
        doc = interests.find_one_and_update(
            {"scheduled_at_utc": rounded},
            {
                "$setOnInsert": {
                    "scheduled_at_utc": rounded,
                    "created_by": str(ctx.author.id),
                    "created_at_utc": datetime.now(timezone.utc),
                },
                "$addToSet": {"interested_ids": str(ctx.author.id)},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )

        view = InterestView(rounded, timeout=None)
        tz = ZoneInfo("America/Chicago")
        embed = discord.Embed(
            description="Creating interest slot…",
            color=discord.Color.green()
        )
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg 

        await view._render(await ctx.fetch_message(msg.id))

    async def cleanup_match_resources(self):
        try:
            if hasattr(self.bot, 'match_channel') and self.bot.match_channel:
                try:
                    await self.bot.match_channel.delete()
                except discord.NotFound:
                    print("[DEBUG] Match channel already deleted")
                except discord.Forbidden:
                    print("[DEBUG] Missing permissions to delete match channel")
                finally:
                    self.bot.match_channel = None

            if hasattr(self.bot, 'match_role') and self.bot.match_role:

                try:
                    for member in self.bot.match_role.members:
                        await member.remove_roles(self.bot.match_role)
                except discord.HTTPException:
                    print("[DEBUG] Error removing roles from members")

                # delete the role
                try:
                    await self.bot.match_role.delete()
                except discord.NotFound:
                    print("[DEBUG] Match role already deleted")
                except discord.Forbidden:
                    print("[DEBUG] Missing permissions to delete match role")
                finally:
                    self.bot.match_role = None

            self.bot.match_not_reported = False
            self.bot.match_ongoing = False
            self.bot.queue.clear()
            
            if self.bot.current_signup_message:
                try:
                    await self.bot.current_signup_message.delete()
                except discord.NotFound:
                    pass
                finally:
                    self.bot.current_signup_message = None

        except Exception as e:
            print(f"[DEBUG] Error during cleanup: {str(e)}")
    
    @commands.command(name="newseason")
    @commands.has_permissions(administrator=True)
    async def new_season(self, ctx, *, no_reset: str = None):
        """
        Creates a new season that ends exactly 2 calendar months from now (UTC).
        By default, resets everyone’s MMR + stats. If you pass 'noreset', it will keep stats.
        Usage: !newseason    (resets)
            !newseason noreset
        """
        reset = True
        if no_reset and no_reset.lower() in {"noreset", "keep", "false", "0"}:
            reset = False

        doc = self.bot.create_new_season(reset_player_stats=reset)

        start_cst = doc["started_at_cst"]   
        end_cst   = doc["ends_at_cst"]

        start_str = start_cst.strftime("%Y-%m-%d %I:%M %p %Z")
        end_str   = end_cst.strftime("%Y-%m-%d %I:%M %p %Z")

        await ctx.send(
            f"**Season {doc['season_number']}** created.\n"
            f"Starts (Central): `{start_str}`\n"
            f"Ends (Central):   `{end_str}`\n"
            f"{'All player MMR + stats were reset.' if reset else 'Player stats were preserved (no reset).'}"
        )

    # Report the match
    @commands.command()
    async def report(self, ctx):
        # linkage check
        current_user = users.find_one({"discord_id": str(ctx.author.id)})
        if not current_user:
            await ctx.send("You need to link your Riot account first using `!linkriot Name#Tag`")
            return

        name = (current_user.get("name") or "").lower().strip()
        tag  = (current_user.get("tag")  or "").lower().strip()
        if not name or not tag:
            await ctx.send("Your Riot account looks incomplete. Re-link with `!linkriot Name#Tag`.")
            return

        if not self.bot.match_ongoing:
            await ctx.send("No match is currently active, use `!signup` to start one")
            return
        if not self.bot.selected_map:
            await ctx.send("No map was selected for this match.")
            return

        def _norm_map(s: str) -> str:
            m = (s or "").strip().lower()
            aliases = {
                "ice box": "icebox",
                "the abyss": "abyss",
                "fracc": "fracture",
            }
            return aliases.get(m, m)

        from urllib.parse import quote
        region, platform = "na", "pc"
        q_name, q_tag = quote(name, safe=""), quote(tag, safe="")
        url = f"https://api.henrikdev.xyz/valorant/v4/matches/{region}/{platform}/{q_name}/{q_tag}"

        try:
            resp = requests.get(url, headers=headers, timeout=30)  # `headers` is defined at top of commands.py
        except requests.RequestException as e:
            await ctx.send(f"Network error reaching HenrikDev API: {e}")
            return

        if resp.status_code == 401:
            await ctx.send("HenrikDev API rejected the request (401). Check that your API key is valid.")
            return
        if resp.status_code == 404:
            await ctx.send("No recent matches found for your Riot ID (404).")
            return
        if resp.status_code == 429:
            await ctx.send("Rate limit hit (429). Try again in a bit.")
            return
        if resp.status_code == 503:
            await ctx.send("Riot/HenrikDev upstream is temporarily unavailable (503). Try again later.")
            return
        if resp.status_code != 200:
            await ctx.send(f"Unexpected error from API ({resp.status_code}).")
            return

        data = resp.json()
        if not isinstance(data, dict) or "data" not in data or not data["data"]:
            await ctx.send("Could not retrieve match data.")
            return

        match = data["data"][0]
        metadata = match.get("metadata") or {}

        map_field = metadata.get("map")
        if isinstance(map_field, dict):
            api_map = _norm_map(map_field.get("name", ""))
        else:
            api_map = _norm_map(map_field or "")

        if _norm_map(self.bot.selected_map) != api_map:
            await ctx.send("Map doesn't match your most recent match. Unable to report it.")
            return

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
            self.bot.queue = queue

            for player_data in match["players"]:
                player_name = player_data["name"].lower()
                player_tag = player_data["tag"].lower()

                user = users.find_one({"name": player_name, "tag": player_tag})
                if user:
                    discord_id = user["discord_id"]
                    player = {"id": discord_id, "name": player_name}

                    queue.append(player)

                    if player_data["team_id"] == "red":
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

            # FOR TESTING PURPOSES
            # self.bot.selected_map = map_name

            if _norm_map(self.bot.selected_map) != api_map:
                await ctx.send(
                    "Map doesn't match your most recent match. Unable to report it."
                )
                return

            # Get total rounds played from the match data
            teams = match.get("teams", [])
            if teams:
                total_rounds = (
                    metadata.get("rounds_played")
                    or metadata.get("total_rounds")
                )
                if not total_rounds:
                    rounds_data = match.get("rounds") or []
                    total_rounds = len(rounds_data)
                total_rounds = int(total_rounds)
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
        
        print(f"[DEBUG] Queued players RIOT ID's: {queue_riot_ids}")

        # get the list of players in the match
        match_player_names = set()
        for player in match_players:
            player_name = player.get("name", "").lower()
            player_tag = player.get("tag", "").lower()
            match_player_names.add((player_name, player_tag))

        print(f"[DEBUG] match_player_names from API: {match_player_names}")

        if not queue_riot_ids.issubset(match_player_names):
            # Find which players don't match
            missing_players = queue_riot_ids - match_player_names
            mismatch_message = "The most recent match does not match the 10-man's match.\n\n"
            mismatch_message += "The following players' Riot IDs don't match the game data:\n"
            
            for name, tag in missing_players:
                mismatch_message += f"• {name}#{tag}\n"
            
            mismatch_message += "\nPossible reasons:\n"
            mismatch_message += "1. Did you or someone make a change to their Riot name/tag?\n"
            mismatch_message += "2. Are you trying to report the correct match?\n\n"
            mismatch_message += "If you changed your Riot ID, please use `!linkriot NewName#NewTag` to update it."
            
            await ctx.send(mismatch_message)
            return

        # Determine which team won
        teams = match.get("teams", [])
        if not teams:
            await ctx.send("No team data found in match data.")
            return

        winning_team_id = None
        for team in teams:
            if team.get("won"):
                winning_team_id = team.get("team_id", "").lower()
                break
        
        print(f"[DEBUG]: Winning team: {winning_team_id}")
        if not winning_team_id:
            await ctx.send("Could not determine the winning team.")
            return

        match_team_players = {"red": set(), "blue": set()}
        for player_info in match_players:
            raw_team_id = player_info.get("team_id", "").lower()  # "red" or "blue"
            p_name = player_info.get("name", "").lower()
            p_tag = player_info.get("tag", "").lower()
            if raw_team_id in match_team_players:
                match_team_players[raw_team_id].add((p_name, p_tag))

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

        print(f"[DEBUG] team1_riot_ids: {team1_riot_ids}")
        print(f"[DEBUG] team2_riot_ids: {team2_riot_ids}")

        winning_match_team_players = match_team_players.get(winning_team_id, set())
        print(f"[DEBUG] Winning team Riot ID's: {winning_match_team_players}")

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
            player_id = str(player["id"])
            self.bot.ensure_player_mmr(player_id, self.bot.player_names)

        # Get top players
        self.bot.player_mmr = {str(k): v for k, v in self.bot.player_mmr.items()}
        pre_update_mmr = copy.deepcopy(self.bot.player_mmr)

        valid_mmr_entries = [
            (pid, stats) for pid, stats in pre_update_mmr.items()
            if isinstance(stats, dict) and "mmr" in stats
        ]

        if valid_mmr_entries:
            sorted_mmr_before = sorted(valid_mmr_entries, key=lambda x: x[1]["mmr"], reverse=True)
            top_mmr_before = sorted_mmr_before[0][1]["mmr"]
            top_players_before = [str(pid) for pid, stats in sorted_mmr_before if stats["mmr"] == top_mmr_before]
        else:
            top_mmr_before = 1000
            top_players_before = []

        # Helper
        riot_to_teamlabel = {}
        for p in self.bot.team1:
            u = users.find_one({"discord_id": str(p["id"])})
            if u:
                riot_to_teamlabel[(u.get("name", "").lower(), u.get("tag", "").lower())] = "team1"
        for p in self.bot.team2:
            u = users.find_one({"discord_id": str(p["id"])})
            if u:
                riot_to_teamlabel[(u.get("name", "").lower(), u.get("tag", "").lower())] = "team2"

        team1_ids = [str(p["id"]) for p in self.bot.team1]
        team2_ids = [str(p["id"]) for p in self.bot.team2]

        def _mmr_of(pid):
            d = pre_update_mmr.get(pid)
            if isinstance(d, dict):
                return int(d.get("mmr", 1000))
            return 1000

        self.team1_mmr = sum(_mmr_of(pid) for pid in team1_ids)
        self.team2_mmr = sum(_mmr_of(pid) for pid in team2_ids)

        riot_to_api_color = {}
        for p in match_players:
            nm = (p.get("name") or "").lower()
            tg = (p.get("tag") or "").lower()
            color = (p.get("team_id") or "").lower()
            riot_to_api_color[(nm, tg)] = color

        # Helper to get the API color
        def _team_api_color(team_players):
            for pl in team_players:
                u = users.find_one({"discord_id": str(pl["id"])})
                if u:
                    key = (u.get("name", "").lower(), u.get("tag", "").lower())
                    return riot_to_api_color.get(key)
            return None

        team1_api_color = _team_api_color(self.bot.team1)
        team2_api_color = _team_api_color(self.bot.team2)

        api_rounds = {}
        for t in teams:
            tid = (t.get("team_id") or "").lower()
            rw = int(t.get("rounds_won") or t.get("rounds", 0) or 0)
            api_rounds[tid] = rw

        self.team1_rounds = int(api_rounds.get(team1_api_color, 0))
        self.team2_rounds = int(api_rounds.get(team2_api_color, 0))
        round_diff_val = abs(self.team1_rounds - self.team2_rounds)
        self.winning_team = "team1" if winning_match_team_players == team1_riot_ids else "team2"
        
        # Update stats for each player
        for player_stats in match_players:
            p_name = (player_stats.get("name") or "").lower()
            p_tag  = (player_stats.get("tag")  or "").lower()
            team_label = riot_to_teamlabel.get((p_name, p_tag))
            if not team_label:
                continue

            update_stats(
                player_stats,
                total_rounds,
                self.bot.player_mmr,
                self.bot.player_names,
                team_sum_mmr=self.team1_mmr if team_label == "team1" else self.team2_mmr,
                opp_sum_mmr=self.team2_mmr if team_label == "team1" else self.team1_mmr,
                team_won=(self.winning_team == team_label),
                round_diff=round_diff_val
            )
        print("[DEBUG] Basic stats updated")

        # Adjust MMR once
        # self.bot.adjust_mmr(winning_team, losing_team)
        # print("[DEBUG] MMR adjusted")
        await ctx.send("Match stats and MMR updated!")

        self.bot.save_mmr_data()
        print("[DEBUG] MMR data saved")

        self.bot.load_mmr_data()  # Reload the MMR data
        print("[DEBUG] Reloaded MMR data after save")

        # save all updates to the database
        print("Before player stats updated")
        

        for discord_id, stats in self.bot.player_mmr.items():
            # Get the riot name for the player
            user_data = users.find_one({"discord_id": str(discord_id)})
            if user_data:
                riot_name = f"{user_data.get('name', 'Unknown')}#{user_data.get('tag', 'Unknown')}"
            else:
                riot_name = "Unknown"

            complete_stats = {
                "mmr": stats.get("mmr", 1000),
                "wins": stats.get("wins", 0),
                "losses": stats.get("losses", 0),
                "name": riot_name,
                "total_combat_score": stats.get("total_combat_score", 0),
                "total_kills": stats.get("total_kills", 0),
                "total_deaths": stats.get("total_deaths", 0),
                "matches_played": stats.get("matches_played", 0),
                "total_rounds_played": stats.get("total_rounds_played", 0),
                "average_combat_score": stats.get("average_combat_score", 0),
                "kill_death_ratio": stats.get("kill_death_ratio", 0)
            }

            mmr_collection.update_one(
                {"player_id": discord_id},
                {"$set": complete_stats},
                upsert=True
            )

        print("[DEBUG] All stats saved to database")

        sorted_mmr_after = sorted(self.bot.player_mmr.items(), key=lambda x: x[1]["mmr"], reverse=True)
        top_mmr_after = sorted_mmr_after[0][1]["mmr"]
        top_players_after = [pid for pid, stats in sorted_mmr_after if stats["mmr"] == top_mmr_after]

        new_top_players = set(top_players_after) - set(top_players_before)
        if new_top_players:
            for new_top_player_id in new_top_players:
                user_data = users.find_one({"discord_id": str(new_top_player_id)})
                if user_data:
                    riot_name = user_data.get("name", "Unknown")
                    riot_tag = user_data.get("tag", "Unknown")
                    await ctx.send(f"{riot_name}#{riot_tag} is now supersonic radiant!")

        def _add_months(dt, months):
            from calendar import monthrange
            year = dt.year + (dt.month - 1 + months) // 12
            month = (dt.month - 1 + months) % 12 + 1
            day = min(dt.day, monthrange(year, month)[1])
            return dt.replace(year=year, month=month, day=day)

        # Record every match played in a new collection
        all_matches.insert_one(match)
        
        now_utc = datetime.now(timezone.utc)
        current = seasons.find_one({"_id": "current"}) or {}

        started_at = _aware_utc(current.get("started_at"))  # <-- normalize
        reset_months = int(current.get("reset_period_months", 2))

        if not started_at:
            started_at = now_utc
            seasons.update_one(
                {"_id": "current"},
                {"$set": {"started_at": started_at, "is_closed": False}},
                upsert=True,
            )

        season_end_utc = _aware_utc(_add_months(started_at, reset_months))  # <-- normalize

        if not current.get("is_closed", False) and now_utc >= season_end_utc:
            await self.end_season(ctx, started_at_utc=started_at, ended_at_utc=now_utc)

        await asyncio.sleep(5)
        self.bot.match_not_reported = False
        self.bot.match_ongoing = False
        await self.cleanup_match_resources()

    async def end_season(self, ctx, started_at_utc=None, ended_at_utc=None):
        current = seasons.find_one({"_id": "current"}) or {}
        if current.get("is_closed"):
            return

        tz_central = ZoneInfo("America/Chicago")
        now_utc = datetime.now(timezone.utc)

        started_at_utc = _aware_utc(started_at_utc or current.get("started_at") or now_utc)
        ended_at_utc   = _aware_utc(ended_at_utc or now_utc)

        # Determine winner
        top_doc = mmr_collection.find_one(sort=[("mmr", -1)])
        if not top_doc:
            winner_player_id = None
            winner_name = "No players"
            winner_mmr = 0
        else:
            winner_player_id = str(top_doc.get("player_id"))
            winner_mmr = top_doc.get("mmr", 0)

            u = users.find_one({"discord_id": winner_player_id})
            if u:
                winner_name = f"{u.get('name', 'Unknown')}#{u.get('tag', 'Unknown')}"
            else:
                winner_name = top_doc.get("name", "Unknown")

        started_cst = started_at_utc.astimezone(tz_central) if started_at_utc else None
        ended_cst = ended_at_utc.astimezone(tz_central)
        started_str = started_cst.strftime("%Y-%m-%d %I:%M %p %Z") if started_cst else "unknown"
        ended_str = ended_cst.strftime("%Y-%m-%d %I:%M %p %Z")

        await ctx.send(
            "🏁 **Season complete!**\n"
            f"**Winner:** {winner_name}\n"
            f"**Final MMR:** {winner_mmr}\n"
            f"**Season window (Central):** {started_str} → {ended_str}"
        )

        seasons.update_one(
            {"_id": "current"},
            {"$set": {
                "is_closed": True,
                "ended_at": ended_at_utc,
                "winner_player_id": winner_player_id,
                "winner_name": winner_name,
                "winner_mmr": winner_mmr,
            }},
            upsert=True,
        )

        # next season
        next_season_number = int(current.get("season_number", 1)) + 1
        reset_period_months = int(current.get("reset_period_months", 2))

        seasons.update_one(
            {"_id": "current"},
            {"$set": {
                "season_number": next_season_number,
                "started_at": ended_at_utc,         
                "is_closed": False,
                "reset_period_months": reset_period_months,
                "matches_played": 0,                
                "last_season": {
                    "season_number": current.get("season_number", 1),
                    "started_at": started_at_utc,
                    "ended_at": ended_at_utc,
                    "winner_player_id": winner_player_id,
                    "winner_name": winner_name,
                    "winner_mmr": winner_mmr,
                    "matches_played": current.get("matches_played", 0),
                },
            }},
        )

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
            player_data = users.find_one({"name": str(riot_name).lower(), "tag": str(riot_tag).lower()})
            if player_data:
                player_id = str(player_data.get("discord_id"))
            else:
                await ctx.send(
                    "Could not find this player. Please check the name and tag and ensure they have played at least one match."
                )
                return
        else:
            player_id = str(ctx.author.id)

        if player_id in self.bot.player_mmr:
            stats_data = self.bot.player_mmr[player_id]
            mmr_value = stats_data.get("mmr", 1000)
            wins = stats_data.get("wins", 0)
            losses = stats_data.get("losses", 0)
            matches_played = stats_data.get("matches_played", wins + losses)
            total_rounds_played = stats_data.get("total_rounds_played", 0)
            avg_cs = stats_data.get("average_combat_score", 0)
            kd_ratio = stats_data.get("kill_death_ratio", 0)
            win_percent = (wins / matches_played) * 100 if matches_played > 0 else 0

            # Get riot name and tag
            user_data = users.find_one({"discord_id": str(player_id)})
            if user_data:
                riot_name = user_data.get("name", "Unknown")
                riot_tag = user_data.get("tag", "Unknown")
                player_name = f"{riot_name}#{riot_tag}"
            else:
                player_name = ctx.author.name

            total_players = len(self.bot.player_mmr)
            sorted_mmr = sorted(
                [(pid, stats) for pid, stats in self.bot.player_mmr.items() if "mmr" in stats],
                key=lambda x: x[1]["mmr"],
                reverse=True
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
        cursor = mmr_collection.find()
        sorted_data = list(cursor)
        sorted_data.sort(key=lambda x: x.get("mmr", 0), reverse=True)

        self.leaderboard_view = LeaderboardView(
            ctx,
            self.bot,
            sorted_data,            
            players_per_page=10,
            timeout=None,
            mode="normal"
        )

        start_index = 0
        end_index = min(self.leaderboard_view.players_per_page, len(self.leaderboard_view.sorted_data))
        page_data = self.leaderboard_view.sorted_data[start_index:end_index]

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

            leaderboard_data.append([
                idx,
                name,
                stats.get("mmr", 1000),
                stats.get("wins", 0),
                stats.get("losses", 0),
                f"{stats.get('average_combat_score', 0):.2f}",
                f"{stats.get('kill_death_ratio', 0):.2f}",
            ])

        table_output = t2a(
            header=["Rank", "User", "MMR", "Wins", "Losses", "Avg ACS", "K/D"],
            body=leaderboard_data,
            first_col_heading=True,
            style=PresetStyle.thick_compact
        )

        content = f"## MMR Leaderboard (Page 1/{self.leaderboard_view.total_pages}) ##\n```\n{table_output}\n```"
        self.leaderboard_message = await ctx.send(content=content, view=self.leaderboard_view)

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
            ),  
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
            ),  
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

    @commands.command()
    @commands.has_role("Owner")  # Restrict this command to admins
    async def initialize_rounds(self, ctx):
        result = mmr_collection.update_many(
            {}, {"$set": {"total_rounds_played": 0}}  # Update
        )
        await ctx.send(
            f"Initialized total_rounds_played for {result.modified_count} players."
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

        await ctx.send("The queue is now full, proceeding to the voting stage.")

        mode_vote = ModeVoteView(ctx, self.bot)
        await mode_vote.send_view()

    # Link Riot Account
    @commands.command()
    async def linkriot(self, ctx, *, riot_input):
        # Validate "Name#Tag"
        try:
            riot_name, riot_tag = riot_input.rsplit("#", 1)
        except ValueError:
            await ctx.send("Please provide your Riot ID in the format: `Name#Tag`")
            return

        api_key = os.getenv("api_key")
        if not api_key or not api_key.strip():
            await ctx.send("API key is not configured")
            return

        from urllib.parse import quote
        q_name = quote(riot_name, safe="")
        q_tag  = quote(riot_tag,  safe="")

        url = f"https://api.henrikdev.xyz/valorant/v2/account/{q_name}/{q_tag}"
        try:
            resp = requests.get(url, headers={"Authorization": api_key}, timeout=30)
        except requests.RequestException as e:
            await ctx.send(f"Network error reaching HenrikDev API: {e}")
            return

        # fully document API outcomes
        if resp.status_code == 401:
            await ctx.send("HenrikDev API rejected the request (401). Check that your API key is valid.")
            return
        if resp.status_code == 429:
            await ctx.send("Rate limit hit (429). Try again in a bit.")
            return
        if resp.status_code == 503:
            await ctx.send("Riot/HenrikDev upstream is temporarily unavailable (503). Try again later.")
            return
        if resp.status_code == 404:
            await ctx.send("Could not find that Riot account. Double-check the name and tag.")
            return
        if resp.status_code != 200:
            await ctx.send(f"Unexpected error from API ({resp.status_code}).")
            return

        data = resp.json()
        if "data" not in data:
            await ctx.send("Could not find your Riot account. Please check the name and tag.")
            return

        discord_id = str(ctx.author.id)
        users.update_one(
            {"discord_id": discord_id},
            {"$set": {
                "discord_id": discord_id,
                "name": riot_name.lower().strip(),
                "tag":  riot_tag.lower().strip(),
            }},
            upsert=True
        )

        full_name = f"{riot_name}#{riot_tag}"
        mmr_collection.update_one({"player_id": discord_id}, {"$set": {"name": full_name}}, upsert=False)
        tdm_mmr_collection.update_one({"player_id": discord_id}, {"$set": {"name": full_name}}, upsert=False)

        if self.bot.signup_active and any(p["id"] == discord_id for p in self.bot.queue):
            if self.bot.current_signup_message:
                riot_names = []
                for player in self.bot.queue:
                    player_data = users.find_one({"discord_id": player["id"]})
                    riot_names.append(
                        f"{player_data.get('name')}#{player_data.get('tag')}" if player_data else "Unknown"
                    )
                try:
                    await self.bot.current_signup_message.edit(
                        content="Click a button to manage your queue status!\n" +
                                f"Current queue ({len(self.bot.queue)}/10): {', '.join(riot_names)}"
                    )
                except discord.NotFound:
                    pass
            await ctx.send(f"Successfully linked {full_name} to your Discord account and updated your active queue entry.")
        else:
            await ctx.send(f"Successfully linked {full_name} to your Discord account.")

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

    # Stop the signup process
    @commands.command()
    @commands.has_role("Owner")
    async def cancel(self, ctx):
        if not self.bot.signup_active:
            await ctx.send("No signup is active to cancel")
            return
            
        if self.bot.signup_view:
            self.bot.signup_view.cleanup()
            self.bot.signup_view = None
            
        self.bot.queue = []
        self.bot.current_signup_message = None
        self.bot.signup_active = False
        
        await ctx.send("Canceled Signup")
        
        try:
            await self.bot.match_channel.delete()
            await self.bot.match_role.delete()
        except discord.NotFound:
            pass

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
            description="Duck's 10 Mans & TDM Commands:",
            color=discord.Color.green()
        )

        # General Commands
        help_embed.add_field(
            name="10 Mans Commands",
            value=(
                "**!signup** - Start a 10 mans signup session\n"
                "**!status** - View current queue status\n"
                "**!report** - Report match results and update MMR\n"
                "**!stats** - Check your MMR and match stats\n"
                "**!linkriot** - Link Riot account using `Name#Tag`\n"
            ),
            inline=False
        )

        # TDM Commands
        help_embed.add_field(
            name="TDM Commands",
            value=(
                "**!tdm** - Start a 3v3 TDM signup session\n"
                "**!tdmreport** - Report TDM match results\n"
                "**!tdmstats** - View TDM-specific stats\n"
            ),
            inline=False
        )

        # Leaderboard Commands  
        help_embed.add_field(
            name="Leaderboard Commands",
            value=(
                "**!leaderboard** - View MMR leaderboard\n"
                "**!leaderboard_KD** - View K/D leaderboard\n"
                "**!leaderboard_wins** - View wins leaderboard\n"
                "**!leaderboard_ACS** - View ACS leaderboard\n"
            ),
            inline=False
        )

        # Admin Commands
        help_embed.add_field(
            name="Admin Commands",
            value=(
                "**!setcaptain1** - Set Captain 1 using `Name#Tag`\n"
                "**!setcaptain2** - Set Captain 2 using `Name#Tag`\n"
                "**!cancel** - Cancel current 10 mans signup\n"
                "**!canceltdm** - Cancel current TDM signup\n"
                "**!toggledev** - Toggle Developer Mode\n"
            ),
            inline=False
        )

        # Footer
        help_embed.set_footer(text="Use commands with the ! prefix")

        await ctx.send(embed=help_embed)
