import asyncio
import configparser
import os
import sys
import types

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

# Stub the DB module (import-time Mongo connection) before importing report.py.
_database_stub = types.ModuleType("database")
_database_stub.users = types.SimpleNamespace(find_one=lambda *a, **k: None)
_database_stub.mmr_collection = types.SimpleNamespace()
_database_stub.seasons = types.SimpleNamespace(
    find_one=lambda *a, **k: {"season_number": 2}
)
_database_stub.all_matches = types.SimpleNamespace()
_database_stub.recent_queue = types.SimpleNamespace()
sys.modules["database"] = _database_stub

import discord

import globals
from globals import feature_enabled
from commands.report import grant_season_roles


def fake_features(**flags):
    cp = configparser.ConfigParser()
    cp.read_dict({"features": flags})
    return cp["features"]


class FakeMember:
    def __init__(self, uid):
        self.id = uid
        self.roles = []

    async def add_roles(self, role):
        if role not in self.roles:
            self.roles.append(role)


class FakeGuild:
    def __init__(self, members):
        self.roles = []
        self._members = {m.id: m for m in members}
        self.created = []

    def get_member(self, uid):
        return self._members.get(uid)

    async def fetch_member(self, uid):
        response = types.SimpleNamespace(status=404, reason="Not Found")
        raise discord.NotFound(response, "Member not found")

    async def create_role(self, name):
        role = types.SimpleNamespace(name=name)
        self.roles.append(role)
        self.created.append(name)
        return role


def run_grant(features, members, players):
    globals.BOT_FEATURES = features
    guild = FakeGuild(members)
    asyncio.run(grant_season_roles(guild, players))
    return guild


def demo():
    original = globals.BOT_FEATURES
    try:
        # Shipped bot.ini enables the feature.
        assert (
            feature_enabled("season_role") is True
        ), "bot.ini should enable season_role"
        # A missing flag keeps the documented default.
        assert (
            feature_enabled("no_such_flag") is True
        ), "missing flag should default True"
        # An invalid value falls back instead of crashing the report path.
        globals.BOT_FEATURES = fake_features(season_role="not-a-bool")
        assert feature_enabled("season_role") is True, "invalid flag should fall back"

        players = [{"id": "1"}, {"id": "2"}]

        # Disabled: no role created, nobody touched.
        guild = run_grant(fake_features(season_role="false"), [FakeMember(1)], players)
        assert guild.created == [], "disabled feature must not create the season role"

        # Enabled: role created once and granted to members.
        m1, m2 = FakeMember(1), FakeMember(2)
        guild = run_grant(fake_features(season_role="true"), [m1, m2], players)
        assert guild.created == [
            "Season-2"
        ], f"unexpected role creation: {guild.created}"
        assert (
            m1.roles == guild.roles and m2.roles == guild.roles
        ), "role not granted to all"

        # A departed member (fetch raises NotFound) must not abort the rest.
        m3 = FakeMember(3)
        guild = run_grant(
            fake_features(season_role="true"), [m3], [{"id": "99"}, {"id": "3"}]
        )
        assert m3.roles, "later players must still be granted after a missing member"

        # A malformed player entry must be skipped, not crash the grant.
        m4 = FakeMember(4)
        guild = run_grant(
            fake_features(season_role="true"), [m4], [{"nope": "x"}, {"id": "4"}]
        )
        assert m4.roles, "later players must still be granted after a malformed entry"

        print("all season role toggle self-checks passed")
    finally:
        globals.BOT_FEATURES = original


if __name__ == "__main__":
    demo()
