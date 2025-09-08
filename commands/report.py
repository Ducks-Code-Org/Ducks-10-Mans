"Report the most recent match played to update MMR and stats."

from datetime import datetime, timezone
import copy
import asyncio
from calendar import monthrange

import requests
import discord
from discord.ext import commands

from commands import BotCommands, convert_to_utc
from database import users, mmr_collection, seasons, all_matches
from globals import API_KEY, TIME_ZONE_CST, mock_match_data
from stats_helper import update_stats
from urllib.parse import quote


async def setup(bot):
    await bot.add_cog(ReportCommand(bot))


class ReportCommand(BotCommands):
    @commands.command()
    async def report(self, ctx):
        # ADD THIS IMMEDIATE RESPONSE - This is the fix for issue #119
        await ctx.send("🔄 Processing match report... Please wait while I fetch the match data.")
        
        # linkage check
        current_user = users.find_one({"discord_id": str(ctx.author.id)})
        if not current_user:
            await ctx.send(
                "You need to link your Riot account first using `!linkriot Name#Tag`"
            )
            return

        name = (current_user.get("name") or "").lower().strip()
        tag = (current_user.get("tag") or "").lower().strip()
        if not name or not tag:
            await ctx.send(
                "Your Riot account looks incomplete. Re-link with `!linkriot Name#Tag`."
            )
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

        region, platform = "na", "pc"
        q_name, q_tag = quote(name, safe=""), quote(tag, safe="")
        url = f"https://api.henrikdev.xyz/valorant/v4/matches/{region}/{platform}/{q_name}/{q_tag}"

        try:
            resp = requests.get(url, headers={"Authorization": API_KEY}, timeout=30)
        except requests.RequestException as e:
            await ctx.send(f"Network error reaching HenrikDev API: {e}")
            return

        if resp.status_code == 401:
            await ctx.send(
                "HenrikDev API rejected the request (401). Check that your API key is valid."
            )
            return
        if resp.status_code == 404:
            await ctx.send("No recent matches found for your Riot ID (404).")
            return
        if resp.status_code == 429:
            await ctx.send("Rate limit hit (429). Try again in a bit.")
            return
        if resp.status_code == 503:
            await ctx.send(
                "Riot/HenrikDev upstream is temporarily unavailable (503). Try again later."
            )
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
            await ctx.send(
                "Map doesn't match your most recent match. Unable to report it."
            )
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
                total_rounds = metadata.get("rounds_played") or metadata.get(
                    "total_rounds"
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
            mismatch_message = (
                "The most recent match does not match the 10-man's match.\n\n"
            )
            mismatch_message += (
                "The following players' Riot IDs don't match the game data:\n"
            )

            for name, tag in missing_players:
                mismatch_message += f"• {name}#{tag}\n"

            mismatch_message += "\nPossible reasons:\n"
            mismatch_message += (
                "1. Did you or someone make a change to their Riot name/tag?\n"
            )
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
            (pid, stats)
            for pid, stats in pre_update_mmr.items()
            if isinstance(stats, dict) and "mmr" in stats
        ]

        if valid_mmr_entries:
            sorted_mmr_before = sorted(
                valid_mmr_entries, key=lambda x: x[1]["mmr"], reverse=True
            )
            top_mmr_before = sorted_mmr_before[0][1]["mmr"]
            top_players_before = [
                str(pid)
                for pid, stats in sorted_mmr_before
                if stats["mmr"] == top_mmr_before
            ]
        else:
            top_mmr_before = 1000
            top_players_before = []

        # Helper
        riot_to_teamlabel = {}
        for p in self.bot.team1:
            u = users.find_one({"discord_id": str(p["id"])})
            if u:
                riot_to_teamlabel[
                    (u.get("name", "").lower(), u.get("tag", "").lower())
                ] = "team1"
        for p in self.bot.team2:
            u = users.find_one({"discord_id": str(p["id"])})
            if u:
                riot_to_teamlabel[
                    (u.get("name", "").lower(), u.get("tag", "").lower())
                ] = "team2"

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
            rw_raw = t.get("rounds_won", t.get("rounds", 0))
            rw = rounds_to_int(rw_raw)
            api_rounds[tid] = rw

        self.team1_rounds = int(api_rounds.get(team1_api_color, 0))
        self.team2_rounds = int(api_rounds.get(team2_api_color, 0))
        round_diff_val = abs(self.team1_rounds - self.team2_rounds)
        self.winning_team = (
            "team1" if winning_match_team_players == team1_riot_ids else "team2"
        )

        # Update stats for each player
        for player_stats in match_players:
            p_name = (player_stats.get("name") or "").lower()
            p_tag = (player_stats.get("tag") or "").lower()
            team_label = riot_to_teamlabel.get((p_name, p_tag))
            if not team_label:
                continue

            update_stats(
                player_stats,
                total_rounds,
                self.bot.player_mmr,
                self.bot.player_names,
                team_sum_mmr=(
                    self.team1_mmr if team_label == "team1" else self.team2_mmr
                ),
                opp_sum_mmr=self.team2_mmr if team_label == "team1" else self.team1_mmr,
                team_won=(self.winning_team == team_label),
                round_diff=round_diff_val,
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
                "name": riot_name.lower(),
                "total_combat_score": stats.get("total_combat_score", 0),
                "total_kills": stats.get("total_kills", 0),
                "total_deaths": stats.get("total_deaths", 0),
                "matches_played": stats.get("matches_played", 0),
                "total_rounds_played": stats.get("total_rounds_played", 0),
                "average_combat_score": stats.get("average_combat_score", 0),
                "kill_death_ratio": stats.get("kill_death_ratio", 0),
            }

            mmr_collection.update_one(
                {"player_id": discord_id}, {"$set": complete_stats}, upsert=True
            )

        print("[DEBUG] All stats saved to database")

        sorted_mmr_after = sorted(
            self.bot.player_mmr.items(), key=lambda x: x[1]["mmr"], reverse=True
        )
        top_mmr_after = sorted_mmr_after[0][1]["mmr"]
        top_players_after = [
            pid for pid, stats in sorted_mmr_after if stats["mmr"] == top_mmr_after
        ]

        new_top_players = set(top_players_after) - set(top_players_before)
        if new_top_players:
            for new_top_player_id in new_top_players:
                user_data = users.find_one({"discord_id": str(new_top_player_id)})
                if user_data:
                    riot_name = user_data.get("name", "Unknown").lower()
                    riot_tag = user_data.get("tag", "Unknown").lower()
                    # Try to send to 'announcements' channel if it exists
                    announcement_channel = None
                    if ctx.guild:
                        for channel in ctx.guild.text_channels:
                            if channel.name.lower() == "announcements":
                                announcement_channel = channel
                                break
                    message = f"{riot_name}#{riot_tag} is now supersonic radiant!"
                    if announcement_channel:
                        await announcement_channel.send(message)
                    else:
                        await ctx.send(message)

        def _add_months(dt, months):
            year = dt.year + (dt.month - 1 + months) // 12
            month = (dt.month - 1 + months) % 12 + 1
            day = min(dt.day, monthrange(year, month)[1])
            return dt.replace(year=year, month=month, day=day)

        # Record every match played in a new collection
        all_matches.insert_one(match)

        now_utc = datetime.now(timezone.utc)
        current = seasons.find_one({"_id": "current"}) or {}

        started_at = convert_to_utc(current.get("started_at"))  # <-- normalize
        reset_months = int(current.get("reset_period_months", 2))

        if not started_at:
            started_at = now_utc
            seasons.update_one(
                {"_id": "current"},
                {"$set": {"started_at": started_at, "is_closed": False}},
                upsert=True,
            )

        season_end_utc = convert_to_utc(
            _add_months(started_at, reset_months)
        )  # <-- normalize

        if not current.get("is_closed", False) and now_utc >= season_end_utc:
            await end_season(ctx, started_at_utc=started_at, ended_at_utc=now_utc)

        await asyncio.sleep(5)
        self.bot.match_not_reported = False
        self.bot.match_ongoing = False
        await self.cleanup_match_resources()

    async def cleanup_match_resources(self):
        await self.bot.wait_until_ready()
        try:
            if hasattr(self.bot, "match_channel") and self.bot.match_channel:
                try:
                    await self.bot.match_channel.delete()
                except discord.NotFound:
                    print("[DEBUG] Match channel already deleted")
                except discord.Forbidden:
                    print("[DEBUG] Missing permissions to delete match channel")
                finally:
                    self.bot.match_channel = None

            if hasattr(self.bot, "match_role") and self.bot.match_role:

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
            try:
                if hasattr(self.bot, "match_channel") and self.bot.match_channel:
                    try:
                        await self.bot.match_channel.delete()
                    except discord.NotFound:
                        print("[DEBUG] Match channel already deleted")
                    except discord.Forbidden:
                        print("[DEBUG] Missing permissions to delete match channel")
                    finally:
                        self.bot.match_channel = None

                if hasattr(self.bot, "match_role") and self.bot.match_role:

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


