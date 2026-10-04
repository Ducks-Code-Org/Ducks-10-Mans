"""Self-checks for the deterministic leaderboard tie ordering (issue #256).

Regression: five "sort by MMR descending" copies ranked tied players by
incidental order (dict insertion / DB document order), so the board could
render rank 40 above rank 39 and different surfaces disagree. ONE canonical
order now lives in game/ranking.leaderboard_order/leaderboard_key and every
ranking site consumes it: board rows, the rank snapshot written after each
match report, the admin-edit rank resync, the manual-import rank rebuild,
and the !stats position lookup.

Tie chain (MMR): matches played desc → wins desc → player_id asc.
Every other column: player_id asc only.
"""

import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub the Mongo-backed modules before importing anything that touches them.
_database_stub = types.ModuleType("database")
_store: dict = {}


def _update_one(query, update, upsert=False):
    pid = query["player_id"]
    doc = _store.setdefault(pid, {"player_id": pid})
    if "$inc" in update:
        for k, v in update["$inc"].items():
            doc[k] = doc.get(k, 0) + v
    for k, v in update.get("$set", {}).items():
        doc[k] = v


_database_stub.users = types.SimpleNamespace(find_one=lambda *a, **k: None)
_database_stub.mmr_collection = types.SimpleNamespace(
    find_one=lambda *a, **k: None,
    find=lambda *a, **k: [],
    update_one=_update_one,
    update_many=lambda *a, **k: None,
    delete_one=lambda *a, **k: None,
)
_database_stub.seasons = types.SimpleNamespace()
_database_stub.all_matches = types.SimpleNamespace()
_database_stub.recent_queue = types.SimpleNamespace()
_database_stub.coin_escrow = types.SimpleNamespace(
    update_one=lambda *a, **k: None, find_one=lambda *a, **k: None
)
_database_stub.client = types.SimpleNamespace()
sys.modules["database"] = _database_stub


class _FakeEmbed:
    def __init__(self, *a, **k):
        pass


_discord_stub = types.ModuleType("discord")
_discord_stub.Embed = _FakeEmbed
_discord_stub.Color = types.SimpleNamespace(
    gold=lambda: None, blurple=lambda: None
)
_discord_stub.utils = types.SimpleNamespace(get=lambda *a, **k: None)
_discord_stub.NotFound = type("NotFound", (Exception,), {})
_discord_stub.HTTPException = type("HTTPException", (Exception,), {})
_discord_stub.Interaction = type("Interaction", (), {})
_discord_stub.ext = types.SimpleNamespace()
_discord_stub.ext.commands = types.SimpleNamespace(
    command=lambda *a, **k: (lambda f: f),
    hybrid_command=lambda *a, **k: (lambda f: f),
    has_permissions=lambda **k: (lambda f: f),
    has_role=lambda *a, **k: (lambda f: f),
    dm_only=lambda *a, **k: (lambda f: f),
    Cog=type("Cog", (), {"__init_subclass__": classmethod(lambda cls, **kw: None)}),
)
_app_stub = types.SimpleNamespace(describe=lambda **k: (lambda f: f), Attachment=object)
_discord_stub.app_commands = _app_stub
_ui_stub = types.ModuleType("discord.ui")
for _n in ("Button", "Select", "View", "Modal"):
    setattr(_ui_stub, _n, type(_n, (object,), {"__init_subclass__": classmethod(lambda cls, **kw: None)}))
for _n in ("select", "button"):
    setattr(_ui_stub, _n, lambda *a, **k: (lambda f: f))
_discord_stub.ui = _ui_stub
sys.modules["discord"] = _discord_stub
sys.modules["discord.ui"] = _ui_stub
sys.modules["discord.ext"] = _discord_stub.ext
sys.modules["discord.ext.commands"] = _discord_stub.ext.commands

import commands.maintenance_commands as mc  # noqa: E402
from game.ranking import (  # noqa: E402
    leaderboard_key,
    leaderboard_order,
    position_of,
)
from views.leaderboard_view import sort_key_for  # noqa: E402


def ids(pairs):
    return [pid for pid, _ in pairs]


