import asyncio
import os
import sys
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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

# Fake coin_escrow collection backed by a dict: mirrors Mongo's upsert/$unset
# contract closely enough for the escrow-journal self-checks.
_ESCROW_DOCS: dict = {}


def _escrow_update_one(query, update, upsert=False):
    _id = query["_id"]
    if "$set" in update:
        _ESCROW_DOCS[_id] = {"_id": _id, **update["$set"]}
    elif "$unset" in update:
        doc = _ESCROW_DOCS.get(_id)
        if doc:
            for field in update["$unset"]:
                doc.pop(field, None)
    elif upsert and _id not in _ESCROW_DOCS:
        _ESCROW_DOCS[_id] = {"_id": _id}


_database_stub.coin_escrow = types.SimpleNamespace(
    update_one=_escrow_update_one,
    find_one=lambda query: _ESCROW_DOCS.get(query["_id"]),
)
sys.modules["database"] = _database_stub

_maps_stub = types.ModuleType("services.maps_service")
_maps_stub.get_standard_maps = lambda: ["Ascent", "Bind", "Haven", "Split"]
_maps_stub.get_competitive_maps = lambda: ["Ascent", "Bind"]
sys.modules["services.maps_service"] = _maps_stub


class _FakeEmbed:
    def __init__(self, *a, **k):
        self.title = k.get("title")
        self.description = k.get("description")
        self.fields = []
        self.footer = None

    def add_field(self, **k):
        self.fields.append(k)

    def set_footer(self, **k):
        self.footer = k


_discord_stub = types.ModuleType("discord")
_discord_stub.Embed = _FakeEmbed
_discord_stub.Color = types.SimpleNamespace(gold=lambda: None)


# Mirror discord.utils.get: iterate `iterable` and return the first item the
# `name`-matching predicate accepts (used to find channels by name).
def _fake_utils_get(iterable=None, **kw):
    wanted = kw.get("name")
    if iterable is None or wanted is None:
        return None
    for item in iterable:
        if getattr(item, "name", None) == wanted:
            return item
    return None


_discord_stub.utils = types.SimpleNamespace(get=_fake_utils_get)
_discord_stub.NotFound = type("NotFound", (Exception,), {})
_discord_stub.HTTPException = type("HTTPException", (Exception,), {})
sys.modules["discord"] = _discord_stub

