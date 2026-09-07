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
    if not hasattr(bot, "report_lock"):
        bot.report_lock = asyncio.Lock()
    await bot.add_cog(ReportCommand(bot))


async def cleanup_match_resources(bot):
    await bot.wait_until_ready()
    try:
        if hasattr(bot, "match_channel") and bot.match_channel:
            try:
                await bot.match_channel.delete()
            except discord.NotFound:
                print("[DEBUG] Match channel already deleted")
            except discord.Forbidden:
                print("[DEBUG] Missing permissions to delete match channel")
            finally:
                bot.match_channel = None

        if hasattr(bot, "match_role") and bot.match_role:

            try:
                for member in bot.match_role.members:
                    await member.remove_roles(bot.match_role)
            except discord.HTTPException:
                print("[DEBUG] Error removing roles from members")

            # delete the role
            try:
                await bot.match_role.delete()
            except discord.NotFound:
                print("[DEBUG] Match role already deleted")
            except discord.Forbidden:
                print("[DEBUG] Missing permissions to delete match role")
            finally:
                bot.match_role = None

        bot.match_not_reported = False
        bot.match_ongoing = False
        bot.queue.clear()

        if bot.current_signup_message:
            try:
                await bot.current_signup_message.delete()
            except discord.NotFound:
                pass
            finally:
                bot.current_signup_message = None

    except Exception as e:
        print(f"[DEBUG] Error during cleanup: {str(e)}")
        try:
            if hasattr(bot, "match_channel") and bot.match_channel:
                try:
                    await bot.match_channel.delete()
                except discord.NotFound:
                    print("[DEBUG] Match channel already deleted")
                except discord.Forbidden:
                    print("[DEBUG] Missing permissions to delete match channel")
                finally:
                    bot.match_channel = None

            if hasattr(bot, "match_role") and bot.match_role:

                try:
                    for member in bot.match_role.members:
                        await member.remove_roles(bot.match_role)
                except discord.HTTPException:
                    print("[DEBUG] Error removing roles from members")

                # delete the role
                try:
                    await bot.match_role.delete()
                except discord.NotFound:
                    print("[DEBUG] Match role already deleted")
                except discord.Forbidden:
                    print("[DEBUG] Missing permissions to delete match role")
                finally:
                    bot.match_role = None

            bot.match_not_reported = False
            bot.match_ongoing = False
            bot.queue.clear()

            if bot.current_signup_message:
                try:
                    await bot.current_signup_message.delete()
                except discord.NotFound:
                    pass
                finally:
                    bot.current_signup_message = None

        except Exception as e:
            print(f"[DEBUG] Error during cleanup: {str(e)}")


