from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi


class StatChange:
    def __init__(self, player_name, stat_name, old, new):
        self.player_name = player_name
        self.stat_name = stat_name
        self.old = old
        self.new = new


# MongoDB Connection
uri = "mongodb+srv://x4skinniestduck:8QZOdjPrrgJkRGPX@rapid.12llf.mongodb.net/?retryWrites=true&w=majority&appName=Rapid"
client = MongoClient(uri, server_api=ServerApi("1"))

# Initialize MongoDB Collections
db = client["valorant"]
users = db["users"]
mmr_collection = db["mmr_data"]
all_matches = db["matches"]


def display_change(change: StatChange):
    print(f"({change.player_name}) {change.collection.name}-{change.stat_name}: {change.old}->{change.new}")


def display_all_changes(stat_changes: list[StatChange]):
    for change in stat_changes:
        display_change(change)


def get_matchlist_changes_that_will_be_made(matchlist):
    from DebugTools.helpers.stat_getters import (
        get_losses_from_matchlist,
        get_wins_from_matchlist,
        get_combat_score_from_matchlist,
        get_deaths_from_matchlist,
        get_kills_from_matchlist,
        get_total_rounds_played_from_matchlist,
        get_winning_team_id
    )

    changes: list[StatChange] = []
    winning_teams = []
    losing_teams = []
    for match in matchlist:
        winning_team_id = get_winning_team_id(match)

        winning_team = []
        losing_team = []

        for player in match["players"]:
            if player["team_id"] == winning_team_id:
                winning_team.append(player)
            else:
                losing_team.append(player)

        winning_teams.append(winning_team)
        losing_teams.append(losing_team)

    # Use stat getters to calculate other stats
    mmr = get_mmr_values_multiple_teams(winning_teams, losing_teams)
    losses: dict = get_losses_from_matchlist(matchlist)
    wins: dict = get_wins_from_matchlist(matchlist)
    total_combat_score: dict = get_combat_score_from_matchlist(matchlist)
    total_deaths: dict = get_deaths_from_matchlist(matchlist)
    total_kills: dict = get_kills_from_matchlist(matchlist)
    total_rounds_played: dict = get_total_rounds_played_from_matchlist(matchlist)
    matches_played: dict = {}
    for match in matchlist:
        # Increment matches_played for all players in the match
        for player in match["players"]:
            player_name = (player["name"] + "#" + player["tag"]).lower()
            matches_played[player_name] = matches_played.get(player_name, 0) + 1

    for stat_name, stat_data in zip(
            ["matches_played", "losses", "wins", "mmr", "total_combat_score", "total_deaths", "total_kills",
             "total_rounds_played", "average_combat_score", "kill_death_ratio"],
            [matches_played, losses, wins, mmr, total_combat_score, total_deaths, total_kills, total_rounds_played,
             total_rounds_played, total_rounds_played],
    ):
        for player_name, new_value in stat_data.items():
            existing_data = mmr_collection.find_one({"name": player_name})
            if existing_data:
                old_value = existing_data.get(stat_name, 0)
                new_value = new_value
                if stat_name == "average_combat_score":
                    old_combat_score = existing_data.get("total_combat_score", 0)
                    new_combat_score = old_combat_score + total_combat_score[player_name]

                    old_total_rounds_played = existing_data.get("total_rounds_played", 0)
                    new_total_rounds_played = old_total_rounds_played + total_rounds_played[player_name]

                    old_average_combat_score = existing_data.get("average_combat_score", 0)
                    new_average_combat_score = round(new_combat_score / new_total_rounds_played, 2)

                    change = StatChange(
                        player_name=player_name,
                        stat_name=stat_name,
                        old=old_average_combat_score,
                        new=new_average_combat_score
                    )
                    changes.append(change)
                elif stat_name == "kill_death_ratio":
                    old_kills = existing_data.get("total_kills", 0)
                    new_kills = old_kills + total_kills[player_name]

                    old_deaths = existing_data.get("total_deaths", 0)
                    new_deaths = old_deaths + total_deaths[player_name]

                    old_kd = existing_data.get("kill_death_ratio", 0)
                    new_kd = round(new_kills / new_deaths, 2)

                    change = StatChange(
                        player_name=player_name,
                        stat_name=stat_name,
                        old=old_kd,
                        new=new_kd
                    )
                    changes.append(change)
                else:
                    change = StatChange(
                        player_name=player_name,
                        stat_name=stat_name,
                        old=old_value,
                        new=new_value,
                    )
                    changes.append(change)

    return changes


