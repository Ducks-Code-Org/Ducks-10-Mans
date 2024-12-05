"""This file is responsible for importing and linking the bot to the mongodb collections."""

import os

from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi


# MongoDB Connection
uri = os.getenv("uri_key")
client = MongoClient(uri, server_api=ServerApi("1"))

# Initialize MongoDB Collections
db = client["valorant"]
users = db["users"]
mmr_collection = db["mmr_data"]
all_matches = db["matches"]
