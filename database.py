# database.py
import logging

from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi

from globals import URI_KEY

log = logging.getLogger(__name__)

client = MongoClient(
    URI_KEY,
    # TLS certificate validation is required; never disable it.
    tls=True,
    server_api=ServerApi("1"),
    serverSelectionTimeoutMS=8000,
)

try:
    client.admin.command("ping")
    log.info("Mongo ping OK")
except Exception as e:
    raise SystemExit(f"[DB] Mongo connection failed: {e}")

# Models
db = client["valorant"]
users = db["users"]
mmr_collection = db["mmr_data"]
all_matches = db["matches"]
seasons = db["seasons"]
interests = db["interests"]
recent_queue = db["recent_queue"]
# Crash-safety journal for in-process Duck Coin state (bet escrow, doubledowns,
# map-override escalation): one doc holding exactly what lives in bot.bet_session
# / bot.double_downs, so a restart can refund what an open window was holding.
coin_escrow = db["coin_escrow"]
