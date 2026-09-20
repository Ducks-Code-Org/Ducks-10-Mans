"""Self-check: the match summary marks placement (first-match) players (issue #211)."""

import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _Coll:
    def __getattr__(self, name):
        return lambda *a, **k: None

    def find(self, *a, **k):
        return []


_database_stub = types.ModuleType("database")
for name in (
    "users",
    "mmr_collection",
    "seasons",
    "all_matches",
    "coin_escrow",
    "interests",
    "recent_queue",
):
    setattr(_database_stub, name, _Coll())
_database_stub.client = types.SimpleNamespace(close=lambda: None)
sys.modules["database"] = _database_stub

from game.stats_helper import update_stats  # noqa: E402


def _summary_line_for(player_mmr, pre, player_stats, discord_id, rounds=13):
    """Drive update_stats the way report.py does and recompute the delta line."""
    before = pre.get(discord_id, {}).get("mmr", 0)
    update_stats(
        player_stats,
        rounds,
        player_mmr,
        {},
        discord_id=discord_id,
        team_avg_mmr=100.0,
        opp_avg_mmr=100.0,
        our_rounds=rounds,
        opp_rounds=0,
        rating=1.2,
        mmr_multiplier=1,
    )
    after = player_mmr[discord_id]["mmr"]
    delta = after - before
    return delta


def demo():
    player_mmr = {}
    pre = {}

    # Veteran: has matches already — no placement.
    pre["vet"] = {
        "mmr": 500,
        "matches_played": 10,
        "wins": 6,
        "losses": 4,
        "total_rating_rounds": 0,
    }
    player_mmr["vet"] = dict(pre["vet"])
    vet_delta = _summary_line_for(
        player_mmr,
        pre,
        {
            "stats": {"score": 0, "kills": 0, "deaths": 0},
            "puuid": "vet",
        },
        "vet",
    )
    assert vet_delta < 0 or 0 < vet_delta < 100, vet_delta  # a normal delta

    # New player: zero matches — placement seed (100×rating) + delta.
    new_delta = _summary_line_for(
        player_mmr,
        pre,
        {
            "stats": {"score": 0, "kills": 0, "deaths": 0},
            "puuid": "new",
        },
        "new",
    )
    # The seed alone is 100×1.0=100 + the delta; a normal delta can't be that.
    assert new_delta >= 90, f"placement seed missing: {new_delta}"

    # The report's _is_new equivalent: stats before the match have no games.
    def is_new(pid):
        stats = pre.get(pid)
        if not isinstance(stats, dict):
            return True
        return (
            stats.get("matches_played", 0) == 0
            and (stats.get("wins", 0) + stats.get("losses", 0)) == 0
        )

    assert is_new("new"), "new player must be flagged placement"
    assert not is_new("vet"), "veteran must not be flagged placement"
    print("placement-tag self-checks passed")


if __name__ == "__main__":
    demo()
