from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from globals import SEASON_2_START_DATE
from DebugTools.helpers.match_helper_functions import get_matches_from_season
from DebugTools.helpers.change_helper_functions import get_matchlist_changes_that_will_be_made, confirm_changes
import os

# MongoDB Connection
uri = os.getenv("uri_key")
client = MongoClient(uri, server_api=ServerApi("1"))

# Initialize MongoDB Collections
db = client["valorant"]
mmr_collection = db["mmr_data"]
all_matches = db["matches"]


def set_data_from_stored_matches():
    season_matches = get_matches_from_season(SEASON_2_START_DATE)

    confirm = input("WARNING. All user stat data will be cleared first. Are you sure you want to continue? (Y/n): ")
    if confirm.lower() != "y":
        print("Canceled.")
        return

    mmr_collection.delete_many({})

    changes = get_matchlist_changes_that_will_be_made(season_matches)

    confirm_changes(changes)


if __name__ == "__main__":
    set_data_from_stored_matches()