def get_changes_that_will_be_made(match):
    from DebugTools.helpers.stat_getters import (
        get_losses_from_match,
        get_wins_from_match,
        get_combat_score_from_match,
        get_deaths_from_match,
        get_kills_from_match,
        get_total_rounds_played_from_match,
        get_winning_team_id
    )

    changes: list[StatChange] = []

    winning_team_id = get_winning_team_id(match)

    winning_team = []
    losing_team = []

    for player in match["players"]:
        if player["team_id"] == winning_team_id:
            winning_team.append(player)
        else:
            losing_team.append(player)

    mmr_changes = get_mmr_changes(winning_team, losing_team)
    changes.extend(mmr_changes)

    # Use stat getters to calculate other stats
    losses: dict = get_losses_from_match(match)
    wins: dict = get_wins_from_match(match)
    total_combat_score: dict = get_combat_score_from_match(match)
    total_deaths: dict = get_deaths_from_match(match)
    total_kills: dict = get_kills_from_match(match)
    total_rounds_played: dict = get_total_rounds_played_from_match(match)

    for stat_name, stat_data in zip(
            ["losses", "wins", "total_combat_score", "total_deaths", "total_kills", "total_rounds_played",
             "average_combat_score", "kill_death_ratio"],
            [losses, wins, total_combat_score, total_deaths, total_kills, total_rounds_played, total_rounds_played,
             total_rounds_played],
    ):
        for player_name, new_value in stat_data.items():
            existing_data = mmr_collection.find_one({"name": player_name})
            if existing_data:
                old_value = existing_data.get(stat_name, 0)
                new_value = old_value + new_value
                if stat_name == "average_combat_score":
                    old_combat_score = existing_data.get("total_combat_score", 0)
                    new_combat_score = old_combat_score + total_combat_score[player_name]

                    old_total_rounds_played = existing_data.get("total_rounds_played", 0)
                    new_total_rounds_played = old_total_rounds_played + total_rounds_played[player_name]

                    old_average_combat_score = existing_data.get("average_combat_score", 0)
                    new_average_combat_score = round(new_combat_score / new_total_rounds_played, 2)

                    change = StatChange(
                        player_name=player_name,
                        stat_name=stat_name,
                        old=old_average_combat_score,
                        new=new_average_combat_score
                    )
                    changes.append(change)
                elif stat_name == "kill_death_ratio":
                    old_kills = existing_data.get("total_kills", 0)
                    new_kills = old_kills + total_kills[player_name]

                    old_deaths = existing_data.get("total_deaths", 0)
                    new_deaths = old_deaths + total_deaths[player_name]

                    old_kd = existing_data.get("kill_death_ratio", 0)
                    new_kd = round(new_kills / new_deaths, 2)

                    change = StatChange(
                        player_name=player_name,
                        stat_name=stat_name,
                        old=old_kd,
                        new=new_kd
                    )
                    changes.append(change)
                else:
                    change = StatChange(
                        player_name=player_name,
                        stat_name=stat_name,
                        old=old_value,
                        new=new_value,
                    )
                    changes.append(change)

    # Increment matches_played for all players in the match
    for player in match["players"]:
        player_name = (player["name"] + "#" + player["tag"]).lower()
        existing_data = mmr_collection.find_one({"name": player_name})
        if existing_data:
            old_matches_played = existing_data.get("matches_played", 0)
            new_matches_played = old_matches_played + 1
            change = StatChange(
                player_name=player_name,
                stat_name="matches_played",
                old=old_matches_played,
                new=new_matches_played,
            )
            changes.append(change)

    return changes


def make_changes(changes: list[StatChange], match=None):
    for change in changes:
        # Add old value to the new value for cumulative stats, replace for calculated stats
        if change.stat_name in ["average_combat_score", "kill_death_ratio"]:
            mmr_collection.update_one(
                {"name": change.player_name},
                {"$set": {change.stat_name: change.new}}
            )
        else:
            mmr_collection.update_one(
                {"name": change.player_name},
                {"$set": {change.stat_name: change.new}}
            )

    print("Changes have been successfully applied to the database.")

    if match:
        all_matches.insert_one(match)


def confirm_changes(changes: list[StatChange], match=None):
    """
    Confirm with the user if they want to proceed with applying the changes.

    Args:
        changes (list[StatChange]): List of changes to be confirmed.

    Returns:
        bool: True if user confirms, False otherwise.
    """
    print("The following changes will be made:")
    display_changes(changes)

    confirmation = input("Do you want to proceed with these changes? (Y/n): ").strip().lower()
    if confirmation == "y":
        make_changes(changes, match)
        return True
    else:
        print("No changes have been applied.")
        return False


def display_changes(changes: list[StatChange]):
    for change in changes:
        print(f"{change.player_name} {change.stat_name}: {change.old} -> {change.new}")


