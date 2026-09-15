import asyncio
import os
import sys
import types

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

# Stub modules with import-time side effects (Mongo connection) so this
# self-check can run without a database.
_database_stub = types.ModuleType("database")
_database_stub.users = types.SimpleNamespace(find_one=lambda *a, **k: None)
_database_stub.mmr_collection = types.SimpleNamespace(
    update_one=lambda *a, **k: None,
    find_one=lambda *a, **k: None,
    find=lambda *a, **k: [],
)
_database_stub.seasons = types.SimpleNamespace()
_database_stub.all_matches = types.SimpleNamespace()
_database_stub.recent_queue = types.SimpleNamespace()
sys.modules["database"] = _database_stub

_maps_stub = types.ModuleType("maps_service")
_maps_stub.get_standard_maps = lambda: ["Ascent", "Bind", "Haven", "Split"]
_maps_stub.get_competitive_maps = lambda: ["Ascent", "Bind"]
sys.modules["maps_service"] = _maps_stub


class _FakeEmbed:
    def __init__(self, *a, **k):
        pass


_discord_stub = types.ModuleType("discord")
_discord_stub.Embed = _FakeEmbed
_discord_stub.Color = types.SimpleNamespace(gold=lambda: None)
_discord_stub.utils = types.SimpleNamespace(get=lambda *a, **k: None)
_discord_stub.NotFound = type("NotFound", (Exception,), {})
_discord_stub.HTTPException = type("HTTPException", (Exception,), {})
sys.modules["discord"] = _discord_stub

import duck_coins
from duck_coins import (
    DOUBLEDOWN_COST,
    SETMAP_BASE_COST,
    coins_of,
    command_available,
    doubledown,
    doubledown_multiplier_of,
    duck_emote,
    place_bet,
    refund_open_bets,
    setmap_override,
)

# --- Fake mmr_collection backed by a dict -------------------------------
DB: dict = {}


def _find_one(q):
    return DB.get(q["player_id"])


class _Res:
    modified_count = 1


class FakeCollection:
    def find_one(self, q, *a, **k):
        return _find_one(q)

    def update_one(self, q, update, upsert=False):
        pid = q["player_id"]
        doc = DB.setdefault(pid, {"player_id": pid})
        if "$inc" in update:
            for key, val in update["$inc"].items():
                doc[key] = doc.get(key, 0) + val
        for key, val in update.get("$set", {}).items():
            if key != "player_id" or pid in DB:
                doc[key] = val

    def find(self, *a, **k):
        return list(DB.values())

    def update_many(self, q, update):
        for doc in DB.values():
            doc.update(update.get("$set", {}))


duck_coins.mmr_collection = FakeCollection()

# --- Fake bot / ctx ------------------------------------------------------


class FakeBot:
    def __init__(self):
        self.team1 = [{"id": "1", "name": "p1"}]
        self.team2 = [{"id": "2", "name": "p2"}]
        self.match_ongoing = True
        self.double_downs = set()
        self.map_override_last = 0
        self.map_override_last_by = None
        self.chosen_mode = "Captains"
        self.selected_map = "Ascent"
        self.bet_session = None
        self.emojis = []


class FakeChannel:
    def __init__(self):
        self.messages = []

    async def send(self, content=None, **kwargs):
        self.messages.append(content if content else str(kwargs))


def open_window(bot):
    bot.bet_session = {
        "open": True,
        "bets": {"attackers": {}, "defenders": {}},
        "message": None,
        "task": None,
    }
    return bot.bet_session


