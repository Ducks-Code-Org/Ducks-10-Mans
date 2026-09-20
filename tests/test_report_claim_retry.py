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
_maps_stub.get_competitive_maps = lambda: ["Ascent", "Bind"]
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
_discord_stub.Colour = types.SimpleNamespace(from_str=lambda css: css)


def _fake_utils_get(iterable=None, **kw):
    wanted = kw.get("name")
    if iterable is None or wanted is None:
        return None
    for item in iterable:
        if getattr(item, "name", None) == wanted:
            return item
    return None


_discord_stub.utils = types.SimpleNamespace(get=_fake_utils_get)
# Mirror discord.py: NotFound/Forbidden subclass HTTPException, so callers
# that catch HTTPException also catch them.
_discord_stub.HTTPException = type("HTTPException", (Exception,), {})
_discord_stub.NotFound = type("NotFound", (_discord_stub.HTTPException,), {})
_discord_stub.Forbidden = type("Forbidden", (_discord_stub.HTTPException,), {})
_discord_stub.Guild = type("Guild", (), {})
_discord_stub.Role = type("Role", (), {})
_discord_stub.Member = type("Member", (), {})
_discord_stub.Interaction = type("Interaction", (), {})
_discord_stub.ui = types.SimpleNamespace(
    View=type("View", (), {"__init__": lambda self, **kw: None}),
    Button=type("Button", (), {}),
    Select=type("Select", (), {}),
)
sys.modules["discord.ui"] = _discord_stub.ui
_discord_stub.ext = types.SimpleNamespace()
_discord_stub.ext.commands = types.SimpleNamespace(
    command=lambda *a, **k: (lambda f: f),
    hybrid_command=lambda *a, **k: (lambda f: f),
    has_permissions=lambda **k: (lambda f: f),
    Cog=type("Cog", (), {"__init_subclass__": classmethod(lambda cls, **kw: None)}),
)
sys.modules["discord"] = _discord_stub
sys.modules["discord.ext"] = _discord_stub.ext
sys.modules["discord.ext.commands"] = _discord_stub.ext.commands
_app_stub = types.SimpleNamespace(
    describe=lambda **k: (lambda f: f),
    Attachment=object,
)
_discord_stub.app_commands = _app_stub
sys.modules["discord.app_commands"] = _app_stub

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


class FakeRole:
    def __init__(self, name, rid):
        self.name = name
        self.id = rid
        self.mention = f"<@&{rid}>"


