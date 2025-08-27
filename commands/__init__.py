from datetime import timezone

from discord.ext import commands

from database import all_matches


def convert_to_utc(datetime):
    if not datetime:
        return None
    if datetime.tzinfo is None:
        return datetime.replace(tzinfo=timezone.utc)
    return datetime.astimezone(timezone.utc)


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

        # For TDM Commands
        self.tdm_signup_active = False
        self.tdm_match_ongoing = False
        self.tdm_queue = []
        self.tdm_team1 = []
        self.tdm_team2 = []
        self.tdm_match_channel = None
        self.tdm_match_role = None
        self.tdm_current_message = None

        print(
            "[DEBUG] Checking the last match document in 'matches' DB for total rounds via 'rounds' array:"
        )
        last_match_doc = all_matches.find_one(sort=[("_id", -1)])
        if last_match_doc:
            rounds_array = last_match_doc.get("rounds", [])
            print(
                f"  [DEBUG DB] The last match in 'matches' had {len(rounds_array)} rounds (via last_match_doc['rounds'])."
            )
        else:
            print("  [DEBUG DB] No matches found in the 'matches' collection.")
