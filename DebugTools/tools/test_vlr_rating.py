"""Self-check for vlr_rating.estimate_ratings_v4.

Builds a synthetic but structurally faithful HenrikDev v4 match payload
(2 rounds, 2 players per side, full kill timeline) and asserts the rating
math: feature extraction, round weighting, and the persistence aggregation
in stats_helper.
"""

import os
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

# Stub modules with import-time side effects (Mongo connection) so the
# stats_helper aggregation can be exercised without a database.
import types

_database_stub = types.ModuleType("database")
_database_stub.mmr_collection = types.SimpleNamespace(update_one=lambda *a, **k: None)
sys.modules["database"] = _database_stub

from vlr_rating import estimate_ratings_v4


def make_match():
    """Two rounds, Red wins both; Alice goes 3-0 with a 2K, Bob 0-2."""
    players = [
        {
            "puuid": "alice",
            "name": "Alice",
            "tag": "ALY",
            "team_id": "Red",
            "stats": {
                "headshots": 2,
                "bodyshots": 4,
                "legshots": 0,
                "damage": {"dealt": 480},
            },
        },
        {
            "puuid": "bob",
            "name": "Bob",
            "tag": "B",
            "team_id": "Red",
            "stats": {
                "headshots": 0,
                "bodyshots": 0,
                "legshots": 0,
                "damage": {"dealt": 0},
            },
        },
        {
            "puuid": "carl",
            "name": "Carl",
            "tag": "C",
            "team_id": "Blue",
            "stats": {
                "headshots": 1,
                "bodyshots": 3,
                "legshots": 0,
                "damage": {"dealt": 240},
            },
        },
        {
            "puuid": "dan",
            "name": "Dan",
            "tag": "D",
            "team_id": "Blue",
            "stats": {
                "headshots": 0,
                "bodyshots": 2,
                "legshots": 0,
                "damage": {"dealt": 140},
            },
        },
    ]
    teams = [{"team_id": "Red", "won": True}, {"team_id": "Blue", "won": False}]
    rounds = [
        {
            "id": 0,
            "plant": {"player": {"puuid": "alice"}},
            "stats": [
                {"player": {"team": "Red"}, "economy": {"loadout_value": 4000}},
                {"player": {"team": "Blue"}, "economy": {"loadout_value": 2000}},
            ],
        },
        {
            "id": 1,
            "defuse": {"player": {"puuid": "alice"}},
            "stats": [
                {"player": {"team": "Red"}, "economy": {"loadout_value": 3900}},
                {"player": {"team": "Blue"}, "economy": {"loadout_value": 2100}},
            ],
        },
    ]
    kills = [
        # Round 0: Alice double kill (Carl, Dan)
        {
            "round": 0,
            "time_in_round_in_ms": 1000,
            "killer": {"puuid": "alice"},
            "victim": {"puuid": "carl"},
            "assistants": [],
        },
        {
            "round": 0,
            "time_in_round_in_ms": 5000,
            "killer": {"puuid": "alice"},
            "victim": {"puuid": "dan"},
            "assistants": [],
        },
        # Round 1: Alice kills Carl; Dan kills Bob (so Bob dies twice, Dan 1 kill)
        {
            "round": 1,
            "time_in_round_in_ms": 2000,
            "killer": {"puuid": "alice"},
            "victim": {"puuid": "carl"},
            "assistants": [],
        },
        {
            "round": 1,
            "time_in_round_in_ms": 4000,
            "killer": {"puuid": "dan"},
            "victim": {"puuid": "bob"},
            "assistants": [],
        },
    ]
    return {"players": players, "teams": teams, "rounds": rounds, "kills": kills}


def main():
    ratings = estimate_ratings_v4(make_match())
    assert set(ratings) == {"alice", "bob", "carl", "dan"}, ratings

    alice = ratings["alice"]
    assert alice["k"] == 3 and alice["d"] == 0 and alice["a"] == 0, alice
    assert alice["rounds"] == 2, alice
    assert alice["team_id"] == "Red" and alice["name"] == "Alice#ALY", alice
    # Alice had a 2K round, a plant and a defuse, and Red won: her rating must
    # beat the closed-form baseline (which ignores situational terms).
    assert alice["rating"] > alice["rating_basic"] > 1.0, alice

    bob = ratings["bob"]
    assert bob["k"] == 0 and bob["d"] == 1, bob
    assert bob["rating"] < alice["rating"], (bob, alice)
    # Bob (0/0/0 over 2 rounds) must sit below the neutral intercept.
    assert bob["rating"] < 1.0, bob

    dan = ratings["dan"]
    assert dan["k"] == 1 and dan["d"] == 1, dan
    assert 0.5 < dan["rating"] < 1.5, dan

    # Degenerate inputs return {} instead of raising.
    assert estimate_ratings_v4({}) == {}
    assert estimate_ratings_v4({"players": [], "rounds": []}) == {}

    # --- stats_helper round-weighted aggregation -------------------------
    import stats_helper

    # The average is derived from the persisted totals, so a player loaded
    # from the DB without an avg_rating key still displays correctly, and a
    # match with no rating preserves their recorded average.
    assert (
        abs(
            stats_helper.avg_rating_of(
                {"total_rating_points": 6.0, "total_rating_rounds": 4}
            )
            - 1.5
        )
        < 1e-9
    )
    assert stats_helper.avg_rating_of({}) is None
    assert (
        stats_helper.avg_rating_of(
            {"total_rating_points": 0.0, "total_rating_rounds": 0}
        )
        is None
    )

    store = {
        "1": {
            "mmr": 1000,
            "wins": 1,
            "losses": 0,
            "total_rating_points": 4.0,
            "total_rating_rounds": 2,  # prior: avg 2.0 over 2 rounds
        }
    }
    player = {"stats": {"score": 1000, "kills": 5, "deaths": 2}}
    stats_helper.update_stats(
        player,
        4,
        store,
        {},
        discord_id="1",
        rating=1.5,
    )
    data = store["1"]
    # (4.0 + 1.5*4) / (2 + 4) = 10/6
    assert abs(data["avg_rating"] - 10 / 6) < 1e-9, data
    assert data["total_rating_rounds"] == 6, data

    # Existing players without rating fields still update fine (None persisted)
    store2 = {"2": {"mmr": 1000, "matches_played": 3, "total_rounds_played": 30}}
    stats_helper.update_stats(
        {"stats": {"score": 500, "kills": 2, "deaths": 1}},
        5,
        store2,
        {},
        discord_id="2",
    )
    assert store2["2"].get("total_rating_rounds") == 0, store2["2"]
    assert store2["2"].get("avg_rating") is None, store2["2"]

    # A rating-less match must not clobber an average loaded from totals.
    store4 = {
        "4": {
            "mmr": 1000,
            "matches_played": 2,
            "total_rating_points": 6.0,
            "total_rating_rounds": 4,
        }
    }
    stats_helper.update_stats(
        {"stats": {"score": 100, "kills": 1, "deaths": 1}},
        4,
        store4,
        {},
        discord_id="4",
    )
    assert abs(store4["4"]["avg_rating"] - 1.5) < 1e-9, store4["4"]

    # New-player branch records the rating of their first match
    store3 = {}
    stats_helper.update_stats(
        {"stats": {"score": 800, "kills": 3, "deaths": 3}},
        2,
        store3,
        {},
        discord_id="3",
        rating=1.1,
    )
    assert abs(store3["3"]["avg_rating"] - 1.1) < 1e-9, store3["3"]

    print("vlr_rating self-check OK")


if __name__ == "__main__":
    main()
