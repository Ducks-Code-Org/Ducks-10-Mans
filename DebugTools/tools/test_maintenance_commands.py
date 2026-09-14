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

    print("all maintenance_commands self-checks passed")


if __name__ == "__main__":
    demo()
