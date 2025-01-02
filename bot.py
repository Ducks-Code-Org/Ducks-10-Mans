"""Hold various general functions of the bot."""

from discord.ext import commands

from commands import BotCommands
from views.signup_view import SignupView
from database import mmr_collection, users
from tdm_commands import TDMCommands

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

        self.load_mmr_data()


    def load_mmr_data(self):
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
                {'player_id': player_id},
                {'$set': {
                    'mmr': stats['mmr'],
                    'wins': stats['wins'],
                    'losses': stats['losses'],
                    'name': name,
                    'total_combat_score': stats.get('total_combat_score', 0),
                    'total_kills': stats.get('total_kills', 0),
                    'total_deaths': stats.get('total_deaths', 0),
                    'matches_played': stats.get('matches_played', 0),
                    'total_rounds_played': stats.get('total_rounds_played', 0),
                    'average_combat_score': stats.get('average_combat_score', 0),
                    'kill_death_ratio': stats.get('kill_death_ratio', 0)
                }},
                upsert=True
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
        K_FACTOR = 32        # How quickly MMR adjusts
        
        winning_team_mmr = sum(
            self.player_mmr[player["id"]].get("tdm_mmr", 1000) 
            for player in winning_team
        ) / len(winning_team)
        
        losing_team_mmr = sum(
            self.player_mmr[player["id"]].get("tdm_mmr", 1000) 
            for player in losing_team
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
            
            final_mmr_change = min(MAX_MMR_CHANGE, max(BASE_MMR_CHANGE, modified_mmr_change))
            
            # Update player's MMR and record
            current_mmr = self.player_mmr[player_id].get("tdm_mmr", 1000)
            self.player_mmr[player_id]["tdm_mmr"] = round(current_mmr + final_mmr_change)
            self.player_mmr[player_id]["tdm_wins"] = self.player_mmr[player_id].get("tdm_wins", 0) + 1
            self.player_mmr[player_id]["latest_tdm_mmr_change"] = final_mmr_change

        # Process losing team
        for player in losing_team:
            player_id = player["id"]
            self.ensure_tdm_player_mmr(player_id)
            performance_mod = self._calculate_tdm_performance_modifier(player_id)
  
            uncertainty_mod = self._calculate_tdm_uncertainty_modifier(player_id)

            raw_mmr_change = K_FACTOR * (0 - expected_loss)
            modified_mmr_change = raw_mmr_change * performance_mod * uncertainty_mod

            final_mmr_change = max(-MAX_MMR_CHANGE, min(-BASE_MMR_CHANGE, modified_mmr_change))
            
            # Update player's MMR and record
            current_mmr = self.player_mmr[player_id].get("tdm_mmr", 1000)
            self.player_mmr[player_id]["tdm_mmr"] = max(0, round(current_mmr + final_mmr_change))
            self.player_mmr[player_id]["tdm_losses"] = self.player_mmr[player_id].get("tdm_losses", 0) + 1
            self.player_mmr[player_id]["latest_tdm_mmr_change"] = final_mmr_change

    def save_tdm_mmr_data(self):
        """Save TDM MMR data to the database"""
        for player_id, stats in self.player_mmr.items():
            # Get the Riot name and tag
            user_data = users.find_one({"discord_id": str(player_id)})
            if user_data:
                riot_name = user_data.get("name", "Unknown")
                riot_tag = user_data.get("tag", "Unknown")
                name = f"{riot_name}#{riot_tag}"
            else:
                name = "Unknown"

            # Only update if TDM stats exist
            if "tdm_mmr" in stats:
                tdm_mmr_collection.update_one(
                    {'player_id': player_id},
                    {'$set': {
                        'tdm_mmr': stats['tdm_mmr'],
                        'tdm_wins': stats['tdm_wins'],
                        'tdm_losses': stats['tdm_losses'],
                        'name': name,
                        'tdm_total_kills': stats.get('tdm_total_kills', 0),
                        'tdm_total_deaths': stats.get('tdm_total_deaths', 0),
                        'tdm_matches_played': stats.get('tdm_matches_played', stats['tdm_wins'] + stats['tdm_losses']),
                        'tdm_avg_kills': stats.get('tdm_avg_kills', 0),
                        'tdm_kd_ratio': stats.get('tdm_kd_ratio', 0)
                    }},
                    upsert=True
                )

    def load_tdm_mmr_data(self):
        for doc in tdm_mmr_collection.find():
            player_id = doc["player_id"]
            # Initialize tdm stats in player_mmr
            if player_id not in self.player_mmr:
                self.player_mmr[player_id] = {}
                
            self.player_mmr[player_id].update({
                "tdm_mmr": doc.get("tdm_mmr", 1000),
                "tdm_wins": doc.get("tdm_wins", 0),
                "tdm_losses": doc.get("tdm_losses", 0),
                "tdm_total_kills": doc.get("tdm_total_kills", 0),
                "tdm_total_deaths": doc.get("tdm_total_deaths", 0),
                "tdm_matches_played": doc.get("tdm_matches_played", 0),
                "tdm_avg_kills": doc.get("tdm_avg_kills", 0),
                "tdm_kd_ratio": doc.get("tdm_kd_ratio", 0)
            })

    def ensure_tdm_player_mmr(self, player_id):
        if player_id not in self.player_mmr:
            self.player_mmr[player_id] = {}
            
        player_data = self.player_mmr[player_id]
        
        # Initialize TDM stats if they don't exist
        if "tdm_mmr" not in player_data:
            player_data.update({
                "tdm_mmr": 1000,
                "tdm_wins": 0,
                "tdm_losses": 0,
                "tdm_total_kills": 0,
                "tdm_total_deaths": 0,
                "tdm_matches_played": 0,
                "tdm_avg_kills": 0.0,
                "tdm_kd_ratio": 0.0,
                "tdm_streak": 0,
                "tdm_performance_history": [],  # List of recent K/D performances
            })

    def _calculate_tdm_performance_modifier(self, player_id):
        player_data = self.player_mmr[player_id]
        history = player_data.get("tdm_performance_history", [])
        
        if not history:
            return 1.0
            
        # Calculate average K/D from recent matches
        avg_recent_kd = sum(history) / len(history)
        
        # Scale the modifier between 0.8 and 1.2 based on performance
        modifier = 1.0 + (avg_recent_kd - 1.0) * 0.2
        return max(0.8, min(1.2, modifier))

    def _calculate_tdm_uncertainty_modifier(self, player_id):
        player_data = self.player_mmr[player_id]
        matches_played = player_data.get("tdm_matches_played", 0)
        
        # Higher multiplier for newer players, gradually decreasing to 1.0
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
            # Initialize
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
            # Update player names
            user_data = users.find_one({"discord_id": str(player_id)})
            if user_data:
                player_names[player_id] = user_data.get("name", "Unknown")
            else:
                player_names[player_id] = "Unknown"

    async def setup_hook(self):
        # This is the recommended place for loading cogs
        await self.add_cog(BotCommands(self))
        await self.add_cog(TDMCommands(self))
        # Add any other setup logic here
        print("Bot is ready and cogs are loaded.")

    def some_custom_method(self):
        print("This is a custom method for the bot.")