def demo():
    bot = FakeBot()
    assert duck_emote(bot) == "🦆", "fallback emote missing"

    # +1 coin per match played
    duck_coins.award_match_coins(["1", "2"])
    assert coins_of("1") == 1 and coins_of("2") == 1
    duck_coins.award_match_coins(["1"])
    assert coins_of("1") == 2

    # Betting: escrow, invalid side, player exclusion, min bet, low balance
    DB["5"] = {"player_id": "5", "duck_coins": 5}
    DB["7"] = {"player_id": "7", "duck_coins": 0}
    session = open_window(bot)
    reply = place_bet(bot, "5", "attackers", 2)
    assert "Bet placed" in reply and coins_of("5") == 3
    reply = place_bet(bot, "6", "attackers", 5)
    assert "need 5" in reply, "insufficient message must show balance and cost"
    assert "have 0" in reply
    reply = place_bet(bot, "5", "middle", 1)
    assert "Pick a side" in reply
    reply = place_bet(bot, "1", "attackers", 1)
    assert "can't bet" in reply, "match player must not bet"
    reply = place_bet(bot, "5", "defenders", 0)
    assert "Minimum" in reply
    reply = place_bet(bot, "5", "attackers", 0)
    assert "Minimum" in reply

    # Parimutuel payout: attackers pool 4 (3+1) vs defenders 1 → total 5.
    # Payouts: 3*5//4=3 for "5", 1*5//4=1 for "7"; dust stays in the pool.
    session["bets"]["attackers"]["5"] = 3
    session["bets"]["attackers"]["7"] = 1
    session["bets"]["defenders"]["6"] = 1
    bot.channel = FakeChannel()
    asyncio.run(duck_coins.settle_bets(bot, bot.channel, "attackers"))
    assert coins_of("5") == 6, f"bettor payout wrong: {coins_of('5')}"
    assert coins_of("7") == 1, f"second bettor payout wrong: {coins_of('7')}"
    assert coins_of("6") == 0, "losing side must not be paid"
    assert bot.bet_session is None

    # Refund on cancel returns escrow
    session = open_window(bot)
    place_bet(bot, "5", "defenders", 2)
    refund_open_bets(bot)
    assert coins_of("5") == 6, f"escrow not refunded: {coins_of('5')}"
    assert bot.bet_session is None

    # Doubledown: cost, window gate, duplicate gate, non-player gate
    DB["1"]["duck_coins"] = DOUBLEDOWN_COST
    bot.double_downs = set()
    open_window(bot)
    reply = doubledown(bot, "1")
    assert "doubled" in reply and coins_of("1") == 0
    assert doubledown_multiplier_of(bot, "1") == 2
    reply = doubledown(bot, "1")
    assert "already" in reply
    DB["5"]["duck_coins"] = 99
    reply = doubledown(bot, "5")
    assert "Only players" in reply, "non-player must not double down"
    DB["1"]["duck_coins"] = 3
    bot.double_downs = set()
    reply = doubledown(bot, "1")
    assert "have 3" in reply and "need 5" in reply, "doubledown insufficient message"
    bot.bet_session["open"] = False
    reply = doubledown(bot, "1")
    assert "5 minutes" in reply, "doubledown outside window must be rejected"

    # Setmap override: validity, cost escalation, repeat-blocker
    # (override is allowed during the captains draft: match not yet ongoing)
    bot.selected_map = "Ascent"
    bot.map_override_last = 0
    bot.map_override_last_by = None
    bot.match_ongoing = False
    DB["1"]["duck_coins"] = 100
    reply = setmap_override(bot, "1", "Bind")
    assert "now **Bind**" in reply and coins_of("1") == 100 - SETMAP_BASE_COST
    assert bot.selected_map == "Bind"
    reply = setmap_override(bot, "1", "Haven")
    assert "another player" in reply, "same player can't override twice in a row"
    DB["2"]["duck_coins"] = 100
    reply = setmap_override(bot, "2", "Haven")
    assert "now **Haven**" in reply and coins_of("2") == 100 - (SETMAP_BASE_COST + 1)
    assert bot.map_override_last == SETMAP_BASE_COST + 1
    reply = setmap_override(bot, "1", "Nuke")
    assert "isn't in the All Maps pool" in reply
    bot.chosen_mode = "Balanced"
    bot.map_override_last = 0
    bot.map_override_last_by = None
    DB["1"]["duck_coins"] = 100
    reply = setmap_override(bot, "1", "Bind")
    assert "Captains mode" in reply, "override outside captains mode must fail"
    bot.chosen_mode = "Captains"
    bot.match_ongoing = False
    bot.map_override_last = 0
    bot.map_override_last_by = None
    reply = setmap_override(bot, "1", "Ascent")
    assert "now **Ascent**" in reply

    # Once teams are decided (match ongoing) no more overrides
    bot.match_ongoing = True
    DB["1"]["duck_coins"] = 100
    reply = setmap_override(bot, "1", "Haven")
    assert "before the teams are fully decided" in reply
    assert coins_of("1") == 100, "override after draft end must not charge"

    # Command gating: the !setmap window (captains draft) is exactly when no
    # match is running, so its gate must not require a running match.
    import globals as _globals

    class _Features(dict):
        def getboolean(self, name, *a, **k):
            return True

    _globals.BOT_FEATURES = _Features(duck_coins="true")
    bot.match_ongoing = False
    assert (
        command_available(bot, requires_running_match=False) is None
    ), "!setmap must be reachable during the draft window"
    assert (
        command_available(bot) is not None
    ), "!bet and !doubledown must require a running match"
    assert command_available(bot) == "No match is running right now."
    bot.match_ongoing = True
    assert command_available(bot) is None

    # Doubledown doubles the match delta only, never the first-match seed.
    # stats_helper is imported separately with its own stub in test_vlr_rating,
    # but here we verify the multiplier plumbing with a tiny fake.
    import stats_helper as _sh

    store = {}
    _sh.update_stats(
        {"stats": {"score": 900, "kills": 8, "deaths": 4}},
        20,
        store,
        {},
        discord_id="8",
        team_avg_mmr=400,
        opp_avg_mmr=400,
        our_rounds=13,
        opp_rounds=5,
        rating=1.4,
        mmr_multiplier=1,
    )
    plain = store["8"]["mmr"]
    store2 = {}
    _sh.update_stats(
        {"stats": {"score": 900, "kills": 8, "deaths": 4}},
        20,
        store2,
        {},
        discord_id="9",
        team_avg_mmr=400,
        opp_avg_mmr=400,
        our_rounds=13,
        opp_rounds=5,
        rating=1.4,
        mmr_multiplier=2,
    )
    doubled = store2["9"]["mmr"]
    seed = 140  # 100 * 1.4
    assert plain > seed, "expected a positive first-match delta"
    assert (
        abs((doubled - seed) - 2 * (plain - seed)) <= 1
    ), f"doubledown must double the delta only: plain={plain} doubled={doubled}"

    # The !setmap call site must pass requires_running_match=False, otherwise
    # the gate and the override window stay mutually exclusive.
    command_src = open(
        os.path.join(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ),
            "commands",
            "duck_commands.py",
        )
    ).read()
    setmap_body = command_src.split("async def setmap_command")[1].split(
        "async def _gated_send"
    )[0]
    assert (
        "requires_running_match=False" in setmap_body
    ), "!setmap command must opt out of the running-match gate"

    # Season resets drop per-match coin state and zero every balance (see
    # test_maintenance_commands for the stat-defaults side of the reset).
    from duck_coins import clear_season_coin_state, reset_all_coins

    DB["1"] = {"player_id": "1", "duck_coins": 42}
    DB["2"] = {"player_id": "2", "duck_coins": 7}
    reset_all_coins()
    assert coins_of("1") == 0 and coins_of("2") == 0, "reset must zero all coins"

    bot.bet_session = None
    open_window(bot)
    bot.double_downs = {"1", "2"}
    bot.map_override_last = 7
    bot.map_override_last_by = "1"
    clear_season_coin_state(bot)
    assert bot.bet_session is None, "open bet session must be dropped on reset"
    assert bot.double_downs == set(), "doubledowns must be cleared on reset"
    assert (
        bot.map_override_last == 0 and bot.map_override_last_by is None
    ), "map-override escalation must reset"

    print("all duck coins self-checks passed")


if __name__ == "__main__":
    demo()
