"""INCOMPLETE. This file is not connected to the bot. The purpose is to be able to add match data to the database in case something goes wrong"""

import os
import requests
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from datetime import datetime
import pytz

class StatChange:
    def __init__(self, player_name, stat_name, old, new):
        self.player_name = player_name
        self.stat_name = stat_name
        self.old = old
        self.new = new

def convert_to_central_time(utc_timestamp):
    """
    Convert an ISO 8601 UTC timestamp to Central Time.

    Args:
        utc_timestamp (str): The UTC timestamp in ISO 8601 format (e.g., "2024-12-06T06:50:54.005Z").

    Returns:
        str: The converted Central Time as a string in ISO 8601 format.
    """
    # Parse the input UTC timestamp
    utc_time = datetime.strptime(utc_timestamp, "%Y-%m-%dT%H:%M:%S.%fZ")

    # Define the UTC and Central timezones
    utc = pytz.utc
    central = pytz.timezone("US/Central")

    # Localize the UTC time and convert to Central Time
    utc_time = utc.localize(utc_time)
    central_time = utc_time.astimezone(central)

    # Return the Central Time in ISO 8601 format
    return central_time.isoformat()

# MongoDB Connection
uri = "mongodb+srv://x4skinniestduck:8QZOdjPrrgJkRGPX@rapid.12llf.mongodb.net/?retryWrites=true&w=majority&appName=Rapid"
client = MongoClient(uri, server_api=ServerApi("1"))

# Initialize MongoDB Collections
db = client["valorant"]
users = db["users"]
mmr_collection = db["mmr_data"]
all_matches = db["matches"]

def get_custom_matchlist(name, tag):
    response = requests.get(
        f"https://api.henrikdev.xyz/valorant/v4/matches/na/pc/{name}/{tag}?mode=custom",
        headers={
            "Authorization": "HDEV-0f2e4072-7536-44a8-861b-e969b6837de7",
        },
    )
    data = response.json()["data"]
    return data

def get_scoreline(match):
    teams = match["teams"]
    blue_score = 0
    red_score = 0
    for team in teams:
        if team["team_id"] == "Blue":
            blue_score += team["rounds"]["won"]
        elif team["team_id"] == "Red":
            red_score += team["rounds"]["won"]
    who_won = ""
    if blue_score > red_score:
        who_won = "Blue won"
    elif red_score > blue_score:
        who_won = "Red won"
    else:
        who_won = "Draw"
    return f"{blue_score}-{red_score} ({who_won})"

def get_blue_team(match):
    players = match["players"]

    blue_players = []
    for player in players:
        if player["team_id"] == "Blue":
            blue_players.append(player)

    player_names = ",".join(player["name"] + "#" + player["tag"] for player in blue_players)
    return player_names


def get_red_team(match):
    players = match["players"]

    red_players = []
    for player in players:
        if player["team_id"] == "Red":
            red_players.append(player)

    player_names = ",".join(player["name"] + "#" + player["tag"] for player in red_players)
    return player_names

def get_map_name_from_match(match):
    return match["metadata"]["map"]["name"]

def get_time_of_match(match):
    return convert_to_central_time(match["metadata"]["started_at"])



def display_match_info(match):
    print(f"    Score: {get_scoreline(match)}")
    print(f"    Blue Team: {get_blue_team(match)}")
    print(f"    Red Team: {get_red_team(match)}")
    print(f"    Map: {get_map_name_from_match(match)}")
    print(f"    Played On: {get_time_of_match(match)}")

def get_match_to_upload(matchlist):
    while True:
        print(f"Select match to upload (0-{len(matchlist)-1})")

        for i in range(len(matchlist)):
            print(f"{i}:")
            display_match_info(matchlist[i])
            print("")

        match_index = int(input("Enter match number: "))

        print(f"\nYou selected match {match_index}:")
        display_match_info(matchlist[match_index])

        confirm_match = input("Are you sure you want to upload this match (Y/n)? ").lower()
        if confirm_match == "y":
            confirm_changes(get_changes_that_will_be_made(matchlist[match_index]))
            return matchlist[match_index]
        print("")

def get_total_rounds(match):
    return match["teams"][0]["rounds"]["lost"] + match["teams"][0]["rounds"]["won"]


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
            discord_id = users.find_one({"name": player["name"].lower(), "tag": player["tag"].lower()})
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

def display_changes(changes: list[StatChange]):
    for change in changes:
        print(f"{change.player_name} {change.stat_name}: {change.old} -> {change.new}")
def get_changes_that_will_be_made(match):
    from stat_getters import (
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
        ["losses", "wins", "total_combat_score", "total_deaths", "total_kills", "total_rounds_played", "average_combat_score", "kill_death_ratio"],
        [losses, wins, total_combat_score, total_deaths, total_kills, total_rounds_played, total_rounds_played, total_rounds_played],
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

def confirm_changes(changes: list[StatChange]):
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

        # Upload the match to the all_matches collection
        match_data = {
            "changes": [{
                "player_name": change.player_name,
                "stat_name": change.stat_name,
                "old": change.old,
                "new": change.new
            } for change in changes]
        }
        all_matches.insert_one(match_data)

        return True
    else:
        print("No changes have been applied.")
        return False




get_match_to_upload(get_custom_matchlist("Samurai", "Mai"))