class FakeGuild:
    """Guild with the rank roles the summary should mention by name."""

    def __init__(self):
        self.id = 4242
        self.name = "test-guild"
        self.text_channels = []
        self.roles = [
            FakeRole(name, 1000 + i)
            for i, name in enumerate(
                ["Wood Rank", "Stone Rank", "Iron Rank", "Gold Rank", "Season-1"]
            )
        ]

    def get_member(self, uid):
        return None

    async def fetch_member(self, uid):
        # Mirror production: a member not in the guild raises NotFound, which
        # grant_season_roles catches and skips — irrelevant to the summary
        # embed under test.
        raise _discord_stub.NotFound()

    def get_channel(self, cid):
        return None

    async def create_role(self, name, **kwargs):
        role = FakeRole(name, 9000 + len(self.roles))
        self.roles.append(role)
        return role


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

    # --- Stale cleanup must also drop the stale signup message and view ----
    # A prior signup's message/view outlive their deleted match channel:
    # buttons that still answer clicks there produce 10003 Unknown Channel
    # followups, and the notification failure then aborted match setup
    # (issue #216). Cleanup must delete the stale message and stop the view.
    class _DeletedMessage:
        def __init__(self):
            self.deleted = False

        async def delete(self):
            self.deleted = True

    class _FakeStaleView:
        def __init__(self):
            self.cleaned = False

        def cleanup(self):
            self.cleaned = True

    async def _run_stale_signup_message_cleanup():
        bot3 = _FakeSignupBot()
        old_message = _DeletedMessage()
        old_view = _FakeStaleView()
        bot3.match_channel = _DeletedChannel("match-0001")
        bot3.match_role = _DeletedRole("match-0001")
        bot3.current_signup_message = old_message
        bot3.signup_view = old_view

        await _report_mod.cleanup_match_resources(bot3, cancelled=True)

        assert old_message.deleted, (
            "the stale signup message must be deleted with the stale match "
            "channel, or its live buttons keep answering clicks against a "
            "deleted channel (10003 Unknown Channel)"
        )
        assert bot3.current_signup_message is None
        assert old_view.cleaned, "the stale signup view's tasks must be stopped"
        assert bot3.signup_view is None

    asyncio.run(_run_stale_signup_message_cleanup())

    # --- A failed signup notification must never strand a full queue -------
    # Issue #216: the "added to the queue" followup raised (10003 Unknown
    # Channel) and aborted finalize, so a 10/10 queue never reached match
    # setup. The notification is best-effort; the signup pipeline continues.
    class _NotifyFailBot:
        def __init__(self):
            self.setup_generation = 1
            self.queue = [{"id": str(i), "name": f"p{i}"} for i in range(9)]
            self.player_mmr = {}
            self.player_names = {}
            self.match_name = "match-0001"
            self.current_signup_message = types.SimpleNamespace(
                edit=lambda **kw: asyncio.sleep(0)
            )
            self.team1 = []
            self.team2 = []
            self.match_channel = types.SimpleNamespace(
                send=lambda *a, **kw: asyncio.sleep(0)
            )
            self.match_ongoing = False

        def ensure_player_mmr(self, *a, **k):
            pass

    class _RaisingNotify:
        def __init__(self):
            self.failed = 0

        async def __call__(self, msg):
            self.failed += 1
            raise _discord_stub.HTTPException(
                "400 Bad Request (10003): Unknown Channel"
            )

    async def _run_notify_failure_finalize():
        from views.signup_view import SignupView

        bot4 = _NotifyFailBot()
        ctx = types.SimpleNamespace(
            guild=None,
            channel=types.SimpleNamespace(send=lambda *a, **kw: asyncio.sleep(0)),
        )
        view = SignupView.__new__(SignupView)
        view.ctx = ctx
        view.bot = bot4
        view.setup_generation = bot4.setup_generation
        view.last_activity_time = 0
        view.sign_up_button = types.SimpleNamespace(label="")
        view.children = []
        # finalize_signup is the observable "match setup started" marker.
        finalized = []

        async def _fake_finalize(channel):
            finalized.append(channel)

        view.finalize_signup = _fake_finalize

        notify = _RaisingNotify()
        result = await view.signup_player(
            "9",
            "p9",
            notify=notify,
            channel=ctx.channel,
            verified_user={"discord_id": "9", "name": "p9", "tag": "t"},
        )

        assert result is True, "the signup must still succeed"
        assert len(bot4.queue) == 10, "the player must still be queued"
        assert notify.failed == 1, "the failed notification must have run"
        assert finalized, "a failed notification must not abort the queue-full handoff"

    asyncio.run(_run_notify_failure_finalize())

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
            # A guild with the rank roles, so the summary can mention them.
            self.guild = FakeGuild()

        async def send(self, content=None, **kw):
            await FakeCtx.send(self, content, **kw)
            if kw.get("embed") is not None:
                self.embeds.append(kw["embed"])

    async def _run_summary(dd_ids):
        bot = FakeBot()
        bot.selected_map = "Ascent"
        bot.double_downs = set(dd_ids)
        # Veterans (matches_played > 0, wins+losses > 0) so MMR deltas come
        # purely from this match's rounds — deterministic: 13-4 win, equal
        # team MMR, vlr 1.0 → Δ = 10 + 60/7 ≈ +18.57 → +19 plain, +37 doubled.
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
        # find_one feeds grant_season_roles; the FakeGuild ctx now reaches it.
        _rm.seasons = types.SimpleNamespace(
            update_one=lambda *a, **k: None,
            find_one=lambda *a, **k: {"season_number": 1},
        )

        bot, ctx = asyncio.run(_run_summary({"1"}))
        assert (
            bot.match_not_reported is False
        ), "happy-path report must consume the claim"
        assert (
            len(ctx.embeds) == 1
        ), f"expected exactly the match summary embed, got {ctx.embeds}"
        summary = ctx.embeds[0]
        assert summary.title == "Match Summary | 10-Mans", summary.title
        assert len(summary.fields) == 3, summary.fields
        attackers, defenders = summary.fields[0], summary.fields[1]
        assert (
            "**+37** ×2" in attackers["value"]
        ), f"doubled player's delta must be bolded and tagged ×2: {attackers}"
        assert (
            "-10" in defenders["value"] and "**" not in defenders["value"]
        ), f"plain delta must not be bolded or tagged: {defenders}"
        assert "×2" not in defenders["value"], defenders
        assert summary.footer and "doubledown" in summary.footer.lower(), summary.footer
        assert "×2" in summary.footer, summary.footer
        # Issue #204: the -10 delta drops player 2 from Stone (100) to Wood
        # (<100), so the optional rank-changes section appears.
        rank_field = summary.fields[2]
        assert rank_field["name"] == "🏅 Rank Changes", rank_field
        assert "⬇️" in rank_field["value"] and "<@2>" in rank_field["value"], rank_field
        # Ranks render as live role mentions (<@&id>), not plain text.
        assert "<@&1000>" in rank_field["value"], rank_field  # Wood Rank
        assert "<@&1001>" in rank_field["value"], rank_field  # Stone Rank
        assert "Rank**" not in rank_field["value"], rank_field

        # Same match, but both players start at MMR 150 (Stone, stay Stone):
        # +37 keeps player 1 in Stone, -10 keeps player 2 in Stone → no
        # rank-changes field at all.
        async def _run_no_tier_change():
            bot = FakeBot()
            bot.selected_map = "Ascent"
            bot.double_downs = set()
            bot.player_mmr = {
                "1": {"mmr": 150, "wins": 2, "losses": 1, "matches_played": 3},
                "2": {"mmr": 150, "wins": 2, "losses": 1, "matches_played": 3},
            }
            bot.save_mmr_data = lambda: None
            cog = make_reporter(bot, fetch_result="ok", data_result=_match_payload())
            ctx = EmbedCtx()
            await cog.report(ctx)
            return ctx

        ctx = asyncio.run(_run_no_tier_change())
        summary = ctx.embeds[0]
        assert all(
            f["name"] != "🏅 Rank Changes" for f in summary.fields
        ), f"no rank field when nobody changed tier: {summary.fields}"

        # Issue #204: an unranked (0 matches) player's first match ranks
        # them — the section shows the unranked → tier promotion.
        async def _run_first_match_rankup():
            bot = FakeBot()
            bot.selected_map = "Ascent"
            bot.double_downs = set()
            bot.player_mmr = {
                "1": {"mmr": 100, "wins": 2, "losses": 1, "matches_played": 3},
            }
            bot.save_mmr_data = lambda: None
            cog = make_reporter(bot, fetch_result="ok", data_result=_match_payload())
            ctx = EmbedCtx()
            await cog.report(ctx)
            return ctx

        ctx = asyncio.run(_run_first_match_rankup())
        summary = ctx.embeds[0]
        rank_field = next(
            (f for f in summary.fields if f["name"] == "🏅 Rank Changes"), None
        )
        assert (
            rank_field is not None
        ), f"first-match player must show a rankup: {summary.fields}"
        assert (
            "now ranked" in rank_field["value"] and "<@2>" in rank_field["value"]
        ), rank_field
        assert "<@&1000>" in rank_field["value"], rank_field  # Wood Rank mention
        # Issue #211: player 2 has zero games, so their summary line carries
        # the placement tag and the footer explains it; veteran 1 does not.
        defenders = next(f for f in summary.fields if f["name"].startswith("Defenders"))
        attackers = next(f for f in summary.fields if f["name"].startswith("Attackers"))
        assert "placement" in defenders["value"], defenders
        assert "placement" not in attackers["value"], attackers
        assert summary.footer and "placement" in summary.footer, summary.footer

        bot, ctx = asyncio.run(_run_summary(set()))
        summary = ctx.embeds[0]
        for field in summary.fields:
            if field["name"] == "🏅 Rank Changes":
                continue
            assert (
                "**" not in field["value"]
            ), f"no delta may be bold without a doubledown: {field}"
            assert (
                "×2" not in field["value"]
            ), f"no player may be tagged without a doubledown: {field}"
        assert (
            not summary.footer
        ), f"footer must be omitted when nobody doubled down: {summary.footer}"

        # --- Issue #213: /report moves team-channel players to the lobby ----
        # End-to-end: the real report path must call move_players_to_lobby
        # with BOTH teams while they are still populated (before the state
        # reset clears them), and only when voice_presence is enabled.
        class LobbyGuild(FakeGuild):
            def __init__(self):
                super().__init__()
                self.lobby = types.SimpleNamespace(id=77, name="lobby")
                self.attackers = types.SimpleNamespace(id=78, name="Attackers")
                self.defenders = types.SimpleNamespace(id=79, name="Defenders")
                self.voice_channels = [self.lobby, self.attackers, self.defenders]

            def get_member(self, uid):
                return self._members.get(str(uid))

        class LobbyMember:
            def __init__(self, channel):
                self.voice = types.SimpleNamespace(channel=channel)
                self.moves = []
                # grant_season_roles runs just before the lobby move.
                self.roles = []

            async def move_to(self, channel):
                self.moves.append(channel)
                self.voice.channel = channel

            async def add_roles(self, role):
                self.roles.append(role)

        async def _run_lobby_move(enabled):
            bot = FakeBot()
            bot.selected_map = "Ascent"
            bot.double_downs = set()
            bot.player_mmr = {
                "1": {"mmr": 100, "wins": 2, "losses": 1, "matches_played": 3},
                "2": {"mmr": 100, "wins": 2, "losses": 1, "matches_played": 3},
            }
            bot.save_mmr_data = lambda: None
            cog = make_reporter(bot, fetch_result="ok", data_result=_match_payload())
            ctx = EmbedCtx()
            guild = LobbyGuild()
            guild._members = {
                "1": LobbyMember(guild.attackers),
                "2": LobbyMember(guild.defenders),
            }
            ctx.guild = guild
            moved_calls = []

            async def _spy_move(g, players):
                moved_calls.append(g)
                # The teams must still be populated at call time (this runs
                # before the report's state reset, not after).
                assert [str(p["id"]) for p in players] == ["1", "2"], players
                await _orig_move(g, players)

            _rm.move_players_to_lobby = _spy_move
            _orig_voice_enabled = _rm.voice_presence_enabled
            _rm.voice_presence_enabled = lambda: enabled
            try:
                await cog.report(ctx)
            finally:
                _rm.voice_presence_enabled = _orig_voice_enabled
                _rm.move_players_to_lobby = _orig_move
            return guild, moved_calls

        _orig_move = _rm.move_players_to_lobby

        guild, calls = asyncio.run(_run_lobby_move(enabled=True))
        assert len(calls) == 1, "voice_presence must trigger exactly one lobby move"
        assert guild._members["1"].moves, "attackers player must be moved to lobby"
        assert guild._members["2"].moves, "defenders player must be moved to lobby"
        assert guild._members["1"].voice.channel is guild.lobby
        assert guild._members["2"].voice.channel is guild.lobby

        # Feature off: no move at all (voice management stays opt-in).
        guild, calls = asyncio.run(_run_lobby_move(enabled=False))
        assert not calls, "lobby move must be skipped when voice_presence is off"
        assert not guild._members["1"].moves
    finally:
        _rm.duck_coins_enabled = _orig_enabled
        _rm.asyncio.sleep = _orig_sleep
        _rm.all_matches = _orig_all_matches
        _rm.seasons = _orig_seasons

    print("all report-claim retry self-checks passed")


if __name__ == "__main__":
    demo()
