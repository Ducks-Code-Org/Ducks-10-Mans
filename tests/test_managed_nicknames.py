"""Self-checks for managed nicknames: `<rank icon> <Riot ID>` (issue #268).

All behavior is asserted through the public seam in game.nicknames with fake
guilds, members, a stubbed users collection and an in-memory MMR cache.
"""

import asyncio
import configparser
import os
import sys
import types
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class UsersStub:
    def __init__(self):
        self.docs = {}

    def find_one(self, query):
        return self.docs.get(str(query.get("discord_id")))


class DatabaseStub(types.ModuleType):
    def __init__(self):
        super().__init__("database")
        self.users = UsersStub()
        self.mmr_collection = types.SimpleNamespace(
            find=lambda *a, **k: [], update_one=lambda *a, **k: None
        )


_database_stub = DatabaseStub()
sys.modules["database"] = _database_stub

import discord  # noqa: E402

import globals as globals_mod  # noqa: E402
import game.nicknames as nn  # noqa: E402
from game.ranks import RANKS, SSR_NAME  # noqa: E402


def fake_features(**flags):
    cp = configparser.ConfigParser()
    cp.read_dict({"features": flags})
    return cp["features"]


class FakeMember:
    def __init__(self, uid, *, bot=False, nick=None, top_role=1, forbidden=False):
        self.id = uid
        self.bot = bot
        self.nick = nick
        self.top_role = top_role
        self.forbidden = forbidden
        self.edits = []

    async def edit(self, nick):
        self.edits.append(nick)
        if self.forbidden:
            response = types.SimpleNamespace(status=403, reason="Forbidden")
            raise discord.Forbidden(response, "missing permission")
        self.nick = nick


class FakeGuild:
    def __init__(self, members, *, owner_id=500):
        self.members = list(members)
        self.owner_id = owner_id
        self.me = FakeMember(9999, bot=True, top_role=100)
        self.members.append(self.me)

    def get_member(self, uid):
        return next((m for m in self.members if m.id == uid), None)


class FakeBot:
    def __init__(self, player_mmr):
        self.player_mmr = player_mmr


def user(discord_id, name, tag, display=None):
    doc = {"discord_id": str(discord_id), "name": name, "tag": tag}
    if display is not None:
        doc["riot_display_name"] = display
    return doc


def stats(mmr, played=True):
    return {"mmr": mmr, "matches_played": 10 if played else 0, "wins": 0, "losses": 0}


PLAYED = {
    "1": stats(450),  # Diamond
    "2": stats(800),  # rank 1
    "3": stats(0, played=False),
    "4": stats(50),  # Wood
    "6": stats(450),
}
BOT = FakeBot(PLAYED)
USERS = {
    "1": user(1, "alice", "na", "Alice#NA"),
    "2": user(2, "bob", "na", "Bob#NA"),
    "3": user(3, "carol", "x", "Carol#X"),
    "4": user(4, "dave", "x"),  # no canonical field: lowercase fallback
    "5": user(5, "", "x"),  # incomplete link
    "6": user(6, "long", "x", "A" * 31 + "#NA"),  # 34-char Riot ID
}


def reset_users():
    _database_stub.users.docs = {key: dict(doc) for key, doc in USERS.items()}


def run(coro):
    return asyncio.run(coro)


def with_flag(value=True):
    globals_mod.BOT_FEATURES = fake_features(managed_nicknames=str(value).lower())


