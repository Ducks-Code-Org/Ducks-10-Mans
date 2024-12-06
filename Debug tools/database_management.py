"""This is an independent file. The purpose to have functions to easily manage the database if needed."""

import os
import requests
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from datetime import datetime
import pytz

uri = os.getenv("uri_key")
client = MongoClient(uri, server_api=ServerApi("1"))

# Initialize MongoDB Collections
db = client["valorant"]
users = db["users"]
mmr_collection = db["mmr_data"]
all_matches = db["matches"]

class StatChange:
    def __init__(self, collection, id, player_name, stat_name, old, new):
        self.collection = collection
        self.id = id
        self.player_name = player_name
        self.stat_name = stat_name
        self.old = old
        self.new = new

class FieldNotFound(Exception):
    """A custom exception for alterting when a field is not found."""
    def __init__(self, message):
        super().__init__(message)

# Function that raises an error if the default value is selected
def get_field_prevent_default(document, field, default):
    data = document.get(field, default)
    if data == default or data is None:
        raise FieldNotFound(f"{field} not found in {document}.")
    return data

def get_lower_names_changes() -> list[StatChange]:
    stat_changes: list[StatChange] = []
    for user in users.find():
        name = get_field_prevent_default(user, "name", "")
        tag = get_field_prevent_default(user, "tag", "")
        riot_name = name + "#" + tag

        stat_changes.append(StatChange(users, user["_id"], riot_name, "name", name, name.lower()))
        stat_changes.append(StatChange(users, user["_id"], riot_name, "tag", tag, tag.lower()))

    for user_data in mmr_collection.find():
        name = get_field_prevent_default(user_data, "name", "")
        riot_name = name

        # Lower all names in users collection
        stat_changes.append(StatChange(mmr_collection, user_data["_id"], riot_name, "name", riot_name, riot_name.lower()))

    return stat_changes

def display_change(change: StatChange):
    print(f"({change.player_name}) {change.collection.name}-{change.stat_name}: {change.old}->{change.new}")

def display_all_changes(stat_changes: list[StatChange]):
    for change in stat_changes:
        display_change(change)

def make_changes_to_database(stat_changes: list[StatChange]):
    print("\nMaking changes to database:")
    for change in stat_changes:
        display_change(change)

        collection = change.collection
        collection.update_one({"_id": change.id}, {"$set": {change.stat_name: change.new}})

    print(f"Completed Changes")



def lower_names():
    name_changes = get_lower_names_changes()
    print(f"All Changes that will be made to the database:")
    display_all_changes(name_changes)

    confirm = input("Are you sure you want to make these changes to the database (Y/n)? ")

    if confirm.lower() != "y":
        return

    make_changes_to_database(name_changes)

lower_names()
