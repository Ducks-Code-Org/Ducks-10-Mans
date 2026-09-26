import os
import sys
import types
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub modules with import-time side effects (Mongo connection) so this
# self-check can run without a database.
_database_stub = types.ModuleType("database")
_database_stub.users = types.SimpleNamespace(find_one=lambda *a, **k: None)
_database_stub.mmr_collection = types.SimpleNamespace(
    update_one=lambda *a, **k: None,
    find_one=lambda *a, **k: None,
    find=lambda *a, **k: [],
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
    Cog=type("Cog", (), {"__init_subclass__": classmethod(lambda cls, **kw: None)}),
)
_app_stub = types.SimpleNamespace(
    describe=lambda **k: (lambda f: f),
    Attachment=object,
)
_discord_stub.app_commands = _app_stub
_ui_stub = types.ModuleType("discord.ui")


class _FakeUiItem:
    def __init__(self, *a, **k):
        pass

    def __init_subclass__(cls, **k):
        pass


for _name in ("Button", "Select", "View", "Modal"):
    setattr(_ui_stub, _name, type(_name, (_FakeUiItem,), {}))
for _name in ("select", "button"):
    setattr(_ui_stub, _name, lambda *a, **k: (lambda f: f))
_discord_stub.ui = _ui_stub
sys.modules["discord"] = _discord_stub
sys.modules["discord.ui"] = _ui_stub
sys.modules["discord.ext"] = _discord_stub.ext
sys.modules["discord.ext.commands"] = _discord_stub.ext.commands

import commands.maintenance_commands as mc  # noqa: E402


