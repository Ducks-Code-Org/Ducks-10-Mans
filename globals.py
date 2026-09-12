"""This file stores global variables that are used throughout the program. These will need to be updated based on map pool changes."""

import configparser
import os
from zoneinfo import ZoneInfo

# Global constants
API_KEY: str | None = os.getenv("api_key")  # For HenrikDev API
URI_KEY: str | None = os.getenv("uri_key")  # URI for MongoDB
BOT_TOKEN: str | None = os.getenv("bot_token")  # Discord bot token

TIME_ZONE_CST: ZoneInfo = ZoneInfo("America/Chicago")

# Read-only bot configuration (bot.ini). Used by future updates to
# enable and disable certain features via the [features] section.
_config = configparser.ConfigParser()
_config.read(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.ini"))
BOT_CONFIG = _config
BOT_FEATURES = _config["features"] if _config.has_section("features") else {}
