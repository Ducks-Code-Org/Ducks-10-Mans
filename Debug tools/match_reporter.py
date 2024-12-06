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
uri = os.getenv("uri_key")
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
            "Authorization": os.getenv("api_key"),
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
            get_changes_that_will_be_made(matchlist[match_index])
            return matchlist[match_index]
        print("")

def get_total_rounds(match):
    return match["teams"][0]["rounds"]["lost"] + match["teams"][0]["rounds"]["won"]

def get_winning_team_id(match):
    teams = match["teams"]
    blue_score = 0
    red_score = 0
    for team in teams:
        if team["team_id"] == "Blue":
            blue_score += team["rounds"]["won"]
        elif team["team_id"] == "Red":
            red_score += team["rounds"]["won"]

    if blue_score > red_score:
        return "Blue"
    elif red_score > blue_score:
        return "Red"
    else:
        return "Draw"

def get_mmr_changes(winning_team, losing_team) -> list[StatChange]:
    mmr_changes = []
    MMR_CONSTANT = 32

    player_mmr_data = {}

    for player in winning_team:
        riot_name = player["name"] + "#" + player["tag"]
        player_data = mmr_collection.find_one({"name": riot_name})
        player_mmr_data[riot_name] = player_data

    # Calculate average MMR for winning and losing teams
    winning_team_mmr = sum(
        player_mmr_data[player["name"]+"#"+player["tag"]] for player in winning_team
    ) / len(winning_team)
    losing_team_mmr = sum(
        player_mmr_data[player["name"]+"#"+player["tag"]] for player in losing_team
    ) / len(losing_team)

    # Calculate expected results
    expected_win = 1 / (1 + 10 ** ((losing_team_mmr - winning_team_mmr) / 400))
    expected_loss = 1 / (1 + 10 ** ((winning_team_mmr - losing_team_mmr) / 400))

    # Adjust MMR for winning team
    for player in winning_team:
        riot_name = player["name"]+player["tag"]
        current_mmr = player_mmr_data[riot_name]
        new_mmr = current_mmr + MMR_CONSTANT * (1 - expected_win)
        new_mmr = round(new_mmr)

        mmr_change = StatChange(riot_name, "mmr", current_mmr, new_mmr)
        mmr_changes.append(mmr_change)


    # Adjust MMR for losing team
    for player in winning_team:
        riot_name = player["name"] + player["tag"]
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
    changes: list[StatChange] = []

    total_rounds = get_total_rounds(match)
    winning_team_id = get_winning_team_id(match)

    winning_team = []
    losing_team = []

    for player in match["players"]:
        if player["team_id"] == winning_team_id:
            winning_team.append(player)
        else:
            losing_team.append(player)
    changes = get_mmr_changes(winning_team, losing_team)

    display_changes(changes)




get_match_to_upload(get_custom_matchlist("Duck", "MST"))








