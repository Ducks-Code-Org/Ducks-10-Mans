"""Shared helpers for computing a player's leaderboard rank."""

from game.stats_helper import DEFAULT_MMR


def has_played(stats: dict) -> bool:
    """True when the player has played at least one match."""
    mp = stats.get("matches_played")
    if isinstance(mp, (int, float)):
        return mp > 0
    return (stats.get("wins", 0) + stats.get("losses", 0)) > 0


def ranked_players(player_mmr: dict) -> list[tuple[str, dict]]:
    """Players who have played, sorted by MMR descending (leaderboard order)."""
    played = [(pid, s) for pid, s in player_mmr.items() if has_played(s)]
    played.sort(key=lambda x: x[1].get("mmr", DEFAULT_MMR), reverse=True)
    return played


def position_of(player_mmr: dict, player_id: str) -> int | None:
    """1-based leaderboard rank among players who have played, or None."""
    for pos, (pid, _) in enumerate(ranked_players(player_mmr), start=1):
        if pid == player_id:
            return pos
    return None