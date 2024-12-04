from discord.ext import commands
from commands import BotCommands
from views.signup_view import SignupView
from database import mmr_collection, users

class CustomBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
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

    def load_mmr_data(self):
        for doc in mmr_collection.find():
            player_id = int(doc['player_id'])
            self.player_mmr[player_id] = {
                'mmr': doc.get('mmr', 1000),
                'wins': doc.get('wins', 0),
                'losses': doc.get('losses', 0),
                'total_combat_score': doc.get('total_combat_score', 0),
                'total_kills': doc.get('total_kills', 0),
                'total_deaths': doc.get('total_deaths', 0),
                'matches_played': doc.get('matches_played', 0),
                'total_rounds_played': doc.get('total_rounds_played', 0),
                'average_combat_score': doc.get('average_combat_score', 0),
                'kill_death_ratio': doc.get('kill_death_ratio', 0)
            }

    def save_mmr_data(self):
        for player_id, stats in self.player_mmr.items():
            # Get the Riot name and tag from the users collection
            user_data = users.find_one({"discord_id": str(player_id)})
            if user_data:
                riot_name = user_data.get("name", "Unknown")
                riot_tag = user_data.get("tag", "Unknown")

    # adjust MMR and track wins/losses
    def adjust_mmr(self, winning_team, losing_team):
        MMR_CONSTANT = 32

        # Calculate average MMR for winning and losing teams
        winning_team_mmr = sum(self.player_mmr[player["id"]]["mmr"] for player in winning_team) / len(winning_team)
        losing_team_mmr = sum(self.player_mmr[player["id"]]["mmr"] for player in losing_team) / len(losing_team)

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

    def ensure_player_mmr(self, player_id, player_names):
        if player_id not in self.player_mmr:
            # Initialize
            self.player_mmr[player_id] = {
                'mmr': 1000,
                'wins': 0,
                'losses': 0,
                'total_combat_score': 0,
                'total_kills': 0,
                'total_deaths': 0,
                'matches_played': 0,
                'total_rounds_played': 0,
                'average_combat_score': 0,
                'kill_death_ratio': 0
            }
            # Update player names
            user_data = users.find_one({"discord_id": str(player_id)})
            if user_data:
                player_names[player_id] = user_data.get("name", "Unknown")
            else:
                player_names[player_id] = "Unknown"
    async def setup_hook(self):
        # This is the recommended place for loading cogs
        await self.add_cog(BotCommands(self))
        # Add any other setup logic here
        print("Bot is ready and cogs are loaded.")

    def some_custom_method(self):
        print("This is a custom method for the bot.")
