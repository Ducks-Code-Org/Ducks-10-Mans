"""This purpose of this file is to be able to recalculate the stats of all users in the database."""
import requests

from stat_getters import (get_losses_from_match, get_wins_from_match,
                          get_combat_score_from_match, get_deaths_from_match,
                          get_kills_from_match, get_total_rounds_played_from_match)
from stat_change import StatChange

def display_change(change: StatChange):
    print(f"({change.player_name}) {change.collection.name}-{change.stat_name}: {change.old}->{change.new}")

def display_all_changes(stat_changes: list[StatChange]):
    for change in stat_changes:
        display_change(change)

def update_player_stats_dict(player_stats: dict[str, dict[str, int]], stat_data: dict[str, int], stat_name: str):
    for player in stat_data:
        if player in player_stats:
            player_stats[player][stat_name] += stat_data[player]

def calculate_player_stats(mmr_data_collection, matches_collection):
    player_stats: dict[str, dict[str, int]] = {}

    # Get every player name and initialize their stats
    for mmr_data in mmr_data_collection.find():
        player_stats[mmr_data["name"]] = {
            "losses": 0,
            "wins": 0,
            "total_combat_score": 0,
            "matches_played": 0,
            "total_deaths": 0,
            "total_kills": 0,
            "total_rounds_played": 0,
        }

    # Update player stats dict from all match data
    for match in matches_collection.find():
        losses = get_losses_from_match(match)
        wins = get_wins_from_match(match)
        total_rounds_played = get_total_rounds_played_from_match(match)
        total_combat_score = get_combat_score_from_match(match)
        total_deaths = get_deaths_from_match(match)
        total_kills = get_kills_from_match(match)

        update_player_stats_dict(player_stats, losses, "losses")
        update_player_stats_dict(player_stats, wins, "wins")
        update_player_stats_dict(player_stats, total_rounds_played, "total_rounds_played")
        update_player_stats_dict(player_stats, total_combat_score, "total_combat_score")
        update_player_stats_dict(player_stats, total_deaths, "total_deaths")
        update_player_stats_dict(player_stats, total_kills, "total_kills")

    return player_stats

def get_changes_from_calculated_player_stats(calculated_player_stats: dict[str, dict[str, int]], mmr_data_collection) -> list[StatChange]:
    changes: list[StatChange] = []

    original_player_stats = {}
    for mmr_data in mmr_data_collection.find():
        original_player_stats[mmr_data["name"]] = mmr_data

    for player_name in calculated_player_stats:
        for stat_name in calculated_player_stats[player_name]:
            document_filter = {"name": player_name}
            old_stat = original_player_stats[player_name][stat_name]
            new_stat = calculated_player_stats[player_name][stat_name]

            change = StatChange(mmr_data_collection, document_filter, player_name, stat_name, old_stat, new_stat)
            changes.append(change)

    return changes

def get_custom_matchlist(name, tag):
    response = requests.get(
        f"https://api.henrikdev.xyz/valorant/v4/matches/na/pc/{name}/{tag}?mode=custom",
        headers={
            "Authorization": os.getenv("api_key"),
        },
    )
    data = response.json()["data"]
    return data

def upload_matches(matches_collection):
    for match in get_custom_matchlist("Duck", "MST"):
        matches_collection.insert_one(match)

if __name__ == "__main__":
    import os

    from pymongo.mongo_client import MongoClient
    from pymongo.server_api import ServerApi

    # MongoDB Connection
    uri = os.getenv("uri_key")
    client = MongoClient(uri, server_api=ServerApi("1"))

    # Initialize MongoDB Collections
    db = client["valorant"]
    mmr_collection = db["mmr_data"]
    all_matches = db["matches"]

    # upload_matches(all_matches)

    stats = calculate_player_stats(mmr_collection, all_matches)
    changes = get_changes_from_calculated_player_stats(stats, mmr_collection)
    display_all_changes(changes)