def demo():
    # A tied MMR pair must render/rank in ONE order, whoever enters the map
    # (the exact #256 symptom: rows 40 above 39 among tied players).
    tie = {
        "hugdung": {"mmr": 500, "matches_played": 10, "wins": 5},
        "luh4r": {"mmr": 500, "matches_played": 10, "wins": 6},
        "clear_above": {"mmr": 600, "matches_played": 3, "wins": 2},
    }
    order = leaderboard_order(list(tie.items()))
    assert ids(order) == ["clear_above", "luh4r", "hugdung"], ids(order)
    # Deterministic across input orders.
    shuffled = leaderboard_order(
        [(k, dict(v)) for k, v in reversed(list(tie.items()))]
    )
    assert ids(shuffled) == ids(order), "order must not depend on input order"

    # Tie chain: more matches wins over fewer with more MMR-tied wins.
    chain = {
        "more_matches": {"mmr": 100, "matches_played": 5, "wins": 2},
        "more_wins": {"mmr": 100, "matches_played": 3, "wins": 3},
    }
    assert ids(leaderboard_order(list(chain.items()))) == [
        "more_matches",
        "more_wins",
    ], "matches played must break MMR ties before wins"

    # Perfect tie (equal MMR, matches, wins): player_id ascending decides.
    perfect = {
        "zz": {"mmr": 100, "matches_played": 2, "wins": 1},
        "aa": {"mmr": 100, "matches_played": 2, "wins": 1},
    }
    assert ids(leaderboard_order(list(perfect.items()))) == ["aa", "zz"]

    # Unplayed players never enter a ranking order (Leaderboard rank: none).
    unplayed = {
        "played": {"mmr": 300, "matches_played": 1, "wins": 1},
        "never": {"mmr": 999, "matches_played": 0, "wins": 0, "losses": 0},
    }
    assert ids(leaderboard_order(list(unplayed.items()))) == ["played"]

    # Non-MMR columns break ties by player_id ascending only (coins docs are
    # real DB shapes with played matches).
    coins = {
        "b": {"duck_coins": 4, "matches_played": 3, "wins": 1},
        "a": {"duck_coins": 4, "matches_played": 3, "wins": 1},
    }
    assert ids(leaderboard_order(list(coins.items()), "duck_coins")) == ["a", "b"]

    # avg_rating: unrated players sink below rated ones.
    rating = {
        "none": {"matches_played": 2},
        "has": {"matches_played": 2, "total_rating_points": 3.0, "total_rating_rounds": 2},
    }
    assert ids(leaderboard_order(list(rating.items()), "avg_rating")) == [
        "has",
        "none",
    ]

    # The leaderboard row-document key agrees with the pair key (same rule,
    # two shapes of input).
    doc_key = sort_key_for("mmr")
    doc = {"player_id": "luh4r", "mmr": 500, "matches_played": 10, "wins": 6}
    assert doc_key(doc) == leaderboard_key(("luh4r", doc), "mmr")
    pair_key = sort_key_for("mmr")
    assert pair_key({"player_id": "aa"}) < pair_key({"player_id": "zz"})

    # !stats position comes from the same order (shared position_of).
    stats_map = dict(tie)
    assert position_of(stats_map, "luh4r") == 2, position_of(stats_map, "luh4r")
    assert position_of(stats_map, "hugdung") == 3
    assert position_of(stats_map, "clear_above") == 1
    assert position_of(stats_map, "stranger") is None

    # --- report.py's rank snapshot uses the canonical order ---------------
    import commands.report as report_mod

    src = open(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "commands", "report.py")
    ).read()
    assert "leaderboard_order(" in src, "report.py must rank via the helper"
    assert 'key=lambda x: x[1].get("mmr", 0)' not in src, "inline sort copy must be gone"

    # --- sync_ranks (admin MMR edit) writes ranks in the canonical order ---
    _store.clear()
    bot = types.SimpleNamespace(
        player_mmr={
            "hugdung": {"mmr": 500, "matches_played": 10, "wins": 5},
            "luh4r": {"mmr": 500, "matches_played": 10, "wins": 6},
        }
    )
    mc.sync_ranks(bot)
    assert _store["luh4r"]["current_rank"] == 1, _store
    assert _store["hugdung"]["current_rank"] == 2, _store
    assert _store["luh4r"]["previous_rank"] == 1

    # --- _rebuild_ranks (manual match import) ditto, from DB documents ----
    _store.clear()
    docs = [
        {"player_id": "hugdung", "mmr": 500, "matches_played": 10, "wins": 5, "current_rank": 7},
        {"player_id": "luh4r", "mmr": 500, "matches_played": 10, "wins": 6, "current_rank": 7},
    ]
    mc.mmr_collection = types.SimpleNamespace(
        find=lambda *a, **k: docs,
        update_one=lambda q, u, upsert=False: _store.setdefault(
            q["player_id"], {}
        ).update(u.get("$set", {})),
        find_one=lambda *a, **k: None,
    )
    cmd = mc.MaintenanceCommands.__new__(mc.MaintenanceCommands)
    cmd._rebuild_ranks()
    assert _store["luh4r"]["current_rank"] == 1, _store
    assert _store["hugdung"]["current_rank"] == 2, _store
    assert _store["luh4r"]["previous_rank"] == 7, "previous rank preserved"

    # --- The leaderboard table's Rank column ascends down its own rows ----
    # The reported snippet: stored ranks 40/39 written opposite to display
    # order. With one helper for both, the ranks column is ascending by
    # construction: after a report rewrite, row i's rank equals i+1.
    rows = [
        {"player_id": "luh4r", "mmr": 500, "matches_played": 10, "wins": 6, "current_rank": 40},
        {"player_id": "hugdung", "mmr": 500, "matches_played": 10, "wins": 5, "current_rank": 39},
    ]
    ranked = leaderboard_order([(r["player_id"], r) for r in rows], "mmr")
    # The row-document render order must equal the pair order — one
    # canonical key (ascending form; no reverse for rendering either).
    doc_sorted = sorted(rows, key=sort_key_for("mmr"))
    assert [d["player_id"] for d in doc_sorted] == ids(ranked)

    print("all leaderboard tie-order self-checks passed")


if __name__ == "__main__":
    demo()