"""Self-check: team displays show rank mentions instead of raw MMR (issue #212)."""

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

from game.ranks import RANKS, display_rank_for  # noqa: E402


class _Role:
    def __init__(self, name):
        self.name = name
        self.mention = f"<@&{name}>"


class _Guild:
    def __init__(self, roles):
        self.roles = roles


def demo():
    # Threshold 100 → "Stone Rank"; below 100 with matches → Wood Rank.
    guild = _Guild([_Role("Stone Rank")])
    assert (
        display_rank_for(guild, 150, matches_played=5) == "<@&Stone Rank>"
    ), "played players show their rank-role mention"

    # Unranked: zero matches → plaintext, never a role mention.
    assert display_rank_for(guild, 150, matches_played=0) == "Unranked"
    assert display_rank_for(guild, 0, matches_played=0) == "Unranked"

    # Rank with no role in the guild falls back to the plain name (not MMR).
    guild_no_role = _Guild([])
    assert display_rank_for(guild_no_role, 150, matches_played=5) == "@Stone Rank"

    # Plain-text surfaces (select-menu labels) get the bare tier name, never
    # a mention and never raw MMR.
    assert display_rank_for(guild, 150, matches_played=5, mention=False) == "Stone Rank"
    assert display_rank_for(guild_no_role, 150, matches_played=5, mention=False) == (
        "Stone Rank"
    )
    assert display_rank_for(guild, 150, matches_played=0, mention=False) == "Unranked"

    # The exact RANKS thresholds still resolve (nothing drifted).
    for threshold, name, _ in RANKS:
        guild_all = _Guild([_Role(name)])
        assert name in display_rank_for(guild_all, threshold, matches_played=1)

    print("rank-display self-checks passed")


if __name__ == "__main__":
    demo()
