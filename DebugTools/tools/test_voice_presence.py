import asyncio
import configparser
import os
import sys
import types

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

# Stub the DB module (import-time Mongo connection).
_database_stub = types.ModuleType("database")
_database_stub.users = types.SimpleNamespace(find_one=lambda *a, **k: None)
_database_stub.mmr_collection = types.SimpleNamespace()
_database_stub.seasons = types.SimpleNamespace()
_database_stub.all_matches = types.SimpleNamespace()
_database_stub.recent_queue = types.SimpleNamespace()
sys.modules["database"] = _database_stub

import discord

import globals
from voice_presence import (
    LOBBY_WAIT_SECONDS,
    missing_lobby_players,
    move_teams_to_voice,
    voice_presence_enabled,
    wait_for_lobby,
)


def fake_features(**flags):
    cp = configparser.ConfigParser()
    cp.read_dict({"features": flags})
    return cp["features"]


class FakeVoice:
    def __init__(self, channel):
        self.channel = channel


class FakeMember:
    def __init__(self, uid, channel=None):
        self.id = uid
        self.voice = FakeVoice(channel) if channel else None


class FakeChannel:
    def __init__(self, cid, name):
        self.id = cid
        self.name = name


class FakeGuild:
    def __init__(self, members, voice_channels):
        self._members = {m.id: m for m in members}
        self.voice_channels = voice_channels
        self.created = []

    def get_member(self, uid):
        return self._members.get(uid)

    async def create_voice_channel(self, name):
        channel = FakeChannel(1000 + len(self.created), name)
        self.voice_channels.append(channel)
        self.created.append(name)
        return channel


class FakeMemberMovable(FakeMember):
    def __init__(self, uid, channel=None):
        super().__init__(uid, channel)
        self.moves = []

    async def move_to(self, channel):
        self.moves.append(channel)
        self.voice = FakeVoice(channel)


def demo():
    original = globals.BOT_FEATURES
    try:
        # Default off: shipped bot.ini must not change the regular flow.
        globals.BOT_FEATURES = fake_features()
        assert voice_presence_enabled() is False, "missing flag must default off"
        globals.BOT_FEATURES = fake_features(voice_presence="false")
        assert voice_presence_enabled() is False
        globals.BOT_FEATURES = fake_features(voice_presence="true")
        assert voice_presence_enabled() is True

        lobby = FakeChannel(1, "lobby")
        other = FakeChannel(2, "General Voice")

        # No lobby channel: any voice channel counts as present.
        guild = FakeGuild([FakeMember(1, other), FakeMember(2, None)], [other])
        assert missing_lobby_players(guild, [{"id": "1"}, {"id": "2"}]) == ["2"]

        # Lobby exists: only lobby connections count.
        guild = FakeGuild([FakeMember(1, other), FakeMember(2, lobby)], [lobby, other])
        assert missing_lobby_players(guild, [{"id": "1"}, {"id": "2"}]) == ["1"]

        # Everyone in lobby -> wait returns immediately.
        guild = FakeGuild([FakeMember(1, lobby)], [lobby])
        sent = []

        async def send(msg):
            sent.append(msg)

        assert asyncio.run(
            wait_for_lobby(guild, [{"id": "1"}], send, lambda: False, poll_seconds=0.01)
        )

        # Cancelled mid-wait -> False, no crash.
        assert not asyncio.run(
            wait_for_lobby(
                guild,
                [{"id": "1"}, {"id": "2"}],
                send,
                lambda: True,
                poll_seconds=0.01,
            )
        )

        # Timeout -> False (uses a tiny timeout instead of waiting 10 min).
        assert not asyncio.run(
            wait_for_lobby(
                guild,
                [{"id": "1"}, {"id": "2"}],
                send,
                lambda: False,
                timeout_seconds=0.05,
                poll_seconds=0.01,
            )
        )
        assert any("Waiting for everyone" in m for m in sent)

        # Someone joins after a poll -> True.
        async def join_then_wait():
            task = asyncio.create_task(
                wait_for_lobby(
                    guild,
                    [{"id": "1"}, {"id": "2"}],
                    send,
                    lambda: False,
                    timeout_seconds=5,
                    poll_seconds=0.01,
                )
            )
            await asyncio.sleep(0.03)
            guild._members[2] = FakeMember(2, lobby)
            return await task

        assert asyncio.run(join_then_wait())
        assert any("Everyone is in the lobby" in m for m in sent)

        # Auto-move: connected players moved, others untouched, channels
        # created once by name and reused.
        atk = FakeChannel(10, "Attackers")
        guild = FakeGuild([], [atk])
        m1, m2, m3 = (
            FakeMemberMovable(1, other),
            FakeMemberMovable(2, other),
            FakeMemberMovable(3),
        )
        guild._members = {1: m1, 2: m2, 3: m3}
        asyncio.run(
            move_teams_to_voice(
                guild,
                [{"id": "1"}, {"id": "3"}],
                [{"id": "2"}],
            )
        )
        defenders = guild.voice_channels[-1]
        assert m1.moves == [atk], "team1 player not moved to Attackers"
        assert m2.moves == [defenders], "team2 player not moved to Defenders"
        assert m3.moves == [], "player without voice must not be moved"
        assert sorted(guild.created) == [
            "Defenders"
        ], "missing team channel not created"

        # Re-running reuses existing channels: no new creation, no moves.
        asyncio.run(
            move_teams_to_voice(
                guild,
                [{"id": "1"}, {"id": "3"}],
                [{"id": "2"}],
            )
        )
        assert guild.created == ["Defenders"], "team channels must be reused"
        assert m1.moves == [atk] and m2.moves == [defenders], "no double moves"

        # A 10 minute lobby window is the documented requirement.
        assert LOBBY_WAIT_SECONDS == 600
    finally:
        globals.BOT_FEATURES = original

    print("all voice presence self-checks passed")


if __name__ == "__main__":
    demo()
