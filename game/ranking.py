"""Shared helpers for computing a player's leaderboard rank."""

from game.stats_helper import DEFAULT_MMR, avg_rating_of


def has_played(stats: dict) -> bool:
    """True when the player has played at least one match."""
    mp = stats.get("matches_played")
    if isinstance(mp, (int, float)):
        return mp > 0
    return (stats.get("wins", 0) + stats.get("losses", 0)) > 0


def _matches_played(stats: dict) -> int:
    mp = stats.get("matches_played")
    if isinstance(mp, (int, float)):
        return int(mp)
    return int(stats.get("wins", 0) + stats.get("losses", 0))


def leaderboard_key(pair, column: str = "mmr"):
    """Sort key behind leaderboard_order: primary column descending (as a
    negated ascending value), then the tie chain. Exposed so document-level
    consumers (leaderboard rows) sort with the exact same rule."""
    pid, s = pair
    if column == "avg_rating":
        rating = avg_rating_of(s)
        primary = rating if rating is not None else float("-inf")
    elif column == "mmr":
        primary = s.get("mmr", DEFAULT_MMR)
    else:
        primary = s.get(column, 0)
    if column == "mmr":
        # Matches/wins use the same fallback semantics as has_played.
        return (-primary, -_matches_played(s), -(s.get("wins", 0) or 0), str(pid))
    return (-primary, str(pid))


def leaderboard_order(entries, column: str = "mmr") -> list[tuple[str, dict]]:
    """Deterministic leaderboard order over (player_id, stats) pairs.

    Players who have never played are filtered out first (Leaderboard rank:
    they have none), then the primary column sorts best-first; MMR ties
    break by matches played descending, then wins descending, then
    player_id ascending — every other column breaks ties by player_id
    ascending only. The ONE ordering for leaderboard row order and every
    stored-rank write, so the Rank column and the rows can never disagree
    on tied MMRs (issue #256).
    """
    return sorted(
        ((pid, s) for pid, s in entries if has_played(s)),
        key=lambda pair: leaderboard_key(pair, column),
    )


def ranked_players(player_mmr: dict) -> list[tuple[str, dict]]:
    """Players who have played, in canonical leaderboard order."""
    return leaderboard_order(list(player_mmr.items()), "mmr")


def position_of(player_mmr: dict, player_id: str) -> int | None:
    """1-based leaderboard rank among players who have played, or None."""
    for pos, (pid, _) in enumerate(ranked_players(player_mmr), start=1):
        if pid == player_id:
            return pos
    return None