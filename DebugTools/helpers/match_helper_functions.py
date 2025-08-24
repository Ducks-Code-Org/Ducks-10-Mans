from datetime import datetime

import requests
import pytz
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi

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

    player_names = ",".join(
        player["name"] + "#" + player["tag"] for player in blue_players
    )
    return player_names


def get_red_team(match):
    players = match["players"]

    red_players = []
    for player in players:
        if player["team_id"] == "Red":
            red_players.append(player)

    player_names = ",".join(
        player["name"] + "#" + player["tag"] for player in red_players
    )
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


def get_total_rounds(match):
    return match["teams"][0]["rounds"]["lost"] + match["teams"][0]["rounds"]["won"]


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


def get_matches_from_season(start_time, end_time=""):
    # Construct the query
    if end_time:
        query = {"metadata.started_at": {"$gte": start_time, "$lte": end_time}}
    else:
        query = {"metadata.started_at": {"$gte": start_time}}
    unique_matches_dict = {}
    # Execute the query and return the results
    matches = all_matches.find(query)
    matches_list = list(matches)
    for match in matches_list:
        match_id = match["metadata"]["match_id"]
        if match_id not in unique_matches_dict:
            unique_matches_dict[match_id] = match

    return unique_matches_dict.values()