def demo():
    original_features = globals_mod.BOT_FEATURES
    try:
        # Shipped config and the code fallback both default the flag to off.
        assert globals_mod.BOT_CONFIG["features"].getboolean("managed_nicknames") is (
            False
        ), "bot.ini must ship managed_nicknames = false"
        globals_mod.BOT_FEATURES = fake_features()
        assert nn.managed_nicknames_enabled() is False, "code fallback must be off"
        globals_mod.BOT_FEATURES = fake_features(managed_nicknames="not-a-bool")
        assert nn.managed_nicknames_enabled() is False, "malformed flag must stay off"

        # One icon per ranked tier, plus the single Supersonic Radiant glyph.
        tier_names = {name for _, name, _ in RANKS}
        assert set(nn.RANK_ICONS) == tier_names | {SSR_NAME}, "icon map has gaps"
        assert nn.RANK_ICONS["Iron Rank"] == "\U0001f6e1\ufe0f", "Iron needs FE0F"
        assert len(nn.RANK_ICONS[SSR_NAME]) == 1 and SSR_NAME == "Supersonic Radiant"

        reset_users()
        with_flag()

        # Flag off: every write path is a no-op.
        with_flag(False)
        m = FakeMember(1)
        guild = FakeGuild([m])
        assert run(nn.sync_member_nickname(BOT, guild, 1)) == "skipped"
        assert run(nn.clear_member_nickname(guild, 1)) == "skipped"
        assert run(nn.strip_rank_icons(guild)) == 0
        assert m.edits == [], "flag off must not edit or clear"

        # Ranked player: tier icon + canonical Riot ID.
        with_flag()
        m1 = FakeMember(1)
        guild = FakeGuild([m1])
        assert run(nn.sync_member_nickname(BOT, guild, 1)) == "updated"
        assert m1.nick == "💎 Alice#NA", m1.nick

        # Rank 1 gets the Supersonic Radiant icon instead of the tier icon.
        m2 = FakeMember(2)
        guild = FakeGuild([m2])
        assert run(nn.sync_member_nickname(BOT, guild, 2)) == "updated"
        assert m2.nick == "🌟 Bob#NA", m2.nick

        # Zero matches: Riot ID only, no icon.
        m3 = FakeMember(3)
        guild = FakeGuild([m3])
        assert run(nn.sync_member_nickname(BOT, guild, 3)) == "updated"
        assert m3.nick == "Carol#X", m3.nick

        # Missing canonical field: the lowercase name#tag fallback is used.
        m4 = FakeMember(4)
        guild = FakeGuild([m4])
        assert run(nn.sync_member_nickname(BOT, guild, 4)) == "updated"
        assert m4.nick == "🪵 dave#x", m4.nick

        # Incomplete link, unlinked ID, bot, owner, outranked, and missing
        # members are all skipped without an edit.
        cases = [
            ("incomplete link", FakeMember(5), 5),
            ("unlinked", FakeMember(7), 7),
            ("bot", FakeMember(8, bot=True), 8),
            ("owner", FakeMember(500), 500),
            ("outranked", FakeMember(9, top_role=100), 9),
            ("missing", None, 777),
        ]
        for label, member, uid in cases:
            guild = FakeGuild([member] if member else [])
            assert run(nn.sync_member_nickname(BOT, guild, uid)) == "skipped", label
            if member is not None:
                assert member.edits == [], f"{label} was edited"

        # Already carrying the target: unchanged, no edit.
        m1 = FakeMember(1, nick="💎 Alice#NA")
        guild = FakeGuild([m1])
        assert run(nn.sync_member_nickname(BOT, guild, 1)) == "unchanged"
        assert m1.edits == [], "unchanged must not call edit"

        # Longer than 32 characters: skipped and logged, never truncated.
        m6 = FakeMember(6)
        guild = FakeGuild([m6])
        with mock.patch.object(nn.log, "warning") as warn:
            assert run(nn.sync_member_nickname(BOT, guild, 6)) == "skipped"
        assert m6.edits == []
        assert any(
            "exceeds" in str(c) for c in warn.call_args_list
        ), warn.call_args_list

        # A forbidden edit returns failed; the surrounding guild sync goes on.
        bad = FakeMember(1, forbidden=True)
        good = FakeMember(4)
        guild = FakeGuild([bad, good])
        counts = run(nn.sync_guild_nicknames(BOT, guild))
        assert counts == {
            "updated": 1,
            "unchanged": 0,
            "skipped": 0,
            "failed": 1,
        }, counts
        assert good.nick == "🪵 dave#x", "a failure must not abort the sync"

        # Backfill counts, and a second run is all unchanged.
        m1 = FakeMember(1)
        m3 = FakeMember(3, nick="Carol#X")  # already the target
        unlinked = FakeMember(7)
        outranked = FakeMember(9, top_role=100)
        guild = FakeGuild([m1, m3, unlinked, outranked])
        counts = run(nn.sync_guild_nicknames(BOT, guild))
        assert counts == {
            "updated": 1,
            "unchanged": 1,
            "skipped": 2,
            "failed": 0,
        }, counts
        counts = run(nn.sync_guild_nicknames(BOT, guild))
        assert counts == {
            "updated": 0,
            "unchanged": 2,
            "skipped": 2,
            "failed": 0,
        }, counts

        # Unlink cleanup clears a managed nickname; a manual one survives and
        # an empty nickname is unchanged.
        m2 = FakeMember(2, nick="🌟 Bob#NA")
        guild = FakeGuild([m2])
        assert run(nn.clear_member_nickname(guild, 2)) == "cleared"
        assert m2.nick is None
        assert run(nn.clear_member_nickname(guild, 2)) == "unchanged"
        manual = FakeMember(2, nick="Bob (custom)")
        guild = FakeGuild([manual])
        assert run(nn.clear_member_nickname(guild, 2)) == "unchanged"
        assert manual.edits == [], "a manual nickname must not be cleared"

        # Season reset strips the icon and leaves the Riot ID.
        m1 = FakeMember(1, nick="💎 Alice#NA")
        m4 = FakeMember(4, nick="🪵 dave#x")
        guild = FakeGuild([m1, m4])
        assert run(nn.strip_rank_icons(guild)) == 2
        assert m1.nick == "Alice#NA", m1.nick
        assert m4.nick == "dave#x", m4.nick
        assert run(nn.strip_rank_icons(guild)) == 0, "second strip is a no-op"

        # The season-reset hook runs whether or not stats are reset.
        admin_src = open(
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "commands",
                "admin_commands.py",
            ),
            encoding="utf-8",
        ).read()
        season_body = admin_src.split("async def new_season", 1)[1].split(
            "@commands.hybrid_command", 1
        )[0]
        remove_at = season_body.index("remove_all_rank_roles")
        strip_at = season_body.index("strip_rank_icons", remove_at)
        between = season_body[remove_at:strip_at]
        assert "if reset" not in between, "rank icons must strip on every reset path"

        print("all managed-nickname self-checks passed")
    finally:
        globals_mod.BOT_FEATURES = original_features


if __name__ == "__main__":
    demo()