def rounds_to_int(value):
    if isinstance(value, dict):
        for key in ("won", "w", "value", "wins", "count"):
            v = value.get(key)
            if isinstance(v, (int, float, str)):
                try:
                    return int(v)
                except Exception:
                    pass
        numeric_vals = [v for v in value.values() if isinstance(v, (int, float))]
        if numeric_vals:
            return int(max(numeric_vals))
        return 0

    if isinstance(value, (list, tuple)):
        if value:
            try:
                return int(value[0])
            except Exception:
                return 0
        return 0

    try:
        return int(value)
    except Exception:
        return 0


async def end_season(ctx, started_at_utc=None, ended_at_utc=None):
    current = seasons.find_one({"_id": "current"}) or {}
    if current.get("is_closed"):
        return

    now_utc = datetime.now(timezone.utc)

    started_at_utc = convert_to_utc(
        started_at_utc or current.get("started_at") or now_utc
    )
    ended_at_utc = convert_to_utc(ended_at_utc or now_utc)

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

    started_cst = started_at_utc.astimezone(TIME_ZONE_CST) if started_at_utc else None
    ended_cst = ended_at_utc.astimezone(TIME_ZONE_CST)
    started_str = (
        started_cst.strftime("%Y-%m-%d %I:%M %p %Z") if started_cst else "unknown"
    )
    ended_str = ended_cst.strftime("%Y-%m-%d %I:%M %p %Z")

    await ctx.send(
        "🏁 **Season complete!**\n"
        f"**Winner:** {winner_name}\n"
        f"**Final MMR:** {winner_mmr}\n"
        f"**Season window (Central):** {started_str} → {ended_str}"
    )

    seasons.update_one(
        {"_id": "current"},
        {
            "$set": {
                "is_closed": True,
                "ended_at": ended_at_utc,
                "winner_player_id": winner_player_id,
                "winner_name": winner_name,
                "winner_mmr": winner_mmr,
            }
        },
        upsert=True,
    )

    # next season
    next_season_number = int(current.get("season_number", 1)) + 1
    reset_period_months = int(current.get("reset_period_months", 2))

    seasons.update_one(
        {"_id": "current"},
        {
            "$set": {
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
            }
        },
    )