def get_mmr_changes(winning_team, losing_team) -> list[StatChange]:
    mmr_changes = []
    MMR_CONSTANT = 32

    player_mmr_data = {}

    for player in winning_team + losing_team:
        riot_name = (player["name"] + "#" + player["tag"]).lower()
        player_data = mmr_collection.find_one({"name": riot_name})

        if not player_data:
            # Initialize missing player data in the database
            default_mmr = 1000
            discord_id = users.find_one({"name": player["name"].lower(), "tag": player["tag"].lower()}).get(
                "discord_id", 0)
            player_data = {
                "player_id": discord_id,
                "name": riot_name,
                "mmr": default_mmr,
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
            mmr_collection.insert_one(player_data)

        player_mmr_data[riot_name] = player_data["mmr"]

    # Calculate average MMR for winning and losing teams
    winning_team_mmr = sum(
        player_mmr_data[(player["name"] + "#" + player["tag"]).lower()] for player in winning_team
    ) / len(winning_team)
    losing_team_mmr = sum(
        player_mmr_data[(player["name"] + "#" + player["tag"]).lower()] for player in losing_team
    ) / len(losing_team)

    # Calculate expected results
    expected_win = 1 / (1 + 10 ** ((losing_team_mmr - winning_team_mmr) / 400))
    expected_loss = 1 / (1 + 10 ** ((winning_team_mmr - losing_team_mmr) / 400))

    # Adjust MMR for winning team
    for player in winning_team:
        riot_name = (player["name"] + "#" + player["tag"]).lower()
        current_mmr = player_mmr_data[riot_name]
        new_mmr = current_mmr + MMR_CONSTANT * (1 - expected_win)
        new_mmr = round(new_mmr)

        mmr_change = StatChange(riot_name, "mmr", current_mmr, new_mmr)
        mmr_changes.append(mmr_change)

    # Adjust MMR for losing team
    for player in losing_team:
        riot_name = (player["name"] + "#" + player["tag"]).lower()
        current_mmr = player_mmr_data[riot_name]
        new_mmr = current_mmr + MMR_CONSTANT * (0 - expected_loss)
        new_mmr = round(new_mmr)

        mmr_change = StatChange(riot_name, "mmr", current_mmr, new_mmr)
        mmr_changes.append(mmr_change)

    return mmr_changes


def get_mmr_values_multiple_teams(winning_teams, losing_teams) -> dict:
    MMR_CONSTANT = 32

    player_mmr_data = {}
    for winning_team, losing_team in zip(winning_teams, losing_teams):
        for player in winning_team + losing_team:
            riot_name = (player["name"] + "#" + player["tag"]).lower()
            player_data = mmr_collection.find_one({"name": riot_name})

            if not player_data:
                # Initialize missing player data in the database
                default_mmr = 1000
                discord_id = users.find_one({"name": player["name"].lower(), "tag": player["tag"].lower()}).get(
                    "discord_id", 0)
                player_data = {
                    "player_id": discord_id,
                    "name": riot_name,
                    "mmr": default_mmr,
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
                mmr_collection.insert_one(player_data)

            if riot_name not in player_mmr_data:
                player_mmr_data[riot_name] = player_data["mmr"]

        # Calculate average MMR for winning and losing teams
        winning_team_mmr = sum(
            player_mmr_data[(player["name"] + "#" + player["tag"]).lower()] for player in winning_team
        ) / len(winning_team)
        losing_team_mmr = sum(
            player_mmr_data[(player["name"] + "#" + player["tag"]).lower()] for player in losing_team
        ) / len(losing_team)

        # Calculate expected results
        expected_win = 1 / (1 + 10 ** ((losing_team_mmr - winning_team_mmr) / 400))
        expected_loss = 1 / (1 + 10 ** ((winning_team_mmr - losing_team_mmr) / 400))

        # Adjust MMR for winning team
        for player in winning_team:
            riot_name = (player["name"] + "#" + player["tag"]).lower()
            current_mmr = player_mmr_data[riot_name]
            new_mmr = current_mmr + MMR_CONSTANT * (1 - expected_win)
            new_mmr = round(new_mmr)

            player_mmr_data[riot_name] = new_mmr

        # Adjust MMR for losing team
        for player in losing_team:
            riot_name = (player["name"] + "#" + player["tag"]).lower()
            current_mmr = player_mmr_data[riot_name]
            new_mmr = current_mmr + MMR_CONSTANT * (0 - expected_loss)
            new_mmr = round(new_mmr)

            player_mmr_data[riot_name] = new_mmr

    return player_mmr_data