import game.duck_coins as duck_coins
from game.duck_coins import (
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
        # Signup queue mirrors team1/team2 (the !signup auto-add fills it).
        # Player 7 stays OUT: it's the non-player used to assert that
        # !setmap/!doubledown reject non-players.
        self.queue = [{"id": "1", "name": "p1"}, {"id": "2", "name": "p2"}]
        self.match_ongoing = True
        self.double_downs = set()
        self.map_override_last = 0
        self.map_override_last_by = None
        # Live 2-minute powerup window by default: !doubledown and !setmap
        # share this deadline.
        self.map_override_deadline = time.monotonic() + 120
        self.chosen_mode = "Captains"
        self.selected_map = "Ascent"
        self.bet_session = None
        self.emojis = []
        self.match_channel = None
        self.map_override_chain = []


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
    # (A small losing pool means break-even nets here: 3-3=0, 1-1=0.)
    session["bets"]["attackers"]["5"] = 3
    session["bets"]["attackers"]["7"] = 1
    session["bets"]["defenders"]["6"] = 1
    embed = asyncio.run(duck_coins.settle_bets(bot, "attackers"))
    assert coins_of("5") == 6, f"bettor payout wrong: {coins_of('5')}"
    assert coins_of("7") == 1, f"second bettor payout wrong: {coins_of('7')}"
    assert coins_of("6") == 0, "losing side must not be paid"
    assert bot.bet_session is None
    # The summary embed must list winners AND losers with net results.
    assert embed is not None, "settlement must produce a summary embed"
    assert "Winners" in embed.fields[0]["name"], embed.fields
    assert "Lost" in embed.fields[1]["name"], embed.fields
    assert "<@5>" in embed.fields[0]["value"], embed.fields
    assert "<@7>" in embed.fields[0]["value"], embed.fields
    assert "<@6>" in embed.fields[1]["value"], embed.fields
    assert "bet 3" in embed.fields[0]["value"], embed.fields
    assert "(+0)" in embed.fields[0]["value"], embed.fields
    assert "lost 1" in embed.fields[1]["value"], embed.fields
    assert "Attackers won" in embed.title, embed.title

    # A bigger losing pool yields a real profit: winners pool 4, losers 6,
    # total 10 → payout 2x per coin (3*10//4=7, 1*10//4=2).
    DB["5"]["duck_coins"] = 0
    DB["7"]["duck_coins"] = 0
    session = open_window(bot)
    session["bets"]["attackers"]["5"] = 3
    session["bets"]["attackers"]["7"] = 1
    session["bets"]["defenders"]["6"] = 6
    embed = asyncio.run(duck_coins.settle_bets(bot, "attackers"))
    assert coins_of("5") == 7, f"2x payout wrong: {coins_of('5')}"
    assert coins_of("7") == 2, f"2x payout wrong: {coins_of('7')}"
    assert "(+4)" in embed.fields[0]["value"], embed.fields
    assert "(+1)" in embed.fields[0]["value"], embed.fields
    assert "lost 6" in embed.fields[1]["value"], embed.fields

    # Nobody bet at all: no embed, nothing to post.
    open_window(bot)
    embed = asyncio.run(duck_coins.settle_bets(bot, "attackers"))
    assert embed is None, "empty settlement must skip the summary"
    assert bot.bet_session is None

    # Refund on cancel returns escrow
    DB["5"]["duck_coins"] = 4  # running balance reset for this scenario
    session = open_window(bot)
    place_bet(bot, "5", "defenders", 2)
    refund_open_bets(bot)
    assert coins_of("5") == 4, f"escrow not refunded: {coins_of('5')}"
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
    # The doubledown window is the 2-minute powerup deadline (shared with
    # !setmap), not the 5-minute bet window.
    bot.bet_session = None
    bot.map_override_deadline = time.monotonic() - 1
    reply = doubledown(bot, "1")
    assert (
        "doubledown window has timed out" in reply
    ), "doubledown outside powerup window must report the timeout"
    bot.map_override_deadline = time.monotonic() + 120

    # Setmap override: validity, cost escalation, repeat-blocker
    # (override is allowed during the captains draft: match not yet ongoing)
    bot.selected_map = "Ascent"
    bot.map_override_last = 0
    bot.map_override_last_by = None
    bot.match_ongoing = False
    DB["1"]["duck_coins"] = 100
    reply = asyncio.run(setmap_override(bot, "1", "Bind"))
    assert "now **Bind**" in reply and coins_of("1") == 100 - SETMAP_BASE_COST
    assert bot.selected_map == "Bind"
    reply = asyncio.run(setmap_override(bot, "1", "Haven"))
    assert "another player" in reply, "same player can't override twice in a row"
    DB["2"]["duck_coins"] = 100
    reply = asyncio.run(setmap_override(bot, "2", "Haven"))
    assert "now **Haven**" in reply and coins_of("2") == 100 - (SETMAP_BASE_COST + 1)
    assert bot.map_override_last == SETMAP_BASE_COST + 1
    reply = asyncio.run(setmap_override(bot, "1", "Nuke"))
    assert "isn't in the All Maps pool" in reply
    # The map can't be "overridden" to itself — no charge, no state change.
    reply = asyncio.run(setmap_override(bot, "1", "Haven"))
    assert "already **Haven**" in reply, "same-map override must be rejected"
    assert coins_of("2") == 100 - (
        SETMAP_BASE_COST + 1
    ), "same-map override must not charge"
    assert bot.selected_map == "Haven", "same-map override must not change the map"
    bot.chosen_mode = "Balanced"
    bot.map_override_last = 0
    bot.map_override_last_by = None
    DB["1"]["duck_coins"] = 100
    reply = asyncio.run(setmap_override(bot, "1", "Bind"))
    # Issue #195: overrides now work in Balanced mode too.
    assert "now **Bind**" in reply, "override must work in balanced mode"
    bot.chosen_mode = "Weird"
    bot.map_override_last = 0
    bot.map_override_last_by = None
    DB["1"]["duck_coins"] = 100
    reply = asyncio.run(setmap_override(bot, "1", "Haven"))
    assert "Captains or Balanced" in reply, "override outside both modes must fail"
    bot.chosen_mode = "Captains"
    bot.match_ongoing = False
    bot.map_override_last = 0
    bot.map_override_last_by = None
    reply = asyncio.run(setmap_override(bot, "1", "Ascent"))
    assert "now **Ascent**" in reply

    # Non-players must not use !setmap — in ANY phase, checked before the
    # mode/window gates so the rejection is always the same.
    bot.map_override_last = 0
    bot.map_override_last_by = None
    DB["7"]["duck_coins"] = 100
    reply = asyncio.run(setmap_override(bot, "7", "Bind"))
    assert (
        "Only players in this match" in reply
    ), "non-player must not override during the draft window"
    assert coins_of("7") == 100, "non-player override must not charge"
    # Same rejection mid-match (grace window open or not).
    bot.match_ongoing = True
    bot.map_override_deadline = time.monotonic() + 120
    reply = asyncio.run(setmap_override(bot, "7", "Bind"))
    assert "Only players in this match" in reply, "non-player mid-match override"
    bot.match_ongoing = False
    bot.map_override_deadline = None

    # Once teams are decided (match ongoing) no more overrides — with no
    # grace window open (deadline cleared), overrides are rejected.
    bot.match_ongoing = True
    bot.map_override_deadline = None
    DB["1"]["duck_coins"] = 100
    reply = asyncio.run(setmap_override(bot, "1", "Haven"))
    assert "before the teams are fully decided" in reply
    assert coins_of("1") == 100, "override after draft end must not charge"
    # With a deadline but the window expired, the rejection names the timeout.
    bot.map_override_deadline = time.monotonic() - 1
    reply = asyncio.run(setmap_override(bot, "1", "Haven"))
    assert "override window has timed out" in reply, "expired grace must deny"
    assert coins_of("1") == 100, "timed-out override must not charge"
    bot.map_override_deadline = time.monotonic() + 120

    # Explicit-amount overrides (issue #195): min 3, must beat the last wager
    bot.match_ongoing = False
    bot.map_override_last = 4
    bot.map_override_last_by = "other"
    bot.map_override_chain = [{"payer": "other", "amount": 4}]
    DB["1"]["duck_coins"] = 100
    DB["other"] = {"player_id": "other", "duck_coins": 0}
    reply = asyncio.run(setmap_override(bot, "1", "Haven", 2))
    assert "minimum override wager is 3" in reply, "below-min wager must be denied"
    assert coins_of("1") == 100, "denied wager must not charge"
    reply = asyncio.run(setmap_override(bot, "1", "Haven", 4))
    assert "already wagered 4" in reply, "tie with last wager must be denied"
    reply = asyncio.run(setmap_override(bot, "1", "Haven", 5))
    assert "<@1> paid 5" in reply and bot.map_override_last == 5
    assert coins_of("1") == 95, "explicit wager must charge exactly that amount"
    # Issue #205: the outbid player gets their wager refunded on override.
    assert (
        coins_of("other") == 4
    ), f"outbid player must be refunded: {coins_of('other')}"
    assert "refunded to <@other>" in reply, reply
    # And the journal now holds only the standing wager.
    assert bot.map_override_chain == [{"payer": "1", "amount": 5}], (
        bot.map_override_chain
    )

    # Grace window after teams finalize: overrides still work for 2 minutes
    # and retitle the posted teams embed; they expire after the deadline.
    # (Deadlines use time.monotonic(), so no running loop is needed to set
    # them, but they are set inside a coroutine for parity with production.)
    class _FakeTeamsMessage:
        def __init__(self):
            self.embeds = [types.SimpleNamespace(title="Teams on Ascent")]
            self.edits = []

        async def edit(self, embed=None):
            self.edits.append(embed)

    async def _run_grace_checks():
        now = time.monotonic()
        bots = []
        for _ in range(3):
            b = FakeBot()
            b.match_ongoing = True
            b.map_override_deadline = now + 120
            b.map_override_last = 0
            b.map_override_last_by = None
            b.current_teams_message = _FakeTeamsMessage()
            b.emojis = []
            bots.append(b)
            DB["1"]["duck_coins"] = 100
        # Inside the window: charged, map set, embed retitled.
        reply = await setmap_override(bots[0], "1", "Bind")
        assert "now **Bind**" in reply, "grace-window override must be accepted"
        assert bots[0].selected_map == "Bind"
        assert bots[0].current_teams_message.edits, "teams embed must be edited"
        assert bots[0].current_teams_message.embeds[0].title == "Teams on Bind"
        assert coins_of("1") == 97, "grace-window override must charge"
        # Past the deadline: rejected without charge, naming the timeout.
        bots[1].map_override_deadline = now - 1
        reply = await setmap_override(bots[1], "1", "Haven")
        assert "override window has timed out" in reply, "expired grace must deny"
        assert not bots[1].current_teams_message.edits
        # Before teams finalize there is no teams embed yet and no edit attempt.
        bots[2].match_ongoing = False
        bots[2].map_override_deadline = None
        reply = await setmap_override(bots[2], "1", "Haven")
        assert "now **Haven**" in reply
        assert not bots[2].current_teams_message.edits, "no embed edit before finalize"

    asyncio.run(_run_grace_checks())

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

    # !setmap and !doubledown are match-channel-only powerups: the gate
    # compares the caller's channel against bot.match_channel. A fake
    # channel object works via its id attribute.
    class _FakeCh:
        def __init__(self, cid, name=None):
            self.id = cid
            # Real match channels are named after the match (match-####).
            self.name = name or f"chan-{cid}"

    bot.match_name = "match-0001"
    bot.match_channel = _FakeCh(1001, name="match-0001")
    match_rejection = command_available(
        bot, requires_running_match=False, channel=_FakeCh(2002)
    )
    assert (
        "match-0001" in match_rejection and "match channel" in match_rejection
    ), f"off-channel powerup must be rejected: {match_rejection}"
    assert (
        command_available(bot, requires_running_match=False, channel=bot.match_channel)
        is None
    ), "the match channel itself must pass"
    # No match channel (simulate run): nothing to enforce, gate passes.
    bot.match_channel = None
    assert (
        command_available(bot, requires_running_match=False, channel=_FakeCh(2002))
        is None
    ), "no match channel must not block"
    bot.match_channel = _FakeCh(1001)

    # Doubledown doubles the match delta only, never the first-match seed.
    # stats_helper is imported separately with its own stub in test_vlr_rating,
    # but here we verify the multiplier plumbing with a tiny fake.
    import game.stats_helper as _sh

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
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "commands",
            "coin_commands.py",
        )
    ).read()
    setmap_body = command_src.split("async def setmap_command")[1].split(
        "async def _gated_send"
    )[0]
    assert (
        "requires_running_match=False" in setmap_body
    ), "!setmap command must opt out of the running-match gate"
    assert (
        "channel=ctx.channel" in setmap_body
    ), "!setmap must pass its channel so the match-channel gate applies"
    # !bet must NOT pass a channel: spectators bet from #10-mans.
    bet_body = command_src.split("async def bet_attackers")[1].split(
        "@commands.command(name="
    )[0]
    assert (
        "channel=ctx.channel" not in bet_body
    ), "!bet must stay usable outside the match channel"

    # Season resets drop per-match coin state and zero every balance (see
    # test_maintenance_commands for the stat-defaults side of the reset).
    from game.duck_coins import clear_season_coin_state, reset_all_coins

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
    assert "data" not in _ESCROW_DOCS.get("open_bets", {}), (
        "escrow journal must be wiped on season reset so a later restart "
        "never refunds coins the reset already voided"
    )
    # clear_season_coin_state also clears the powerup deadline; restore it
    # for the crash-journal doubledown check below.
    bot.map_override_deadline = time.monotonic() + 120

    # --- Crash-safety journal: persist + startup recovery -----------------
    from game.duck_coins import recover_orphaned_escrow

    DB["9"] = {"player_id": "9", "duck_coins": 10}
    DB["8"] = {"player_id": "8", "duck_coins": 10}
    session = open_window(bot)
    place_bet(bot, "9", "attackers", 4)
    # Make player 8 a match player so the real doubledown() path applies.
    bot.team1.append({"id": "8", "name": "p8"})
    reply = doubledown(bot, "8")
    assert "doubled" in reply
    assert coins_of("8") == 5, "doubledown must charge before the crash"
    # The journal (written inside doubledown) must hold the escrow + dd.
    journal = _ESCROW_DOCS["open_bets"]["data"]
    assert journal["bets"]["attackers"]["9"] == 4, journal
    assert journal["double_downs"] == ["8"], journal

    # Simulated crash: nothing in memory, but the journal survived.
    bot.bet_session = None
    bot.double_downs = set()
    recover_orphaned_escrow(bot)
    assert coins_of("9") == 10, "bet escrow must be refunded on startup"
    assert coins_of("8") == 10, "doubledown must be refunded on startup"
    assert "data" not in _ESCROW_DOCS.get(
        "open_bets", {}
    ), "journal must be cleared after recovery"
    # Second recovery call is a no-op (no double refund).
    recover_orphaned_escrow(bot)
    assert coins_of("9") == 10 and coins_of("8") == 10, "no double refund"

    # Settlement clears the journal: a crash after !report must NOT refund.
    session = open_window(bot)
    place_bet(bot, "9", "attackers", 2)
    DB["9"]["duck_coins"] = 8
    embed = asyncio.run(duck_coins.settle_bets(bot, "attackers"))
    assert embed is not None, "a lone winner must still get a summary embed"
    assert coins_of("9") == 10, "settlement must pay the winning bettor"
    assert "data" not in _ESCROW_DOCS.get(
        "open_bets", {}
    ), "settlement must clear the journal"
    recover_orphaned_escrow(bot)
    assert coins_of("9") == 10, "settled bets must never be re-refunded"

    # --- Map-override journal and startup recovery (issue #205) -----------
    # (Reset the once-per-process gate so this simulates a fresh process.)
    duck_coins._escrow_recovered = False
    DB["2"] = {"player_id": "2", "duck_coins": 100}
    DB["6"] = {"player_id": "6", "duck_coins": 100}
    bot.bet_session = None
    bot.double_downs = set()
    bot.map_override_chain = []
    bot.map_override_last = 0
    bot.map_override_last_by = None
    bot.match_ongoing = False
    bot.selected_map = "Ascent"
    # Players 2 and 6 override in this section; both must be match players.
    bot.queue = [
        {"id": "1", "name": "p1"},
        {"id": "2", "name": "p2"},
        {"id": "6", "name": "p6"},
    ]
    asyncio.run(setmap_override(bot, "2", "Bind"))
    # Player 2 paid 3 (balance 97, chain holds their wager).
    assert coins_of("2") == 97 and bot.map_override_last == 3
    asyncio.run(setmap_override(bot, "6", "Haven"))
    # Issue #205: the outbid player 2 is refunded immediately (97 -> 100),
    # and player 6 outbids by escalation: pays last+1 = 4 (100 -> 96).
    assert coins_of("2") == 100, "outbid player must be refunded on override"
    assert coins_of("6") == 96, "outbidder pays last+1"
    assert bot.map_override_last == 4, "escalation follows the standing wager"
    asyncio.run(setmap_override(bot, "2", "Split"))
    # Player 6 is outbid and refunded (96 -> 100); player 2 pays 5.
    assert coins_of("2") == 95 and coins_of("6") == 100
    assert bot.map_override_last == 5
    bot.queue = [{"id": "1", "name": "p1"}, {"id": "2", "name": "p2"}]
    journal = _ESCROW_DOCS["open_bets"]["data"]
    assert journal["map_overrides"] == [
        {"payer": "2", "amount": 5},
    ], journal
    # Simulated crash: journal survives, memory does not.
    bot.map_override_chain = []
    bot.bet_session = None
    recover_orphaned_escrow(bot)
    assert (
        coins_of("2") == 100
    ), "the standing wager must be refunded on crash (and only it)"
    assert coins_of("6") == 100, "the outbid wager was already refunded live"

    # A gateway reconnect must not refund a LIVE window (process-gated).
    DB["9"]["duck_coins"] = 10
    duck_coins._escrow_recovered = False
    open_window(bot)
    place_bet(bot, "9", "attackers", 2)  # 10 -> 8, escrowed
    recover_orphaned_escrow(bot)  # the real process-start recovery
    assert coins_of("9") == 10, "first recovery refunds the journaled bet"
    session = open_window(bot)
    place_bet(bot, "9", "attackers", 2)  # a new live window: 10 -> 8
    recover_orphaned_escrow(bot)  # reconnect: on_ready fires again
    assert (
        coins_of("9") == 8
    ), "reconnect must not refund a live window (double payout risk)"

    # --- Post-setup announcements: powerup notice + betting embed ---------
    from game.duck_coins import _betting_embed, _powerups_announcement

    # The match-channel powerup notice must mention !setmap and !doubledown.
    text = _powerups_announcement(bot, 120)
    assert "!setmap" in text and "Override" in text, text
    assert "!doubledown" in text, text

    # The #10-mans betting embed must explain !bet and show both teams with
    # pools, expected payout multipliers, and the countdown.
    fake_session = {
        "open": True,
        "bets": {"attackers": {"9": 3}, "defenders": {"8": 2}},
        "message": None,
        "powerup_message": None,
        "task": None,
        "powerup_task": None,
        "ends_at": 0,
    }
    embed = _betting_embed(bot, fake_session, 300)
    assert "!bet attackers" in embed.description, embed.description
    assert "Attackers" in embed.fields[0]["name"], embed.fields
    assert "Defenders" in embed.fields[1]["name"], embed.fields
    assert any("pays" in f["value"] for f in embed.fields), embed.fields
    assert "4:60" not in embed.footer["text"] and "5:00" in embed.footer["text"]

    class _FakeFeatureGlobals:
        pass

    async def _run_announcement_routing():
        # bot.match_channel set: the powerup notice goes to the match channel;
        # with no guild, the betting embed falls back to ctx.channel.
        match_ch = FakeChannel()
        bot.match_channel = match_ch
        # ctx carries its own .channel in production; give the fake one.
        ctx = types.SimpleNamespace(channel=FakeChannel(), guild=None)
        await duck_coins.on_teams_announced(bot, ctx)
        assert (
            len(match_ch.messages) == 1
        ), "powerup notice must post once to the match channel"
        assert "!setmap" in match_ch.messages[0]
        assert (
            len(ctx.channel.messages) == 1
        ), "betting embed must fall back to ctx.channel without a guild"

        # When match_channel is unset too, both surfaces use ctx.channel
        # (simulate path).
        bot2 = FakeBot()
        bot2.match_channel = None
        ctx2 = types.SimpleNamespace(channel=FakeChannel(), guild=None)
        await duck_coins.on_teams_announced(bot2, ctx2)
        assert (
            len(ctx2.channel.messages) == 2
        ), "fallback must post powerup notice + betting embed"
        assert "!setmap" in ctx2.channel.messages[0]

    asyncio.run(_run_announcement_routing())

    # --- Cancel refunds EVERY coin spent on the match ----------------------
    from game.duck_coins import announce_cancellation_async, refund_match_coins

    DB["1"]["duck_coins"] = 20
    DB["2"]["duck_coins"] = 20
    DB["5"]["duck_coins"] = 20
    bot.current_teams_message = None
    session = open_window(bot)
    # Escrow directly into the session (the same place place_bet writes), so
    # this check is independent of any earlier balance churn in the demo.
    session["bets"]["attackers"]["5"] = 4
    DB["5"]["duck_coins"] = 16  # as if player 5 paid 4 for the bet
    bot.double_downs = {"1"}
    DB["1"]["duck_coins"] = 15  # as if player 1 paid 5 for the doubledown
    # Standing override wager (issue #205): the chain holds only the current
    # wager now — outbid wagers are refunded live at override time, so a
    # cancel refunds exactly the standing wager.
    bot.map_override_last = 5
    bot.map_override_last_by = "2"
    bot.map_override_chain = [{"payer": "2", "amount": 5}]
    DB["2"]["duck_coins"] = 15  # as if player 2 paid 5

    guild_captured = []

    async def _capturing_send(content=None, **kw):
        guild_captured.append(content)

    fake_10mans = types.SimpleNamespace(name="10-mans", send=_capturing_send)
    guild = types.SimpleNamespace(text_channels=[fake_10mans])

    total = refund_match_coins(bot)
    # 4 bet + 5 doubledown + 5 standing override wager = 14
    assert total == 14, f"total refund wrong: {total}"
    assert coins_of("5") == 20, f"bettor refund wrong: {coins_of('5')}"
    assert coins_of("1") == 20, f"doubledown refund wrong: {coins_of('1')}"
    assert coins_of("2") == 20, f"standing overrider refund wrong: {coins_of('2')}"
    assert bot.bet_session is None
    assert bot.double_downs == set()
    assert bot.map_override_last == 0 and bot.map_override_last_by is None
    assert bot.map_override_chain == [], "chain must be cleared after refund"

    # The #10-mans notice — only when coins were actually refunded.
    asyncio.run(announce_cancellation_async(bot, guild))
    assert len(guild_captured) == 1
    assert "Match cancelled" in guild_captured[0] and "returned" in guild_captured[0]

    # Nothing spent → nothing refunded → callers must NOT post the notice.
    DB["1"]["duck_coins"] = 50
    total2 = refund_match_coins(bot)
    assert total2 == 0, f"empty state must refund nothing: {total2}"
    guild_captured.clear()
    if total2:
        asyncio.run(announce_cancellation_async(bot, guild))
    assert guild_captured == [], "no coins refunded → no cancellation notice"

    # Guild without a #10-mans channel: notice skipped silently.
    asyncio.run(
        announce_cancellation_async(bot, types.SimpleNamespace(text_channels=[]))
    )

    # --- A played match must not leave a stale refund journal ---------------
    # !report settles bets and clears per-match state; a stale journal would
    # make the next restart refund coins for a match that actually happened.
    duck_coins._escrow_recovered = False
    DB["2"]["duck_coins"] = 10
    bot.map_override_chain = [{"payer": "2", "amount": 3}]
    duck_coins.persist_escrow(bot)
    bot.map_override_chain = []
    refund_open_bets(bot)  # what !report calls after settlement
    recover_orphaned_escrow(bot)
    assert (
        coins_of("2") == 10
    ), "a settled match must not refund overrides on the next restart"

    # Outbid wagers are refunded ONLY on cancel/crash, never just for losing
    # the outbid: refund_match_coins is the sole refund path and it is only
    # called from cancel/recovery (source contract).
    src = open(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "commands",
            "admin_commands.py",
        )
    ).read()
    assert "refund_match_coins(self.bot)" in src, "!cancel must refund on cancel"

    print("all duck coins self-checks passed")


if __name__ == "__main__":
    demo()
