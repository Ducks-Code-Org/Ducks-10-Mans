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

    # --- issue #159: delta_mmr math --------------------------------------
    d = stats_helper.delta_mmr
    # Equal MMR, even rounds, 1.0 VLR → the +8.57 baseline
    assert abs(d(13, 13, 500, 500, 1.0) - 60 / 7) < 1e-9
    # Calibration curve points: h(0.5)=0, h(0.7)=1, h(1.0)=4, h(1.3)=5
    h = stats_helper._h
    assert h(0.5) == 0.0 and h(0.7) == 1.0 and h(1.0) == 4.0 and h(1.3) == 5.0
    # r saturates at ±1 (i.e. ±4.3 round diff)
    assert abs(d(20, 5, 500, 500, 1.0) - d(17.3, 13, 500, 500, 1.0)) < 1e-9
    # Big favorite (5x) winning evenly at 1.0 VLR: expectation term is
    # negative (m = 5^0.75); equal-MMR even result is the +8.57 baseline.
    assert d(13, 13, 2500, 500, 1.0) < 0 < d(13, 13, 500, 500, 1.0)
    # Underdog (1/5) winning: sqrt-damped, +12.2 max on expectation term
    assert d(13, 13, 500, 2500, 1.0) > 60 / 7
    # Corner bonus: only fires on round lead AND vlr > 1.0
    no_lead = d(10, 13, 500, 500, 1.3) - d(10, 13, 500, 500, 1.0)
    with_lead = d(13, 10, 500, 500, 1.3) - d(13, 10, 500, 500, 1.0)
    assert no_lead > 0 and with_lead > no_lead

    # --- issue #159: first-match seeding + 0 floor ------------------------
    # First match: MMR seeded to 100*VLR then delta applied (always > 0 seed)
    store5 = {}
    stats_helper.update_stats(
        {"stats": {"score": 900, "kills": 8, "deaths": 4}},
        20,
        store5,
        {},
        discord_id="5",
        team_avg_mmr=400,
        opp_avg_mmr=400,
        our_rounds=13,
        opp_rounds=5,
        rating=1.4,
    )
    assert store5["5"]["mmr"] > 100, store5["5"]  # seed 140 + positive delta
    assert store5["5"]["wins"] == 1 and store5["5"]["losses"] == 0

    # Veteran losing hard cannot go below 0 (e.g. someone at 10 MMR whose
    # delta is -22 must land at 0, not negative)
    store6 = {
        "6": {
            "mmr": 10,
            "matches_played": 5,
            "wins": 2,
            "losses": 3,
            "total_rounds_played": 100,
        }
    }
    stats_helper.update_stats(
        {"stats": {"score": 100, "kills": 2, "deaths": 15}},
        15,
        store6,
        {},
        discord_id="6",
        team_avg_mmr=800,
        opp_avg_mmr=100,
        our_rounds=3,
        opp_rounds=13,
        rating=0.7,
    )
    assert store6["6"]["mmr"] == 0, store6["6"]
    assert store6["6"]["losses"] == 4

    # No rating data → vlr defaults to 1.0 in the delta (no crash, seed 0)
    store7 = {}
    stats_helper.update_stats(
        {"stats": {"score": 500, "kills": 5, "deaths": 10}},
        15,
        store7,
        {},
        discord_id="7",
        team_avg_mmr=0,
        opp_avg_mmr=300,
        our_rounds=5,
        opp_rounds=13,
        rating=None,
    )
    assert store7["7"]["mmr"] >= 0, store7["7"]

    # --- ranks: tier thresholds and ordering ------------------------------
    from ranks import RANKS, rank_of

    assert rank_of(0) == "Wood Rank"
    assert rank_of(99) == "Wood Rank"
    assert rank_of(100) == "Stone Rank"
    assert rank_of(299) == "Iron Rank"
    assert rank_of(750) == "Mother-Ducker Rank"
    assert rank_of(10000) == "Mother-Ducker Rank"
    # rank_of is purely MMR-based and never returns Supersonic Radiant.
    assert rank_of(500) != "Supersonic Radiant"
    assert rank_of(-5) is None  # unplayed players get no tier
    # Thresholds strictly ascending when listed low-to-high
    thresholds = sorted(t for t, _, _ in RANKS)
    assert thresholds == [0, 100, 200, 300, 400, 500, 750]

    # Unplayed players (0 matches) get no tier even at position 1, matching
    # the rank-role sync which only ranks players who played (issue #159).
    from ranks import tiers_for_player, tier_for_player

    assert tiers_for_player(1200, position=1, matches_played=0) == []
    # Rank 1 wears Supersonic Radiant IN ADDITION to their traditional tier.
    assert tiers_for_player(1200, position=1, matches_played=3) == [
        "Mother-Ducker Rank",
        "Supersonic Radiant",
    ]
    assert tiers_for_player(0, position=5, matches_played=0) == []
    assert tiers_for_player(0, position=5, matches_played=1) == ["Wood Rank"]
    # Non-rank-1 players hold only their traditional tier.
    assert tiers_for_player(1200, position=2, matches_played=3) == [
        "Mother-Ducker Rank"
    ]

    # tier_for_player shows only the traditional tier (SSR status is a
    # separate role/display, never a replacement for the MMR tier).
    assert tier_for_player(1200, matches_played=0) is None
    assert tier_for_player(1200, matches_played=3) == "Mother-Ducker Rank"
    assert tier_for_player(0, matches_played=1) == "Wood Rank"

    print("vlr_rating self-check OK")


if __name__ == "__main__":
    main()
