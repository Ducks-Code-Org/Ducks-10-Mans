"""This file stores global variables that are used throughout the program. These will need to be updated based on map pool changes."""

import os
from zoneinfo import ZoneInfo

# Global constants
API_KEY: str | None = os.getenv("api_key")  # For HenrikDev API
URI_KEY: str | None = os.getenv("uri_key")  # URI for MongoDB
BOT_TOKEN: str | None = os.getenv("bot_token")  # Discord bot token

TIME_ZONE_CST: ZoneInfo = ZoneInfo("America/Chicago")

# Developer Tools/Settings
SEASON_2_START_DATE = "2024-12-15T00:00:00.000Z"
MATCHES_PER_SEASON = 20

mock_match_data = {
    "players": [
        {
            "name": "Samurai",
            "tag": "Mai",
            "team_id": "red",
            "stats": {"score": 8136, "kills": 29, "deaths": 16, "assists": 8},
        },
        {
            "name": "WaffIes",
            "tag": "NA1",
            "team_id": "red",
            "stats": {"score": 6048, "kills": 20, "deaths": 20, "assists": 6},
        },
        {
            "name": "DeagleG",
            "tag": "Y33T",
            "team_id": "red",
            "stats": {"score": 5928, "kills": 24, "deaths": 14, "assists": 13},
        },
        {
            "name": "TheAlphaEw0k",
            "tag": "MST",
            "team_id": "red",
            "stats": {"score": 5688, "kills": 21, "deaths": 18, "assists": 3},
        },
        {
            "name": "dShocc1",
            "tag": "LNEUP",
            "team_id": "red",
            "stats": {"score": 1368, "kills": 3, "deaths": 15, "assists": 12},
        },
        {
            "name": "Nisom",
            "tag": "zia",
            "team_id": "blue",
            "stats": {"score": 8424, "kills": 30, "deaths": 19, "assists": 5},
        },
        {
            "name": "mizu",
            "tag": "yor",
            "team_id": "blue",
            "stats": {"score": 7368, "kills": 26, "deaths": 20, "assists": 3},
        },
        {
            "name": "Duck",
            "tag": "MST",
            "team_id": "blue",
            "stats": {"score": 3528, "kills": 11, "deaths": 19, "assists": 5},
        },
        {
            "name": "twentytwo",
            "tag": "4249",
            "team_id": "blue",
            "stats": {"score": 3240, "kills": 12, "deaths": 16, "assists": 3},
        },
        {
            "name": "mintychewinggum",
            "tag": "8056",
            "team_id": "blue",
            "stats": {"score": 1656, "kills": 4, "deaths": 21, "assists": 11},
        },
    ],
    "teams": [
        {"team_id": "red", "won": True, "rounds_won": 13, "rounds_lost": 11},
        {"team_id": "blue", "won": False, "rounds_won": 11, "rounds_lost": 13},
    ],
}
