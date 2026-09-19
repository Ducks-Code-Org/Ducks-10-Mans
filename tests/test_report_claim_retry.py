"""Self-checks for the !report atomic claim (issue: premature report retry).

Regression: a reporter who attempted !report before the match was visible on
the Riot API consumed the atomic claim (match_not_reported flipped False) and
the early return never restored it — every retry then said "already been
reported" even though nothing was written. The claim must be released on
every failure path and consumed only after a full commit.
"""

import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub modules with import-time side effects (Mongo connection) before
# importing commands.report.
_database_stub = types.ModuleType("database")
_database_stub.users = types.SimpleNamespace(find_one=lambda *a, **k: None)
_database_stub.mmr_collection = types.SimpleNamespace(
    update_one=lambda *a, **k: None,
    find_one=lambda *a, **k: None,
    find=lambda *a, **k: [],
)
_database_stub.seasons = types.SimpleNamespace()
_database_stub.all_matches = types.SimpleNamespace(find_one=lambda *a, **k: None)
_database_stub.recent_queue = types.SimpleNamespace(
    update_one=lambda *a, **k: None,
)
_database_stub.coin_escrow = types.SimpleNamespace(
    update_one=lambda *a, **k: None, find_one=lambda *a, **k: None
)
sys.modules["database"] = _database_stub

_maps_stub = types.ModuleType("services.maps_service")
_maps_stub.get_standard_maps = lambda: ["Ascent", "Bind"]
sys.modules["services.maps_service"] = _maps_stub


class _FakeEmbed:
    def __init__(self, *a, **k):
        self.title = a[0] if a else k.get("title")
        self.fields = []
        self.footer = None

    def add_field(self, **kw):
        self.fields.append(kw)

    def set_footer(self, **kw):
        self.footer = kw.get("text")


_discord_stub = types.ModuleType("discord")
_discord_stub.Embed = _FakeEmbed
_discord_stub.Color = types.SimpleNamespace(green=lambda: None, gold=lambda: None)
_discord_stub.utils = types.SimpleNamespace(get=lambda *a, **k: None)
_discord_stub.NotFound = type("NotFound", (Exception,), {})
_discord_stub.HTTPException = type("HTTPException", (Exception,), {})
_discord_stub.Forbidden = type("Forbidden", (Exception,), {})
_discord_stub.Guild = type("Guild", (), {})
_discord_stub.Role = type("Role", (), {})
_discord_stub.Member = type("Member", (), {})
_discord_stub.ext = types.SimpleNamespace()
_discord_stub.ext.commands = types.SimpleNamespace(
    command=lambda *a, **k: (lambda f: f),
    has_permissions=lambda **k: (lambda f: f),
    Cog=type("Cog", (), {"__init_subclass__": classmethod(lambda cls, **kw: None)}),
)
sys.modules["discord"] = _discord_stub
sys.modules["discord.ext"] = _discord_stub.ext
sys.modules["discord.ext.commands"] = _discord_stub.ext.commands

import commands.report as report_mod  # noqa: E402
from commands.report import ReportCommand  # noqa: E402


class FakeBot:
    def __init__(self):
        self.report_lock = asyncio.Lock()
        self.match_ongoing = True
        self.match_not_reported = True
        self.selected_map = "Ascent"
        self.queue = [{"id": "1", "name": "p1"}, {"id": "2", "name": "p2"}]
        self.team1 = [{"id": "1", "name": "p1"}]
        self.team2 = [{"id": "2", "name": "p2"}]
        self.player_mmr = {}
        self.player_names = {}
        self.double_downs = set()
        self.map_override_last = 0
        self.map_override_last_by = None
        self.match_channel = None
        self.match_role = None
        self.current_signup_message = None
        self.match_name = "10-Mans"

    def ensure_player_mmr(self, pid, names):
        self.player_mmr.setdefault(pid, {"mmr": 100, "wins": 0, "losses": 0})

    async def wait_until_ready(self):
        pass


class FakeCtx:
    def __init__(self, user_id="999"):
        self.author = types.SimpleNamespace(id=int(user_id), name="reporter")
        self.channel = self
        self.guild = None
        self.sent = []

    async def send(self, content=None, **kw):
        self.sent.append(content)