class ReportCommand(BotCommands):
    @commands.command()
    async def report(self, ctx):
        # ---------------------------------------------------------
        # Acquire report_lock to prevent concurrent double-reporting.
        # Only one !report command may run at a time.  We additionally
        # atomically clear match_not_reported so a second reporter who
        # acquires the lock after us sees the flag as already cleared.
        # ---------------------------------------------------------
        async with self.bot.report_lock:
            await ctx.send("Attempting to report latest match...")

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
                await ctx.send(
                    "No match is currently active, use `!signup` to start one"
                )
                return
            if not self.bot.selected_map:
                await ctx.send("No map was selected for this match.")
                return

            # ------------------------------------------------------------
            # ATOMIC CLAIM: only the FIRST simultaneous reporter to get here
            # may proceed; later ones will see match_not_reported == False.
            # ------------------------------------------------------------
            if not self.bot.match_not_reported:
                await ctx.send(
                    "This match has already been reported (a report is in progress "
                    "or completed)."
                )
                return
            self.bot.match_not_reported = False  # claim it right now

        # ------------------------------------------------------------
        # After this point we hold the sole right to write to the DB.
        # Everything outside the lock reads match state that won't change
        # until this handler finishes (cleanup at the end resets flags).
        # ------------------------------------------------------------

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

        # Resolve every queued player to their Discord id (the persistent
        # identity) plus their current Riot name/tag (only a lookup label).
        # The Discord id is authoritative; Riot IDs can change at any time.
        queue_members = []
        for player in self.bot.queue:
            user_data = users.find_one({"discord_id": str(player["id"])})
            if user_data:
                queue_members.append(
                    {
                        "discord_id": str(player["id"]),
                        "puuid": (user_data.get("puuid") or "").strip().lower(),
                        "name": user_data.get("name", "").lower(),
                        "tag": user_data.get("tag", "").lower(),
                    }
                )

        # Map each API player back to a queue member's Discord id. Prefer the
        # puuid when both sides have one; fall back to the Riot name/tag.
        def _resolve_api_player(api_player):
            api_puuid = (api_player.get("puuid") or "").strip().lower()
            api_name = (api_player.get("name") or "").lower()
            api_tag = (api_player.get("tag") or "").lower()

            for member in queue_members:
                if api_puuid and member["puuid"] and api_puuid == member["puuid"]:
                    return member["discord_id"]
            for member in queue_members:
                if api_name == member["name"] and api_tag == member["tag"]:
                    return member["discord_id"]
            return None

        queue_riot_ids = {(member["name"], member["tag"]) for member in queue_members}

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

        match_team_players = {"red": {}, "blue": {}}
        for player_info in match_players:
            raw_team_id = player_info.get("team_id", "").lower()  # "red" or "blue"
            p_discord_id = _resolve_api_player(player_info)
            if raw_team_id in match_team_players and p_discord_id:
                match_team_players[raw_team_id][p_discord_id] = player_info

        team1_ids_set = {str(player["id"]) for player in self.bot.team1}
        team2_ids_set = {str(player["id"]) for player in self.bot.team2}

        print(f"[DEBUG] team1 discord ids: {team1_ids_set}")
        print(f"[DEBUG] team2 discord ids: {team2_ids_set}")

        winning_match_team_ids = set(match_team_players.get(winning_team_id, {}))
        print(f"[DEBUG] Winning team Discord ID's: {winning_match_team_ids}")

        if winning_match_team_ids == team1_ids_set:
            winning_team = self.bot.team1
            losing_team = self.bot.team2
        elif winning_match_team_ids == team2_ids_set:
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

        # Helper: discord id -> team label (the discord id is the persistent
        # identity; Riot name/tag is only a display label)
        discord_to_teamlabel = {}
        for p in self.bot.team1:
            discord_to_teamlabel[str(p["id"])] = "team1"
        for p in self.bot.team2:
            discord_to_teamlabel[str(p["id"])] = "team2"

        team1_ids = [str(p["id"]) for p in self.bot.team1]
        team2_ids = [str(p["id"]) for p in self.bot.team2]

        def _mmr_of(pid):
            d = pre_update_mmr.get(pid)
            if isinstance(d, dict):
                return int(d.get("mmr", 1000))
            return 1000

        self.team1_mmr = sum(_mmr_of(pid) for pid in team1_ids)
        self.team2_mmr = sum(_mmr_of(pid) for pid in team2_ids)

        # discord id -> API team color ("red"/"blue")
        discord_to_api_color = {}
        for p in match_players:
            p_discord_id = _resolve_api_player(p)
            if p_discord_id:
                discord_to_api_color[p_discord_id] = (p.get("team_id") or "").lower()

        # Helper to get the API color
        def _team_api_color(team_players):
            for pl in team_players:
                color = discord_to_api_color.get(str(pl["id"]))
                if color:
                    return color
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
            "team1" if winning_match_team_ids == team1_ids_set else "team2"
        )

        # Update stats for each player
        for player_stats in match_players:
            p_discord_id = _resolve_api_player(player_stats)
            if not p_discord_id:
                print(
                    f"[DEBUG] API player {player_stats.get('name')}#"
                    f"{player_stats.get('tag')} is not in the queue; skipping"
                )
                continue
            team_label = discord_to_teamlabel.get(p_discord_id)
            if not team_label:
                continue

            update_stats(
                player_stats,
                total_rounds,
                self.bot.player_mmr,
                self.bot.player_names,
                discord_id=p_discord_id,
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

        # Increment Current Season Match Count
        seasons.update_one(
            {"_id": "current"}, {"$inc": {"matches_played": 1}}, upsert=True
        )

        await asyncio.sleep(5)
        self.bot.match_not_reported = False
        self.bot.match_ongoing = False
        await cleanup_match_resources(self.bot)


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