def demo():
    # resolve_user_arg: mentions, linked Riot IDs, rejects garbage
    assert mc._MENTION_RE.match("<@123456789>").group(1) == "123456789"
    assert mc._MENTION_RE.match("<@!123456789>").group(1) == "123456789"
    assert mc.resolve_user_arg("not a user") is None
    assert mc.resolve_user_arg("") is None
    assert mc.resolve_user_arg("<@123456789>") == "123456789"

    # resolve_user_arg with a guild: display-name and username fallbacks
    class _Member:
        def __init__(self, id, name, display_name=None):
            self.id = id
            self.name = name
            self.display_name = display_name or name

    class _Guild:
        members = [
            _Member(1, "pyr", "Pyrallux"),
            _Member(2, "treetops", "Tree Tops"),
        ]

    g = _Guild()
    assert mc.resolve_user_arg("Pyrallux", g) == "1"
    assert mc.resolve_user_arg("pyr", g) == "1"
    assert mc.resolve_user_arg("Tree Tops", g) == "2"
    assert mc.resolve_user_arg("nobody", g) is None
    assert mc.resolve_user_arg("Pyrallux", None) is None

    # parse_edit_args: field-token scan, spaces preserved on both sides
    ua, f, v = mc.parse_edit_args("Pyrallux Tree Tops#P ENG mmr 1500")
    assert ua == "Pyrallux Tree Tops#P ENG" and f == "mmr" and v == "1500"
    ua, f, v = mc.parse_edit_args("<@123> riot New Name#TAG")
    assert ua == "<@123>" and f == "riot" and v == "New Name#TAG"
    ua, f, v = mc.parse_edit_args("@user losses 3")
    assert ua == "@user" and f == "losses" and v == "3"
    ua, f, v = mc.parse_edit_args("no field here")
    assert ua is None and f is None and v is None
    ua, f, v = mc.parse_edit_args("")
    assert ua is None and f is None and v is None
    # Field-looking token at the start: everything before it is empty -> invalid
    ua, f, v = mc.parse_edit_args("mmr 1500")
    assert ua == "" and f == "mmr" and v == "1500"

    # set_ini_value: replaces, preserves comments/sections, creates missing keys
    import tempfile

    tmp = os.path.join(tempfile.gettempdir(), "test_maint_bot.ini")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("# comment\n[features]\nseason_role = true\n# trailing\n")
    mc.set_ini_value(tmp, "features", "season_role", "false")
    content = open(tmp, encoding="utf-8").read()
    assert "season_role = false" in content
    assert "# comment" in content and "# trailing" in content
    mc.set_ini_value(tmp, "features", "new_flag", "true")
    content = open(tmp, encoding="utf-8").read()
    assert "new_flag = true" in content and "# trailing" in content
    mc.set_ini_value(tmp, "features", "second_section_flag", "1")
    content = open(tmp, encoding="utf-8").read()
    assert "[features]" in content and "second_section_flag = 1" in content
    os.remove(tmp)

    # _extract_match_id: tracker.gg URLs, bare ids, rejects junk
    mid = "2233f144-b849-42e2-8656-84417f639234"
    assert mc._extract_match_id(f"https://tracker.gg/valorant/match/{mid}") == mid
    assert (
        mc._extract_match_id(f"https://tracker.gg/valorant/match/{mid}/?utm=x") == mid
    )
    assert mc._extract_match_id(mid) == mid
    assert mc._extract_match_id(mid.upper()) == mid
    assert mc._extract_match_id("") is None
    assert mc._extract_match_id("not a match id") is None
    assert mc._extract_match_id("https://tracker.gg/valorant/profile/foo") is None
    assert mc._extract_match_id("abc123") is None

    # rounds_to_int: dicts, numbers, garbage
    assert mc.rounds_to_int({"won": 14}) == 14
    assert mc.rounds_to_int(14) == 14
    assert mc.rounds_to_int("14") == 14
    assert mc.rounds_to_int({}) == 0
    assert mc.rounds_to_int(None) == 0

    # resolve_match_players: two players sharing a game name but different
    # tags must both resolve (name-keyed maps silently dropped one).
    class _UsersStub:
        def __init__(self, by_puuid, by_name):
            self._by_puuid, self._by_name = by_puuid, by_name

        def find_one(self, q):
            if "puuid" in q:
                return self._by_puuid.get(q["puuid"])
            return self._by_name.get((q.get("name"), q.get("tag")))

    original_users = mc.users
    try:
        mc.users = _UsersStub(
            by_puuid={},
            by_name={
                ("smurf", "na1"): {"discord_id": "111"},
                ("smurf", "euw"): {"discord_id": "222"},
            },
        )
        p1 = {"puuid": "", "name": "Smurf", "tag": "NA1"}
        p2 = {"puuid": "", "name": "Smurf", "tag": "EUW"}
        p3 = {"puuid": "", "name": "Ghost", "tag": "X"}
        pid_of, unlinked = mc.resolve_match_players([p1, p2, p3])
        assert pid_of == {id(p1): "111", id(p2): "222"}, pid_of
        assert unlinked == ["Ghost#X"], unlinked

        # puuid lookup wins over name/tag
        mc.users = _UsersStub(
            by_puuid={"abc-123": {"discord_id": "999"}},
            by_name={("smurf", "na1"): {"discord_id": "111"}},
        )
        p4 = {"puuid": "abc-123", "name": "Smurf", "tag": "NA1"}
        pid_of, unlinked = mc.resolve_match_players([p4])
        assert pid_of == {id(p4): "999"} and not unlinked
    finally:
        mc.users = original_users

    # Duck Coins are per-season. SEASON_STAT_DEFAULTS feeds !resetplayer
    # and !resetseason; !newseason resets coins only when it resets stats,
    # so `noreset` preserves balances, while per-match coin state is always
    # dropped when the season turns over.
    assert (
        mc.SEASON_STAT_DEFAULTS.get("duck_coins") == 0
    ), "season stat defaults must zero duck_coins"

    import ast

    bot_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "bot.py",
    )
    tree = ast.parse(open(bot_path, encoding="utf-8").read())
    create_fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "create_new_season"
    )

    def calls_in(nodes):
        """All function names called anywhere under these statements."""
        names = []
        for node in nodes:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    names.append(ast.unparse(sub.func))
        return names

    # Everything at the function's top level except the reset_player_stats
    # branch: coin balances may only be zeroed inside that branch.
    unconditional = [
        n
        for n in create_fn.body
        if not (isinstance(n, ast.If) and ast.unparse(n.test) == "reset_player_stats")
    ]
    assert "reset_all_coins" not in calls_in(
        unconditional
    ), "noreset must preserve coin balances"
    # The stats-reset branch zeroes coins and resets stats together.
    reset_branch = [
        n
        for n in create_fn.body
        if isinstance(n, ast.If) and ast.unparse(n.test) == "reset_player_stats"
    ]
    assert reset_branch, "create_new_season must branch on reset_player_stats"
    branch_calls = calls_in(reset_branch)
    assert (
        "reset_all_coins" in branch_calls
    ), "newseason with reset must zero duck_coins"
    assert (
        "self._reset_all_players_for_new_season" in branch_calls
    ), "stats reset must stay in the same branch"
    # Per-match state is dropped unconditionally (season turns over either
    # way), so it must appear outside the reset branch.
    assert "clear_season_coin_state" in calls_in(
        unconditional
    ), "newseason must always drop per-match coin state"

    resetseason_src = open(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "commands",
            "maintenance_commands.py",
        ),
        encoding="utf-8",
    ).read()
    assert (
        "clear_season_coin_state(self.bot)" in resetseason_src
    ), "!resetseason must drop per-match coin state"

    # --- !setconfig validation --------------------------------------------
    # A value like "100%" poisons configparser on the next read (interpolation
    # error), so only strict booleans may be written and the whole file must
    # round-trip before the write is accepted.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        ini = os.path.join(tmp, "bot.ini")
        _write_test_ini(ini)
        mc.set_ini_value(ini, "features", "duck_coins", "false")
        probe = mc.configparser.ConfigParser()
        probe.read(ini)
        assert probe["features"].getboolean("duck_coins") is False
        assert "# comment" in open(ini, encoding="utf-8").read(), "comments preserved"
        # A value that would break parsing is rejected by the command before
        # it is left on disk: simulate the command's validate-then-restore.
        before = open(ini, encoding="utf-8").read()
        mc.set_ini_value(ini, "features", "duck_coins", "100%")
        try:
            bad = mc.configparser.ConfigParser()
            bad.read(ini)
            bad["features"].getboolean("duck_coins")
            broke = False
        except Exception:
            broke = True
        assert broke, "the probe must detect a poisoned value"
        open(ini, "w", encoding="utf-8").write(before)
        good = mc.configparser.ConfigParser()
        good.read(ini)
        assert good["features"].getboolean("duck_coins") is False, "file restored"

    # globals.feature_enabled must survive a poisoned value rather than
    # raising configparser.InterpolationSyntaxError at every call site.
    import globals as globals_mod

    class _Poisoned:
        def getboolean(self, name):
            raise mc.configparser.InterpolationSyntaxError(
                "duck_coins", "features", "100%"
            )

    original_features = globals_mod.BOT_FEATURES
    globals_mod.BOT_FEATURES = _Poisoned()
    try:
        assert globals_mod.feature_enabled("duck_coins", default=True) is True
        assert globals_mod.feature_enabled("voice_presence", default=False) is False
    finally:
        globals_mod.BOT_FEATURES = original_features

    # --- !recoverseason is transactional (rollback on failure) -------------
    # A mid-recovery exception must not leave any collection half-wiped.
    recoverseason_src = resetseason_src.split("async def recoverseason", 1)[1]
    assert "start_session" in recoverseason_src, "recoverseason must use a session"
    assert (
        "start_transaction" in recoverseason_src
    ), "recoverseason must be transactional"
    assert "rolled back" in recoverseason_src, "failure reply must mention rollback"

    # --- /substitute works pre-team (issue #249) ----------------------------
    # The command must be callable from the moment the queue is full (lobby
    # wait onwards), swapping the signup queue entry: the lobby wait tracks
    # the new player through the live queue. Pre-team skips team assignment
    # and doubledown refunds (nothing exists yet), keeps the Riot-ID check,
    # and still swaps the match roles.
    substitute_src = resetseason_src.split("async def substitute", 1)[1].split(
        "    @commands.hybrid_command", 1
    )[0]
    assert (
        "match_ongoing or finalized" in substitute_src
    ), "substitute must also accept a finalized signup without teams"
    assert (
        "self.bot.match_ongoing\n            and duck_coins_enabled()"
        in substitute_src
    ), "doubledown refund must be gated on an ongoing match"
    assert (
        "and self.bot.match_ongoing:" in substitute_src
    ), "team voice move must be skipped pre-team"
    assert (
        "no linked Riot ID" in substitute_src
    ), "pre-team substitution keeps the Riot-ID check"

    import configparser as _cp
    import globals as _g

    class _RoleStub:
        pass

    original_users = mc.users
    original_verify = mc.verify_riot_account_async
    original_add = None
    original_remove = None
    import views.signup_view as _sv

    original_add, original_remove = _sv.add_match_role, _sv.remove_match_role

    async def _fake_verify(session, name, tag):
        return (True, "ok")

    _role_calls = []

    async def _fake_add(bot_, guild, uid):
        _role_calls.append(("add", uid))

    async def _fake_remove(bot_, guild, uid):
        _role_calls.append(("remove", uid))

    mc.users = types.SimpleNamespace(
        find_one=lambda q: {"discord_id": "9", "name": "Sub", "tag": "TAG"}
    )
    mc.verify_riot_account_async = _fake_verify
    _sv.add_match_role, _sv.remove_match_role = _fake_add, _fake_remove
    saved_features = _g.BOT_FEATURES
    features_cp = _cp.ConfigParser()
    features_cp.read_dict({"features": {"duck_coins": "false"}})
    _g.BOT_FEATURES = features_cp["features"]
    try:
        bot = types.SimpleNamespace()
        bot.report_lock = asyncio.Lock()
        bot.signup_active = False
        bot.setup_generation = 1
        bot.match_setup_generation = 1
        bot.match_ongoing = False
        bot.queue = [{"id": "1", "name": "out"}, {"id": "2", "name": "keep"}]
        bot.team1 = []
        bot.team2 = []
        bot.double_downs = set()
        bot.player_names = {}
        bot.ensure_player_mmr = lambda pid, names: None
        bot.match_role = _RoleStub()

        cog = object.__new__(mc.MaintenanceCommands)
        cog.bot = bot

        class _SubCtx:
            def __init__(self):
                self.sent = []
                self.author = types.SimpleNamespace(id=42, name="admin")
                self.guild = None

            async def send(self, msg=None, **k):
                self.sent.append(msg)

            def get_member(self, uid):
                return None

        # Pre-team swap: queue entry replaced, no teams touched, roles swapped.
        ctx = _SubCtx()
        asyncio.run(cog.substitute(ctx, "<@1>", "<@9>"))
        assert [p["id"] for p in bot.queue] == ["9", "2"], bot.queue
        assert bot.team1 == [] and bot.team2 == [], "no team assignment pre-team"
        assert ("add", "9") in _role_calls and ("remove", "1") in _role_calls
        assert bot.double_downs == set(), "no doubledown handling pre-team"
        assert (
            "lobby wait now tracks the new player" in ctx.sent[0]
        ), ctx.sent[0]

        # Outgoing player must be in the queue (or a team) to be subbed out.
        bot.queue = [{"id": "1", "name": "out"}, {"id": "2", "name": "keep"}]
        ctx = _SubCtx()
        asyncio.run(cog.substitute(ctx, "<@5>", "<@9>"))
        assert "not in the current signup queue" in ctx.sent[0], ctx.sent[0]

        # Filling phase (signup still active): refused.
        bot.signup_active = True
        ctx = _SubCtx()
        asyncio.run(cog.substitute(ctx, "<@2>", "<@9>"))
        assert "queue is full" in ctx.sent[0], ctx.sent[0]
        bot.signup_active = False

        # Cancelled signup (generation bumped, resources released): refused.
        bot.match_setup_generation = None
        ctx = _SubCtx()
        asyncio.run(cog.substitute(ctx, "<@2>", "<@9>"))
        assert "Substitutions work" in ctx.sent[0], ctx.sent[0]
        bot.match_setup_generation = bot.setup_generation

        # Post-team: unchanged behavior — team entry swapped, refund branch
        # reachable, side named in the announcement.
        bot.match_ongoing = True
        bot.team1 = [{"id": "2", "name": "keep"}]
        bot.team2 = []
        bot.double_downs = {"2"}
        ctx = _SubCtx()
        asyncio.run(cog.substitute(ctx, "<@2>", "<@9>"))
        assert bot.team1 == [{"id": "9", "name": "Sub"}], bot.team1
        assert [p["id"] for p in bot.queue] == ["1", "9"], bot.queue
        assert "(Attackers)" in ctx.sent[0], ctx.sent[0]
    finally:
        mc.users = original_users
        mc.verify_riot_account_async = original_verify
        _sv.add_match_role, _sv.remove_match_role = original_add, original_remove
        _g.BOT_FEATURES = saved_features

    print("all maintenance_commands self-checks passed")


def _write_test_ini(path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("[features]\n# comment\nduck_coins = true\n")


if __name__ == "__main__":
    demo()
