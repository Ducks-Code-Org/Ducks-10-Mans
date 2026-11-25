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
# standing map-override wager): one doc holding exactly what lives in
# bot.bet_session / bot.double_downs, so a restart can refund what an open
# window was holding.
coin_escrow = db["coin_escrow"]

# Conduct reporting, sanctions, and Duck Council applications (issue #269).
conduct_cases = db["conduct_cases"]
conduct_sanctions = db["conduct_sanctions"]
conduct_applications = db["conduct_applications"]

for _collection, _indexes in (
    (conduct_cases, (("subject_id", 1), ("status", 1), ("purge_after", 1))),
    (
        conduct_sanctions,
        (("subject_id", 1), ("type", 1), ("case_id", 1)),
    ),
    (conduct_applications, (("applicant_id", 1), ("status", 1), ("purge_after", 1))),
):
    for _field, _direction in _indexes:
        try:
            _collection.create_index([(_field, _direction)])
        except Exception as _e:  # index creation must never block startup
            log.warning("Could not create index %s: %s", _field, _e)