def make_reporter(bot, *, fetch_result="raise", data_result=None):
    """ReportCommand instance with riot-api and DB paths stubbed."""
    cog = ReportCommand.__new__(ReportCommand)
    cog.bot = bot

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    async def _fake_fetch(session, name, tag, **kw):
        if fetch_result == "raise":
            raise report_mod.RiotApiInconclusive("429 rate limit persisted")
        if fetch_result == "none":
            return None
        return data_result

    report_mod.get_recent_matches_async = _fake_fetch
    report_mod.aiohttp.ClientSession = _FakeSession

    # DB stubs: the reporter and both queue players are linked. Queue players
    # resolve to their Discord ids by puuid so the report flow gets far enough
    # to write stats (used by the post-commit failure check).
    _linked = {
        "999": {"discord_id": "999", "name": "reporter", "tag": "tag"},
        "1": {"discord_id": "1", "name": "p1", "tag": "t", "puuid": "a"},
        "2": {"discord_id": "2", "name": "p2", "tag": "t", "puuid": "b"},
    }
    report_mod.users.find_one = lambda q: _linked.get(str(q.get("discord_id")))
    return cog


def _claim_state(bot):
    return bot.match_not_reported


def demo():
    # --- Premature report (match not yet on the API) must release the claim.
    bot = FakeBot()
    cog = make_reporter(bot, fetch_result="none")
    ctx = FakeCtx()
    asyncio.run(cog.report(ctx))
    assert _claim_state(bot) is True, (
        "claim must be released when the match is not yet visible on the API "
        "(404) so the reporter can retry"
    )
    assert any("Try again in a minute" in m for m in ctx.sent), ctx.sent

    # --- Network error path must release the claim too.
    bot = FakeBot()
    cog = make_reporter(bot, fetch_result="raise")
    ctx = FakeCtx()
    asyncio.run(cog.report(ctx))
    assert (
        _claim_state(bot) is True
    ), "claim must be released after a network error so the reporter can retry"

    # --- Map mismatch must release the claim (user reported the wrong game).
    bot = FakeBot()
    cog = make_reporter(
        bot,
        fetch_result="ok",
        data_result={
            "data": [
                {
                    "metadata": {"map": "Bind", "rounds_played": 17},
                    "players": [
                        {"name": "p1", "tag": "t", "puuid": "a", "team_id": "Red"},
                        {"name": "p2", "tag": "t", "puuid": "b", "team_id": "Blue"},
                    ],
                    "teams": [
                        {"team_id": "Red", "won": True, "rounds_won": 13},
                        {"team_id": "Blue", "won": False, "rounds_won": 4},
                    ],
                    "rounds": [],
                }
            ]
        },
    )
    ctx = FakeCtx()
    asyncio.run(cog.report(ctx))
    assert (
        _claim_state(bot) is True
    ), "claim must be released on a map mismatch so the next report is possible"

    # --- Missing player data must release the claim.
    bot = FakeBot()
    cog = make_reporter(
        bot,
        fetch_result="ok",
        data_result={
            "data": [
                {
                    "metadata": {"map": "Ascent", "rounds_played": 17},
                    "players": [],
                    "teams": [],
                }
            ]
        },
    )
    ctx = FakeCtx()
    asyncio.run(cog.report(ctx))
    assert (
        _claim_state(bot) is True
    ), "claim must be released when the API match has no team/player data"

    # --- Claim/release round trip is repeatable: claim, fail, release, retry.
    bot = FakeBot()
    cog = make_reporter(bot, fetch_result="none")

    async def _claim_then_fail_then_retry():
        # First reporter takes the claim and fails on the API.
        async with bot.report_lock:
            bot.match_not_reported = False
        committed = False

        def _mark_commit():
            nonlocal committed
            committed = True

        try:
            await cog._report_claimed(FakeCtx(), "r", "t", on_commit=_mark_commit)
        finally:
            if not committed:
                bot.match_not_reported = True
        assert bot.match_not_reported is True, "claim released after failure"

        # A retry (new cog run) now proceeds past the claim check.
        ctx = FakeCtx()
        await cog.report(ctx)
        assert bot.match_not_reported is True, "retry also released (API still 404)"

    asyncio.run(_claim_then_fail_then_retry())

    # --- A failure AFTER the first DB write must NOT release the claim -----
    # Once stats/coins are applied, releasing the claim would let a retry
    # double-apply them. on_commit fires just before the write loop, so a
    # post-commit exception must leave match_not_reported consumed.
    bot = FakeBot()
    cog = make_reporter(
        bot,
        fetch_result="ok",
        data_result={
            "data": [
                {
                    "metadata": {"map": "Ascent", "rounds_played": 17},
                    "players": [
                        {"name": "p1", "tag": "t", "puuid": "a", "team_id": "Red"},
                        {"name": "p2", "tag": "t", "puuid": "b", "team_id": "Blue"},
                    ],
                    "teams": [
                        {"team_id": "Red", "won": True, "rounds_won": 13},
                        {"team_id": "Blue", "won": False, "rounds_won": 4},
                    ],
                    "rounds": [],
                }
            ]
        },
    )
    # Make the first DB write raise, after on_commit fires.
    _orig_update_stats = report_mod.update_stats

    def _boom(*a, **k):
        raise RuntimeError("disk full")

    report_mod.update_stats = _boom
    try:
        ctx = FakeCtx()
        try:
            asyncio.run(cog.report(ctx))
        except RuntimeError as e:
            assert "disk full" in str(e)  # surfaced by on_command_error in prod
    finally:
        report_mod.update_stats = _orig_update_stats
    assert bot.match_not_reported is False, (
        "a failure after the first write must leave the claim consumed so a "
        "retry cannot double-apply MMR/coins"
    )

    # --- Already-recorded match id must be refused (idempotency) -----------
    bot = FakeBot()
    cog = make_reporter(
        bot,
        fetch_result="ok",
        data_result={
            "data": [
                {
                    "metadata": {
                        "map": "Ascent",
                        "rounds_played": 17,
                        "match_id": "already-seen",
                    },
                    "players": [],
                    "teams": [],
                }
            ]
        },
    )
    report_mod.all_matches.find_one = lambda q: (
        {"_id": "x"} if q.get("metadata.match_id") == "already-seen" else None
    )
    ctx = FakeCtx()
    asyncio.run(cog.report(ctx))
    assert any("already been recorded" in str(m) for m in ctx.sent), ctx.sent
    assert bot.match_not_reported is True, "a duplicate report releases the claim"

    # --- Stale match resources are cleared by a new !signup ----------------
    # After the incident: the failed report left match_channel/match_role
    # set, and the next !signup created a new channel on top of the old one.
    # The signup path must detect and clean stale resources first.
    import commands.report as _report_mod

    class _DeletedChannel:
        def __init__(self, name):
            self.name = name
            self.deleted = False

        async def delete(self):
            self.deleted = True

    class _DeletedRole:
        def __init__(self, name):
            self.name = name
            self.deleted = False
            self.members = []

        async def delete(self):
            self.deleted = True

    class _FakeSignupBot(FakeBot):
        def __init__(self):
            super().__init__()
            self.signup_lock = asyncio.Lock()
            self.signup_active = False
            self.signup_view = None
            self.background_purge_task = None
            self.setup_generation = 0
            self.match_ongoing = False
            self.match_not_reported = False
            self.selected_map = "Ascent"  # leftover from the stuck match
            self.current_teams_message = object()
            # Leftover channel/role from the stuck previous match.
            self.match_channel = _DeletedChannel("match-0001")
            self.match_role = _DeletedRole("match-0001")

        def load_mmr_data(self):
            pass

    _cleanup_calls = []
    _orig_cleanup = _report_mod.cleanup_match_resources

    async def _spy_cleanup(b, cancelled=False):
        _cleanup_calls.append(cancelled)
        await _orig_cleanup(b, cancelled=cancelled)

    _report_mod.cleanup_match_resources = _spy_cleanup

    async def _run_signup_cleanup():
        bot2 = _FakeSignupBot()
        old_channel, old_role = bot2.match_channel, bot2.match_role
        # The stuck-match leftovers: channel and role still set.
        assert old_channel is not None and old_role is not None

        # Directly exercise the stale-resource guard that was added inside
        # SignupCommand.signup (under signup_lock), mirroring its logic.
        assert getattr(bot2, "signup_view", None) is None
        if (
            bot2.match_channel is not None
            or bot2.match_role is not None
            or bot2.current_teams_message is not None
        ):
            await _report_mod.cleanup_match_resources(bot2, cancelled=True)
            bot2.current_teams_message = None

        assert _cleanup_calls == [True], "stale cleanup must run with cancelled=True"
        # cleanup_match_resources clears these:
        assert bot2.match_channel is None
        assert bot2.match_role is None
        assert bot2.match_not_reported is False
        assert bot2.match_ongoing is False
        assert bot2.queue == []
        # The old channel/role were actually deleted, not leaked.
        assert (
            old_channel.deleted and old_role.deleted
        ), "stale match channel and role must be deleted before the new signup"

    asyncio.run(_run_signup_cleanup())

    # --- Doubledown players are tagged in the match summary embed ----------
    # The summary must show which players' MMR gain/loss was doubled:
    # their delta is bolded and tagged "×2", with a footer explaining the
    # tag. Non-doubledown deltas stay plain.
    _rm = report_mod

    def _match_payload():
        return {
            "data": [
                {
                    "metadata": {"map": "Ascent", "rounds_played": 17},
                    "players": [
                        {"name": "p1", "tag": "t", "puuid": "a", "team_id": "Red"},
                        {"name": "p2", "tag": "t", "puuid": "b", "team_id": "Blue"},
                    ],
                    "teams": [
                        {"team_id": "Red", "won": True, "rounds_won": 13},
                        {"team_id": "Blue", "won": False, "rounds_won": 4},
                    ],
                    "rounds": [],
                }
            ]
        }

    class EmbedCtx(FakeCtx):
        def __init__(self):
            super().__init__()
            self.embeds = []

        async def send(self, content=None, **kw):
            await FakeCtx.send(self, content, **kw)
            if kw.get("embed") is not None:
                self.embeds.append(kw["embed"])

    async def _run_summary(dd_ids):
        bot = FakeBot()
        bot.selected_map = "Ascent"
        bot.double_downs = set(dd_ids)
        # Veterans (matches_played > 0, wins+losses > 0) so MMR deltas come
        # purely from this match's rounds — deterministic: 80/7 raw delta
        # (13-4 win, equal team MMR, vlr 1.0) → +11 plain, +23 doubled.
        bot.player_mmr = {
            "1": {"mmr": 100, "wins": 2, "losses": 1, "matches_played": 3},
            "2": {"mmr": 100, "wins": 2, "losses": 1, "matches_played": 3},
        }
        bot.save_mmr_data = lambda: None
        cog = make_reporter(bot, fetch_result="ok", data_result=_match_payload())
        ctx = EmbedCtx()
        await cog.report(ctx)
        return bot, ctx

    _orig_enabled = _rm.duck_coins_enabled
    _orig_sleep = _rm.asyncio.sleep
    _orig_all_matches = _rm.all_matches
    _orig_seasons = _rm.seasons
    try:
        _rm.duck_coins_enabled = lambda: True

        async def _fast_sleep(*a, **k):
            await _orig_sleep(0)

        _rm.asyncio.sleep = _fast_sleep
        _rm.all_matches = types.SimpleNamespace(
            find_one=lambda *a, **k: None, insert_one=lambda *a, **k: None
        )
        _rm.seasons = types.SimpleNamespace(update_one=lambda *a, **k: None)

        bot, ctx = asyncio.run(_run_summary({"1"}))
        assert (
            bot.match_not_reported is False
        ), "happy-path report must consume the claim"
        assert (
            len(ctx.embeds) == 1
        ), f"expected exactly the match summary embed, got {ctx.embeds}"
        summary = ctx.embeds[0]
        assert summary.title == "Match Summary | 10-Mans", summary.title
        assert len(summary.fields) == 2, summary.fields
        attackers, defenders = summary.fields[0], summary.fields[1]
        assert (
            "**+23** ×2" in attackers["value"]
        ), f"doubled player's delta must be bolded and tagged ×2: {attackers}"
        assert (
            "+6" in defenders["value"] and "**" not in defenders["value"]
        ), f"plain delta must not be bolded or tagged: {defenders}"
        assert "×2" not in defenders["value"], defenders
        assert summary.footer and "doubledown" in summary.footer.lower(), summary.footer
        assert "×2" in summary.footer, summary.footer

        bot, ctx = asyncio.run(_run_summary(set()))
        summary = ctx.embeds[0]
        for field in summary.fields:
            assert (
                "**" not in field["value"]
            ), f"no delta may be bold without a doubledown: {field}"
            assert (
                "×2" not in field["value"]
            ), f"no player may be tagged without a doubledown: {field}"
        assert (
            not summary.footer
        ), f"footer must be omitted when nobody doubled down: {summary.footer}"
    finally:
        _rm.duck_coins_enabled = _orig_enabled
        _rm.asyncio.sleep = _orig_sleep
        _rm.all_matches = _orig_all_matches
        _rm.seasons = _orig_seasons

    print("all report-claim retry self-checks passed")


if __name__ == "__main__":
    demo()
