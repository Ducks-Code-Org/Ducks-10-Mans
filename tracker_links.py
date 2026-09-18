"Helpers for building tracker.gg profile links from Riot IDs."

from urllib.parse import quote

TRACKER_PROFILE_URL = "https://tracker.gg/valorant/profile/riot"

# Shown for players whose Riot account died (purge unlinked them) until they
# re-link with `!linkriot`. Their MMR stats are preserved meanwhile.
UNLINKED_DISPLAY_NAME = "N/A"


def tracker_link(name: str, tag: str) -> str:
    """Return a markdown link to the player's tracker.gg profile."""
    display = f"{name or 'Unknown'}#{tag or 'Unknown'}"
    url = f"{TRACKER_PROFILE_URL}/{quote(name or 'Unknown')}%23{quote(tag or 'Unknown')}/overview"
    return f"[{display}]({url})"


def _member_display_name(guild, discord_id: str) -> str | None:
    """The member's guild display name, or None when unavailable."""
    if guild is None:
        return None
    try:
        member = guild.get_member(int(discord_id))
    except (ValueError, TypeError):
        return None
    return member.display_name if member else None


def display_name_for(user_data, guild=None, discord_id: str = None) -> str:
    """Best-effort display name for a player, Riot ID first.

    Resolution order:
    1. Linked Riot ID (name#tag) — the player's canonical identity.
    2. Guild member display name (e.g. a purged/unlinked player, or someone
       whose Riot link died): shown by their Discord name so leaderboards and
       other displays stay human-readable until they re-link.
    3. "N/A" when the player can't be found anywhere.

    `user_data` is the users-collection doc (or None); `discord_id` defaults
    to reading it from the doc.
    """
    if user_data:
        name = (user_data.get("name") or "").strip()
        tag = (user_data.get("tag") or "").strip()
        if name and tag:
            return f"{name}#{tag}"
    pid = discord_id or ((user_data or {}).get("discord_id") if user_data else None)
    if pid:
        member_name = _member_display_name(guild, str(pid))
        if member_name:
            return member_name
    return UNLINKED_DISPLAY_NAME


def tracker_link_for(user_data, guild=None, discord_id: str = None) -> str | None:
    """A tracker link for a player, or None when they have no linked Riot ID.

    Unlinked players have no profile to link to, so callers should show the
    display name alone instead of a broken/placeholder tracker link.
    """
    if user_data:
        name = (user_data.get("name") or "").strip()
        tag = (user_data.get("tag") or "").strip()
        if name and tag:
            return tracker_link(name, tag)
    return None


def display_line_for(user_data, guild=None, discord_id: str = None) -> str:
    """A tracker link, or a plain Discord display name when unlinked.

    Linked players render as a clickable Riot-ID tracker link; players whose
    Riot link died (purged, stats preserved) render as their Discord display
    name with no link. Use this anywhere a player line previously did
    ``tracker_link(name, tag)``.
    """
    link = tracker_link_for(user_data, guild=guild, discord_id=discord_id)
    if link:
        return link
    return display_name_for(user_data, guild=guild, discord_id=discord_id)
