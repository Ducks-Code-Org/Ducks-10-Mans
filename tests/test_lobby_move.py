"""Self-check: /report moves team-channel players back to #lobby (issue #213)."""

import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _Coll:
    def __getattr__(self, name):
        return lambda *a, **k: None

    def find(self, *a, **k):
        return []


_database_stub = types.ModuleType("database")
for name in (
    "users",
    "mmr_collection",
    "seasons",
    "all_matches",
    "coin_escrow",
    "interests",
    "recent_queue",
):
    setattr(_database_stub, name, _Coll())
_database_stub.client = types.SimpleNamespace(close=lambda: None)
sys.modules["database"] = _database_stub

from game.voice_presence import (  # noqa: E402
    LOBBY_CHANNEL_NAME,
    move_players_to_lobby,
)


class FakeVoiceChannel:
    def __init__(self, cid, name):
        self.id = cid
        self.name = name


class _Voice:
    def __init__(self, channel=None):
        self.channel = channel


class FakeMember:
    def __init__(self, mid, channel):
        self.id = mid
        self.voice = _Voice(channel)

    async def move_to(self, channel):
        self.moves = getattr(self, "moves", 0) + 1
        self.voice.channel = channel


class FakeGuild:
    def __init__(self):
        self.lobby = FakeVoiceChannel(1, LOBBY_CHANNEL_NAME)
        self.attackers = FakeVoiceChannel(2, "Attackers")
        self.defenders = FakeVoiceChannel(3, "Defenders")
        self.voice_channels = [self.lobby, self.attackers, self.defenders]
        self._members = {}

    def get_member(self, mid):
        return self._members.get(mid)


def demo():
    guild = FakeGuild()

    m_in_attackers = FakeMember(11, guild.attackers)
    m_in_defenders = FakeMember(12, guild.defenders)
    m_left = FakeMember(13, None)  # already left voice
    m_elsewhere = FakeMember(14, guild.lobby)  # already back in the lobby
    m_other_channel = FakeMember(15, FakeVoiceChannel(9, "Hangout"))  # moved self
    guild._members = {
        11: m_in_attackers,
        12: m_in_defenders,
        13: m_left,
        14: m_elsewhere,
        15: m_other_channel,
    }

    players = [
        {"id": "11", "name": "a"},
        {"id": "12", "name": "b"},
        {"id": "13", "name": "c"},
        {"id": "14", "name": "d"},
        {"id": "15", "name": "e"},
        {"id": "oops", "name": "malformed"},  # skipped quietly
    ]

    asyncio.run(move_players_to_lobby(guild, players))

    assert m_in_attackers.voice.channel is guild.lobby, "attackers -> lobby"
    assert m_in_defenders.voice.channel is guild.lobby, "defenders -> lobby"
    assert (
        m_left.voice is not None and m_left.voice.channel is None
    ), "players who already left voice must not be moved (they have no voice)"
    assert m_elsewhere.voice.channel is guild.lobby, "lobby occupant untouched"
    assert (
        m_other_channel.voice.channel.id == 9
    ), "players who moved themselves elsewhere must be left alone"
    print("report-lobby-move self-checks passed")


if __name__ == "__main__":
    demo()
