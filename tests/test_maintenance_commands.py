import os
import sys
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
    delete_one=lambda *a, **k: None,
)
_database_stub.seasons = types.SimpleNamespace()
_database_stub.all_matches = types.SimpleNamespace()
_database_stub.recent_queue = types.SimpleNamespace()
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
_discord_stub.ext = types.SimpleNamespace()
_discord_stub.ext.commands = types.SimpleNamespace(
    command=lambda *a, **k: (lambda f: f),
    has_permissions=lambda **k: (lambda f: f),
    Cog=type("Cog", (), {"__init_subclass__": classmethod(lambda cls, **kw: None)}),
)
sys.modules["discord"] = _discord_stub
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

    print("all maintenance_commands self-checks passed")


if __name__ == "__main__":
    demo()
