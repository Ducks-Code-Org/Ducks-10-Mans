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

import discord  # noqa: E402

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

    # Missing #lobby: nobody can be moved, and the call must not raise.
    class NoLobbyGuild(FakeGuild):
        def __init__(self):
            super().__init__()
            self.voice_channels = [self.attackers, self.defenders]

    no_lobby = NoLobbyGuild()
    stranded = FakeMember(21, no_lobby.attackers)
    no_lobby._members = {21: stranded}
    asyncio.run(move_players_to_lobby(no_lobby, [{"id": "21"}]))
    assert (
        stranded.voice.channel is no_lobby.attackers
    ), "without a lobby channel nobody should be moved (and nothing raised)"

    # Permission/API failure on move_to: reported, not raised, and the rest
    # of the team still gets moved.
    class DeniedMember(FakeMember):
        async def move_to(self, channel):
            raise discord.Forbidden(
                types.SimpleNamespace(status=403, reason="Forbidden"), "no perms"
            )

    denied_guild = FakeGuild()
    denied = DeniedMember(31, denied_guild.attackers)
    still_moved = FakeMember(32, denied_guild.defenders)
    denied_guild._members = {31: denied, 32: still_moved}
    asyncio.run(move_players_to_lobby(denied_guild, [{"id": "31"}, {"id": "32"}]))
    assert denied.voice.channel is denied_guild.attackers, "denied move must not retry"
    assert (
        still_moved.voice.channel is denied_guild.lobby
    ), "one member's permission failure must not abort the remaining moves"

    print("report-lobby-move self-checks passed")


if __name__ == "__main__":
    demo()
