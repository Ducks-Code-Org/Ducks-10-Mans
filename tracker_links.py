"Helpers for building tracker.gg profile links from Riot IDs."

from urllib.parse import quote

TRACKER_PROFILE_URL = "https://tracker.gg/valorant/profile/riot"


def tracker_link(name: str, tag: str) -> str:
    """Return a markdown link to the player's tracker.gg profile."""
    display = f"{name or 'Unknown'}#{tag or 'Unknown'}"
    url = f"{TRACKER_PROFILE_URL}/{quote(name or 'Unknown')}%23{quote(tag or 'Unknown')}/overview"
    return f"[{display}]({url})"
