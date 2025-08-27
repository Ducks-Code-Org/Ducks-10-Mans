# database.py
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi

from globals import URI_KEY

client = MongoClient(URI_KEY, server_api=ServerApi("1"), serverSelectionTimeoutMS=8000)

try:
    client.admin.command("ping")
    print("[DB] Mongo ping OK")
except Exception as e:
    raise SystemExit(f"[DB] Mongo connection failed: {e}")

# Models
db = client["valorant"]
users = db["users"]
mmr_collection = db["mmr_data"]
tdm_mmr_collection = db["tdm_mmr_data"]
all_matches = db["matches"]
tdm_matches = db["tdm_matches"]
seasons = db["seasons"]
interests = db["interests"]
