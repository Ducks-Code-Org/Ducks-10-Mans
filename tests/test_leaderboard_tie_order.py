"""Self-checks for the deterministic leaderboard tie ordering (issue #256).

Regression: five "sort by MMR descending" copies ranked tied players by
incidental order (dict insertion / DB document order), so the board could
render rank 40 above rank 39 and different surfaces disagree. ONE canonical
order now lives in game/ranking.leaderboard_order and every ranking site
consumes it: board rows (the /leaderboard command and the persistent
startup post), the rank snapshot written after each match report, the
admin-edit rank resync, the manual-import rank rebuild, and the !stats
position lookup.

Tie chain (MMR): matches played desc → wins desc → player_id asc.
Every other column: player_id asc only. Unplayed players have no rank.
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- Stubs (Mongo / Discord) before importing bot modules -----------------
_data: dict = {}


def _stub_update_one(query, update, upsert=False):
    pid = query["player_id"]
    doc = _data.setdefault(pid, {"player_id": pid})
    if "$inc" in update:
        for k, v in update["$inc"].items():
            doc[k] = doc.get(k, 0) + v
    for k, v in update.get("$set", {}).items():
        doc[k] = v


_database_stub = types.ModuleType("database")
_database_stub.users = types.SimpleNamespace(
    find_one=lambda q: {"name": f"player-{q['discord_id']}", "tag": "t"}
)
_database_stub.mmr_collection = types.SimpleNamespace(
    find_one=lambda *a, **k: None,
    find=lambda *a, **k: list(_data.values()),
    update_one=_stub_update_one,
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
        self.title = k.get("title")
        self.description = k.get("description")
        self.fields = []

    def add_field(self, **k):
        self.fields.append(k)


_discord_stub = types.ModuleType("discord")
_discord_stub.Embed = _FakeEmbed
_discord_stub.Color = types.SimpleNamespace(gold=lambda: None, blurple=lambda: None)
_discord_stub.ButtonStyle = types.SimpleNamespace(blurple="blurple")
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
_discord_stub.app_commands = types.SimpleNamespace(
    describe=lambda **k: (lambda f: f), Attachment=object
)
_ui_stub = types.ModuleType("discord.ui")


class _FakeView:
    def __init__(self, timeout=None):
        pass

    def add_item(self, item):
        pass


class _FakeButton:
    def __init__(self, **k):
        self.kwargs = k


for _n in ("Modal", "Select"):
    setattr(
        _ui_stub,
        _n,
        type(_n, (object,), {"__init_subclass__": classmethod(lambda cls, **kw: None)}),
    )
_ui_stub.View = _FakeView
_ui_stub.Button = _FakeButton
for _n in ("select", "button"):
    setattr(_ui_stub, _n, lambda *a, **k: (lambda f: f))
_discord_stub.ui = _ui_stub
sys.modules["discord"] = _discord_stub
sys.modules["discord.ui"] = _ui_stub
sys.modules["discord.ext"] = _discord_stub.ext
sys.modules["discord.ext.commands"] = _discord_stub.ext.commands

import commands.maintenance_commands as mc  # noqa: E402
from commands.leaderboard import LeaderboardCommand  # noqa: E402
from game.ranking import leaderboard_order, matches_played, position_of  # noqa: E402


def ids(pairs):
    return [pid for pid, _ in pairs]


def rendered_pids(content: str) -> list[str]:
    """Player ids in the order they appear in a rendered leaderboard table."""
    marks = {
        pid: content.index(f"player-{pid}")
        for pid in _data
        if f"player-{pid}" in content
    }
    return sorted(marks, key=marks.get)


def demo():
    # A tied MMR pair renders/ranks in ONE order, whoever enters the store
    # (the exact #256 symptom: rows 40 above 39 among tied players).
    _data.update(
        {
            "hugdung": {
                "player_id": "hugdung",
                "mmr": 500,
                "matches_played": 10,
                "wins": 5,
            },
            "luh4r": {
                "player_id": "luh4r",
                "mmr": 500,
                "matches_played": 10,
                "wins": 6,
            },
            "clear_above": {
                "player_id": "clear_above",
                "mmr": 600,
                "matches_played": 3,
                "wins": 2,
            },
            "never": {"player_id": "never", "mmr": 999, "matches_played": 0, "wins": 0},
        }
    )
    # The /leaderboard command AND the persistent startup post both render
    # through this path: rows must come out in canonical rank order.
    _, content, error = LeaderboardCommand.generate_leaderboard(None, None, "mmr")
    assert error is None, error
    assert rendered_pids(content) == ["clear_above", "luh4r", "hugdung"], content

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

    # `matches_played` falls back to wins+losses for legacy docs.
    assert matches_played({"mmr": 100, "wins": 3, "losses": 2}) == 5

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
        "has": {
            "matches_played": 2,
            "total_rating_points": 3.0,
            "total_rating_rounds": 2,
        },
    }
    assert ids(leaderboard_order(list(rating.items()), "avg_rating")) == [
        "has",
        "none",
    ]

    # !stats position comes from the same order (shared position_of).
    stats_map = {
        "hugdung": {"mmr": 500, "matches_played": 10, "wins": 5},
        "luh4r": {"mmr": 500, "matches_played": 10, "wins": 6},
        "clear_above": {"mmr": 600, "matches_played": 3, "wins": 2},
    }
    assert position_of(stats_map, "luh4r") == 2, position_of(stats_map, "luh4r")
    assert position_of(stats_map, "hugdung") == 3
    assert position_of(stats_map, "clear_above") == 1
    assert position_of(stats_map, "stranger") is None

    # --- The reported snippet: stored ranks 40/39 opposite to display -----
    # With one helper for both, the Rank column is ascending down the table.
    # Ranks rewritten by sync_ranks must match the row order the board
    # renders from the same store.
    _data.clear()
    _data.update(
        {
            "hugdung": {
                "player_id": "hugdung",
                "mmr": 500,
                "matches_played": 10,
                "wins": 5,
                "current_rank": 39,
            },
            "luh4r": {
                "player_id": "luh4r",
                "mmr": 500,
                "matches_played": 10,
                "wins": 6,
                "current_rank": 40,
            },
        }
    )
    mc.sync_ranks(types.SimpleNamespace(player_mmr=_data))
    assert _data["luh4r"]["current_rank"] == 1, _data
    assert _data["hugdung"]["current_rank"] == 2, _data
    assert _data["hugdung"]["previous_rank"] == 2, "previous rank mirrors sync"
    _, content, _ = LeaderboardCommand.generate_leaderboard(None, None, "mmr")
    assert rendered_pids(content) == ["luh4r", "hugdung"], content
    # Rank column strictly ascending down the table (the reported symptom).
    ranks_in_order = [
        int(line.split("┃")[1].strip())
        for line in content.splitlines()
        if line.startswith("┃") and line.split("┃")[1].strip().isdigit()
    ]
    assert ranks_in_order == [1, 2], ranks_in_order

    # --- _rebuild_ranks (manual match import) ditto, from DB documents ----
    _data.clear()
    _data.update(
        {
            "hugdung": {
                "player_id": "hugdung",
                "mmr": 500,
                "matches_played": 10,
                "wins": 5,
                "current_rank": 7,
            },
            "luh4r": {
                "player_id": "luh4r",
                "mmr": 500,
                "matches_played": 10,
                "wins": 6,
                "current_rank": 7,
            },
        }
    )
    cmd = mc.MaintenanceCommands.__new__(mc.MaintenanceCommands)
    cmd._rebuild_ranks()
    assert _data["luh4r"]["current_rank"] == 1, _data
    assert _data["hugdung"]["current_rank"] == 2, _data
    assert _data["luh4r"]["previous_rank"] == 7, "previous rank preserved"

    print("all leaderboard tie-order self-checks passed")


if __name__ == "__main__":
    demo()
