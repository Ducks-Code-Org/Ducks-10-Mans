"""Hold various general functions of the bot."""

from datetime import datetime, timezone

import discord
from discord.ext import commands

from views.signup_view import SignupView
from database import mmr_collection, users, tdm_mmr_collection, seasons
from globals import TIME_ZONE_CST

try:
    from dateutil.relativedelta import relativedelta
except Exception:
    relativedelta = None


class CustomBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 10 mans attributes
        self.signup_view: SignupView = None
        self.match_not_reported = False
        self.player_mmr = {}
        self.player_names = {}
        self.match_ongoing = False
        self.selected_map = None
        self.team1 = []
        self.team2 = []
        self.signup_active = False
        self.current_signup_message = None
        self.queue = []
        self.captain1 = None
        self.captain2 = None
        self.chosen_mode = None

        self.match_channel = None
        self.match_role = None
        self.match_name = "10-Mans"

        # TDM attributes
        self.tdm_queue = []
        self.tdm_team1 = []
        self.tdm_team2 = []
        self.tdm_match_ongoing = False
        self.tdm_selected_map = None
        self.tdm_match_role = None
        self.tdm_match_channel = None
        self.tdm_current_message = None
        self.tdm_signup_active = False

        self.load_mmr_data()
        self.load_tdm_mmr_data()
        seasons.update_one(
            {"_id": "current"},
            {
                "$setOnInsert": {
                    "season_number": 1,
                    "started_at": datetime.now(timezone.utc),
                    "matches_played": 0,
                    "is_closed": False,
                    "reset_period_months": 2,  # 2-month seasons
                }
            },
            upsert=True,
        )

    def _two_months_after(self, start_utc: datetime) -> datetime:
        if relativedelta is not None:
            return start_utc + relativedelta(months=+2)

        # Fallback
        y, m = start_utc.year, start_utc.month
        m += 2
        while m > 12:
            y += 1
            m -= 12
        from calendar import monthrange

        d = min(start_utc.day, monthrange(y, m)[1])
        return datetime(
            y,
            m,
            d,
            start_utc.hour,
            start_utc.minute,
            start_utc.second,
            start_utc.microsecond,
            tzinfo=timezone.utc,
        )

    def create_new_season(self, *, reset_player_stats: bool = True) -> dict:
        now_utc = datetime.now(timezone.utc)
        starts = now_utc
        ends = self._two_months_after(starts)

        current = seasons.find_one({"_id": "current"}) or {}
        next_num = int(current.get("season_number", 0)) + 1

        seasons.update_one(
            {"_id": "current"},
            {
                "$set": {
                    "season_number": next_num,
                    "started_at": starts,
                    "is_closed": False,
                    "reset_period_months": 2,
                    "matches_played": 0,
                    "ends_at_expected": ends,
                },
                "$unset": {"ended_at": ""},
            },
            upsert=True,
        )

        if reset_player_stats:
            self._reset_all_players_for_new_season(next_num)

        # Return a small dict used by the newseason command for display
        return {
            "season_number": next_num,
            "started_at": starts,  # UTC
            "ends_at_expected": ends,  # UTC
            "started_at_cst": starts.astimezone(TIME_ZONE_CST),
            "ends_at_cst": ends.astimezone(TIME_ZONE_CST),
            "is_closed": False,
        }

    def _reset_all_players_for_new_season(self, season_number: int) -> None:
        """
        Hard reset of everyone’s per‑season stats and MMR in the correct collections.
        Also resets in-memory caches so commands reflect the reset immediately.
        """
        BASE_MMR = 1000

        # Reset core 10-mans stats in db
        mmr_collection.update_many(
            {},
            {
                "$set": {
                    "mmr": BASE_MMR,
                    "wins": 0,
                    "losses": 0,
                    "total_combat_score": 0,
                    "total_kills": 0,
                    "total_deaths": 0,
                    "matches_played": 0,
                    "total_rounds_played": 0,
                    "average_combat_score": 0,
                    "kill_death_ratio": 0,
                }
            },
        )

        # Reset TDM stats in db
        tdm_mmr_collection.update_many(
            {},
            {
                "$set": {
                    "tdm_mmr": BASE_MMR,
                    "tdm_wins": 0,
                    "tdm_losses": 0,
                    "tdm_total_kills": 0,
                    "tdm_total_deaths": 0,
                    "tdm_matches_played": 0,
                    "tdm_avg_kills": 0.0,
                    "tdm_kd_ratio": 0.0,
                }
            },
        )

        # 3) Reset in-memory cache
        for _pid, stats in list(self.player_mmr.items()):
            # Core 10-mans
            stats.update(
                {
                    "mmr": BASE_MMR,
                    "wins": 0,
                    "losses": 0,
                    "total_combat_score": 0,
                    "total_kills": 0,
                    "total_deaths": 0,
                    "matches_played": 0,
                    "total_rounds_played": 0,
                    "average_combat_score": 0,
                    "kill_death_ratio": 0,
                }
            )
            if "tdm_mmr" in stats:
                stats.update(
                    {
                        "tdm_mmr": BASE_MMR,
                        "tdm_wins": 0,
                        "tdm_losses": 0,
                        "tdm_total_kills": 0,
                        "tdm_total_deaths": 0,
                        "tdm_matches_played": 0,
                        "tdm_avg_kills": 0.0,
                        "tdm_kd_ratio": 0.0,
                        "tdm_streak": 0,
                        "tdm_performance_history": [],
                    }
                )

        self.load_mmr_data()
        self.load_tdm_mmr_data()

    def load_mmr_data(self):
        self.player_mmr.clear()
        self.player_names.clear()

        for doc in mmr_collection.find():
            player_id = doc["player_id"]
            self.player_mmr[player_id] = {
                "mmr": doc.get("mmr", 1000),
                "wins": doc.get("wins", 0),
                "losses": doc.get("losses", 0),
                "total_combat_score": doc.get("total_combat_score", 0),
                "total_kills": doc.get("total_kills", 0),
                "total_deaths": doc.get("total_deaths", 0),
                "matches_played": doc.get("matches_played", 0),
                "total_rounds_played": doc.get("total_rounds_played", 0),
                "average_combat_score": doc.get("average_combat_score", 0),
                "kill_death_ratio": doc.get("kill_death_ratio", 0),
            }

    def save_mmr_data(self):
        for player_id, stats in self.player_mmr.items():
            # Get the Riot name and tag from the users collection
            user_data = users.find_one({"discord_id": str(player_id)})
            if user_data:
                riot_name = user_data.get("name", "Unknown")
                riot_tag = user_data.get("tag", "Unknown")
                name = f"{riot_name}#{riot_tag}"
            else:
                name = "Unknown"
            mmr_collection.update_one(
                {"player_id": player_id},
                {
                    "$set": {
                        "mmr": stats["mmr"],
                        "wins": stats["wins"],
                        "losses": stats["losses"],
                        "name": name,
                        "total_combat_score": stats.get("total_combat_score", 0),
                        "total_kills": stats.get("total_kills", 0),
                        "total_deaths": stats.get("total_deaths", 0),
                        "matches_played": stats.get("matches_played", 0),
                        "total_rounds_played": stats.get("total_rounds_played", 0),
                        "average_combat_score": stats.get("average_combat_score", 0),
                        "kill_death_ratio": stats.get("kill_death_ratio", 0),
                    }
                },
                upsert=True,
            )

    # adjust MMR and track wins/losses
    def adjust_mmr(self, winning_team, losing_team):
        MMR_CONSTANT = 32

        # Calculate average MMR for winning and losing teams
        winning_team_mmr = sum(
            self.player_mmr[player["id"]]["mmr"] for player in winning_team
        ) / len(winning_team)
        losing_team_mmr = sum(
            self.player_mmr[player["id"]]["mmr"] for player in losing_team
        ) / len(losing_team)

        # Calculate expected results
        expected_win = 1 / (1 + 10 ** ((losing_team_mmr - winning_team_mmr) / 400))
        expected_loss = 1 / (1 + 10 ** ((winning_team_mmr - losing_team_mmr) / 400))

        # Adjust MMR for winning team
        for player in winning_team:
            player_id = player["id"]
            current_mmr = self.player_mmr[player_id]["mmr"]
            new_mmr = current_mmr + MMR_CONSTANT * (1 - expected_win)
            self.player_mmr[player_id]["mmr"] = round(new_mmr)
            self.player_mmr[player_id]["wins"] += 1

        # Adjust MMR for losing team
        for player in losing_team:
            player_id = player["id"]
            current_mmr = self.player_mmr[player_id]["mmr"]
            new_mmr = current_mmr + MMR_CONSTANT * (0 - expected_loss)
            self.player_mmr[player_id]["mmr"] = max(0, round(new_mmr))
            self.player_mmr[player_id]["losses"] += 1

    def adjust_tdm_mmr(self, winning_team, losing_team):

        BASE_MMR_CHANGE = 25
        MAX_MMR_CHANGE = 35
        K_FACTOR = 32

        winning_team_mmr = sum(
            self.player_mmr[player["id"]].get("tdm_mmr", 1000)
            for player in winning_team
        ) / len(winning_team)

        losing_team_mmr = sum(
            self.player_mmr[player["id"]].get("tdm_mmr", 1000) for player in losing_team
        ) / len(losing_team)

        expected_win = 1 / (1 + 10 ** ((losing_team_mmr - winning_team_mmr) / 400))
        expected_loss = 1 / (1 + 10 ** ((winning_team_mmr - losing_team_mmr) / 400))
        for player in winning_team:
            player_id = player["id"]
            self.ensure_tdm_player_mmr(player_id)

            performance_mod = self._calculate_tdm_performance_modifier(player_id)

            uncertainty_mod = self._calculate_tdm_uncertainty_modifier(player_id)

            raw_mmr_change = K_FACTOR * (1 - expected_win)
            modified_mmr_change = raw_mmr_change * performance_mod * uncertainty_mod

            final_mmr_change = min(
                MAX_MMR_CHANGE, max(BASE_MMR_CHANGE, modified_mmr_change)
            )

            # Update player's MMR and record
            current_mmr = self.player_mmr[player_id].get("tdm_mmr", 1000)
            self.player_mmr[player_id]["tdm_mmr"] = round(
                current_mmr + final_mmr_change
            )
            self.player_mmr[player_id]["tdm_wins"] = (
                self.player_mmr[player_id].get("tdm_wins", 0) + 1
            )
            self.player_mmr[player_id]["latest_tdm_mmr_change"] = final_mmr_change

        # Process losing team
        for player in losing_team:
            player_id = player["id"]
            self.ensure_tdm_player_mmr(player_id)
            performance_mod = self._calculate_tdm_performance_modifier(player_id)

            uncertainty_mod = self._calculate_tdm_uncertainty_modifier(player_id)

            raw_mmr_change = K_FACTOR * (0 - expected_loss)
            modified_mmr_change = raw_mmr_change * performance_mod * uncertainty_mod

            final_mmr_change = max(
                -MAX_MMR_CHANGE, min(-BASE_MMR_CHANGE, modified_mmr_change)
            )

            # Update player's MMR and record
            current_mmr = self.player_mmr[player_id].get("tdm_mmr", 1000)
            self.player_mmr[player_id]["tdm_mmr"] = max(
                0, round(current_mmr + final_mmr_change)
            )
            self.player_mmr[player_id]["tdm_losses"] = (
                self.player_mmr[player_id].get("tdm_losses", 0) + 1
            )
            self.player_mmr[player_id]["latest_tdm_mmr_change"] = final_mmr_change

    def save_tdm_mmr_data(self):
        """Save TDM MMR data to the database"""
        for player_id, stats in self.player_mmr.items():
            user_data = users.find_one({"discord_id": str(player_id)})
            if user_data:
                riot_name = user_data.get("name", "Unknown")
                riot_tag = user_data.get("tag", "Unknown")
                name = f"{riot_name}#{riot_tag}"
            else:
                name = "Unknown"

            if "tdm_mmr" in stats:
                tdm_mmr_collection.update_one(
                    {"player_id": player_id},
                    {
                        "$set": {
                            "tdm_mmr": stats["tdm_mmr"],
                            "tdm_wins": stats["tdm_wins"],
                            "tdm_losses": stats["tdm_losses"],
                            "name": name,
                            "tdm_total_kills": stats.get("tdm_total_kills", 0),
                            "tdm_total_deaths": stats.get("tdm_total_deaths", 0),
                            "tdm_matches_played": stats.get(
                                "tdm_matches_played",
                                stats["tdm_wins"] + stats["tdm_losses"],
                            ),
                            "tdm_avg_kills": stats.get("tdm_avg_kills", 0),
                            "tdm_kd_ratio": stats.get("tdm_kd_ratio", 0),
                        }
                    },
                    upsert=True,
                )

    def load_tdm_mmr_data(self):
        for doc in tdm_mmr_collection.find():
            player_id = doc["player_id"]
            if player_id not in self.player_mmr:
                self.player_mmr[player_id] = {}

            self.player_mmr[player_id].update(
                {
                    "tdm_mmr": doc.get("tdm_mmr", 1000),
                    "tdm_wins": doc.get("tdm_wins", 0),
                    "tdm_losses": doc.get("tdm_losses", 0),
                    "tdm_total_kills": doc.get("tdm_total_kills", 0),
                    "tdm_total_deaths": doc.get("tdm_total_deaths", 0),
                    "tdm_matches_played": doc.get("tdm_matches_played", 0),
                    "tdm_avg_kills": doc.get("tdm_avg_kills", 0),
                    "tdm_kd_ratio": doc.get("tdm_kd_ratio", 0),
                }
            )

    def ensure_tdm_player_mmr(self, player_id):
        if player_id not in self.player_mmr:
            self.player_mmr[player_id] = {}

        player_data = self.player_mmr[player_id]

        tdm_data = tdm_mmr_collection.find_one({"player_id": player_id})

        if tdm_data:
            player_data.update(
                {
                    "tdm_mmr": tdm_data.get("tdm_mmr", 1000),
                    "tdm_wins": tdm_data.get("tdm_wins", 0),
                    "tdm_losses": tdm_data.get("tdm_losses", 0),
                    "tdm_total_kills": tdm_data.get("tdm_total_kills", 0),
                    "tdm_total_deaths": tdm_data.get("tdm_total_deaths", 0),
                    "tdm_matches_played": tdm_data.get("tdm_matches_played", 0),
                    "tdm_avg_kills": tdm_data.get("tdm_avg_kills", 0.0),
                    "tdm_kd_ratio": tdm_data.get("tdm_kd_ratio", 0.0),
                    "tdm_streak": tdm_data.get("tdm_streak", 0),
                    "tdm_performance_history": tdm_data.get(
                        "tdm_performance_history", []
                    ),
                }
            )
        else:
            if "tdm_mmr" not in player_data:
                player_data.update(
                    {
                        "tdm_mmr": 1000,
                        "tdm_wins": 0,
                        "tdm_losses": 0,
                        "tdm_total_kills": 0,
                        "tdm_total_deaths": 0,
                        "tdm_matches_played": 0,
                        "tdm_avg_kills": 0.0,
                        "tdm_kd_ratio": 0.0,
                        "tdm_streak": 0,
                        "tdm_performance_history": [],
                    }
                )

    def _calculate_tdm_performance_modifier(self, player_id):
        player_data = self.player_mmr[player_id]
        history = player_data.get("tdm_performance_history", [])

        if not history:
            return 1.0

        avg_recent_kd = sum(history) / len(history)

        modifier = 1.0 + (avg_recent_kd - 1.0) * 0.2
        return max(0.8, min(1.2, modifier))

    def _calculate_tdm_uncertainty_modifier(self, player_id):
        player_data = self.player_mmr[player_id]
        matches_played = player_data.get("tdm_matches_played", 0)

        if matches_played < 10:
            return 1.5
        elif matches_played < 20:
            return 1.25
        elif matches_played < 30:
            return 1.1
        else:
            return 1.0

    def ensure_player_mmr(self, player_id, player_names):
        if player_id not in self.player_mmr:
            self.player_mmr[player_id] = {
                "mmr": 1000,
                "wins": 0,
                "losses": 0,
                "total_combat_score": 0,
                "total_kills": 0,
                "total_deaths": 0,
                "matches_played": 0,
                "total_rounds_played": 0,
                "average_combat_score": 0,
                "kill_death_ratio": 0,
            }
            user_data = users.find_one({"discord_id": str(player_id)})
            if user_data:
                player_names[player_id] = user_data.get("name", "Unknown")
            else:
                player_names[player_id] = "Unknown"

    async def setup_hook(self):
        await self.load_extension("commands.admin_commands")
        await self.load_extension("commands.help")
        await self.load_extension("commands.interest")
        await self.load_extension("commands.leaderboard_commands")
        await self.load_extension("commands.linkriot")
        await self.load_extension("commands.report")
        await self.load_extension("commands.signup")
        await self.load_extension("commands.stats")
        await self.load_extension("commands.tdm_commands")
        print("Bot is ready and cogs are loaded.")

    async def purge_old_match_roles(self):
        print("Checking for old match roles to delete...")
        for guild in self.guilds:
            # Find roles with 'match' or 'tdm' in the name (case-insensitive)
            old_roles = list(
                filter(
                    lambda r: "match" in r.name.lower() or "tdm" in r.name.lower(),
                    guild.roles,
                )
            )
            if old_roles:
                print(
                    f"Deleting roles in guild '{guild.name}':",
                    [role.name for role in old_roles],
                )
                for role in old_roles:
                    try:
                        await role.delete()
                    except discord.HTTPException:
                        pass

    async def purge_old_match_channels(self):
        print("Checking for old match channels to delete...")
        for guild in self.guilds:
            # Find channels with 'match' or 'tdm' in the name (case-insensitive)
            old_channels = list(
                filter(
                    lambda c: "match" in c.name.lower() or "tdm" in c.name.lower(),
                    guild.channels,
                )
            )
            if old_channels:
                print(
                    f"Deleting channels in guild '{guild.name}':",
                    [channel.name for channel in old_channels],
                )
                for channel in old_channels:
                    try:
                        await channel.delete()
                    except discord.HTTPException:
                        pass

    async def on_ready(self):
        print(f"Bot connected as {self.user}.")
        await self.purge_old_match_roles()
        await self.purge_old_match_channels()
