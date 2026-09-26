import asyncio
import configparser
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub the DB module (import-time Mongo connection).
_database_stub = types.ModuleType("database")
_database_stub.users = types.SimpleNamespace(find_one=lambda *a, **k: None)
_database_stub.mmr_collection = types.SimpleNamespace()
_database_stub.seasons = types.SimpleNamespace()
_database_stub.all_matches = types.SimpleNamespace()
_database_stub.recent_queue = types.SimpleNamespace()
_database_stub.coin_escrow = types.SimpleNamespace(
    update_one=lambda *a, **k: None, find_one=lambda *a, **k: None
)
sys.modules["database"] = _database_stub

import discord

import globals
from game.voice_presence import (
    LOBBY_WAIT_SECONDS,
    _deadline_text,
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
    def __init__(self, uid, channel=None, display_name=None):
        self.id = uid
        self.voice = FakeVoice(channel) if channel else None
        self.display_name = display_name or f"user{uid}"


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


class FakeMessage:
    def __init__(self, content):
        self.content = content
        self.edits = []

    async def edit(self, **kwargs):
        self.edits.append(kwargs)


def wait_for_lobby_with_joiner(guild, queue, send, joiner, **kwargs):
    """Run wait_for_lobby while `joiner` runs alongside it.

    `joiner` is a coroutine that mutates guild state; it gets the wait task
    so it can time its join. Returns (result, joiner_result).
    """

    async def run():
        task = asyncio.create_task(
            wait_for_lobby(guild, queue, send, lambda: False, **kwargs)
        )
        joiner_result = await joiner(task)
        return await task, joiner_result

    return asyncio.run(run())


class RecordingSender:
    """Send seam that records messages; each send returns a FakeMessage.

    The message-like return lets the countdown edit loop be observed
    through this same seam (issue #248).
    """

    def __init__(self):
        self.messages = []

    async def __call__(self, content):
        msg = FakeMessage(content)
        self.messages.append(msg)
        return msg


def run_wait(guild, queue, send=None, is_cancelled=lambda: False, **kwargs):
    """One lobby wait at test cadences, returning (result, sender).

    Defaults scale the 15s poll and 1s tick down to milliseconds so the
    final-warning path is exercised without real 10-minute runs.
    """
    sender = send if send is not None else RecordingSender()
    result = asyncio.run(
        wait_for_lobby(
            guild,
            queue,
            sender,
            is_cancelled,
            timeout_seconds=kwargs.pop("timeout_seconds", 0.05),
            poll_seconds=kwargs.pop("poll_seconds", 0.01),
            tick_seconds=kwargs.pop("tick_seconds", 0.001),
            warning_seconds=kwargs.pop("warning_seconds", 0.03),
            **kwargs,
        )
    )
    return result, sender


def wait_with_joiner(guild, queue, joiner, send=None):
    """run_wait's concurrent variant: `joiner` mutates guild state mid-wait."""
    sender = send if send is not None else RecordingSender()
    result, joiner_result = wait_for_lobby_with_joiner(
        guild,
        queue,
        sender,
        joiner,
        timeout_seconds=0.05,
        poll_seconds=0.01,
        tick_seconds=0.001,
        warning_seconds=0.03,
    )
    return result, sender, joiner_result


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
                tick_seconds=0.001,
                warning_seconds=0.03,
            )
        )
        assert any("lobby voice channel" in m and "2" in m for m in sent)

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
                    warning_seconds=1,
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

        # A 10 minute lobby window is the documented requirement, and the
        # notice deadline is derived from the wait window actually in use.
        assert LOBBY_WAIT_SECONDS == 600
        assert _deadline_text(LOBBY_WAIT_SECONDS) == "10 minutes"
        assert _deadline_text(60) == "1 minute"
        assert _deadline_text(2) == "0:02"

        # A player joining during the final poll window is still detected:
        # the timeout check runs after the presence re-check, so the last
        # poll that sees everyone in the lobby wins (issue #233).
        guild = FakeGuild([], [lobby])

        async def join_in_final_window(task):
            # Join between the second-to-last and last poll: past the old
            # pre-poll deadline check, visible only to the final poll.
            await asyncio.sleep(0.045)
            guild._members[2] = FakeMember(2, lobby)
            return "joined"

        result, _, _ = wait_with_joiner(guild, [{"id": "2"}], join_in_final_window)
        assert result is True, "a join inside the last poll window must be detected"

        # Staged messaging (issue #233): the first notice must NOT ping the
        # missing players, only tell them to join.
        guild = FakeGuild([], [lobby])
        result, sender = run_wait(guild, [{"id": "1"}, {"id": "2"}])
        assert result is False
        first = sender.messages[0].content
        assert (
            "to join or the match will be cancelled" in first
        ), "initial notice must state the deadline (issue #247)"
        assert (
            "<@1>" not in first and "<@2>" not in first
        ), "initial notice must not ping missing players"
        assert any(
            "will be cancelled in" in m.content and "<@1>" in m.content
            for m in sender.messages[1:]
        ), "final warning must ping missing players with a countdown (issue #248)"

        # Final warning (issue #248): exactly one warning message, real
        # pings, no emojis, live m:ss countdown edited every tick from the
        # live queue, ending in the cancelled outcome.
        warning_guild = FakeGuild([], [lobby])
        warning_queue = [{"id": "1"}, {"id": "2"}]
        result, warning_sender = run_wait(warning_guild, warning_queue)
        assert result is False
        warning = warning_sender.messages[1]
        assert (
            warning_sender.messages[0].content != warning.content
        ), "the warning must be a distinct message from the initial notice"
        assert (
            len(warning_sender.messages) == 2
        ), "the warning must be a single message, edited in place"
        warn_text = warning.content
        assert "⚠" not in warn_text and "⏰" not in warn_text, warn_text
        assert "<@1>" in warn_text and "<@2>" in warn_text, warn_text
        assert "will be cancelled in" in warn_text, warn_text
        assert "0:0" in warn_text, "countdown must show m:ss (issue #248)"
        assert len(warning.edits) >= 1, "countdown must live-edit (issue #248)"
        assert all(
            "will be cancelled in" in e["content"] or "Match cancelled." == e["content"]
            for e in warning.edits
        ), "edits must keep the countdown format, ending in the outcome"
        assert warning.edits[-1]["content"] == "Match cancelled."
        last_content_edit = next(
            e["content"]
            for e in reversed(warning.edits)
            if e["content"] != "Match cancelled."
        )
        assert "<@1>" in last_content_edit and "<@2>" in last_content_edit
        assert "0:0" in last_content_edit, "final countdown keeps m:ss"

        def first_notice(guild, queue, timeout_seconds=0.05):
            """Send-seam record of the first lobby-wait notice only."""
            sender = RecordingSender()
            asyncio.run(
                wait_for_lobby(
                    guild,
                    queue,
                    sender,
                    lambda: False,
                    timeout_seconds=timeout_seconds,
                    poll_seconds=0.01,
                )
            )
            return sender.messages[0].content

        # Notice names (issue #247): display names as plain text, lobby as
        # a real channel mention, raw-id fallback for departed members.
        named = FakeGuild([FakeMember(1, display_name="Ducky"), FakeMember(2)], [lobby])
        notice = first_notice(named, [{"id": "1"}, {"id": "2"}])
        assert "Ducky" in notice and "user2" in notice, notice
        assert "@" not in notice, "display names must not carry an @ prefix"
        assert not any(
            ord(c) >= 0x2600 for c in notice
        ), "notice must contain no emojis"
        assert "<#1>" in notice, "lobby must be referenced as a channel mention"
        assert "lobby voice channel" in notice, notice
        assert "<@" not in notice.replace("<#1>", ""), notice
        # The lobby is matched case/whitespace-insensitively for the link.
        weird = FakeGuild([], [FakeChannel(20, "  LOBBY ")])
        assert "<#20>" in first_notice(weird, [{"id": "1"}])
        # A departed member falls back to the raw id as plain text, and a
        # member with no readable name falls back the same way.
        gone = first_notice(FakeGuild([], [lobby]), [{"id": "1"}])
        assert ": 1 — you have" in gone, "raw-id fallback failed"
        nameless_member = FakeMember(1)
        nameless_member.display_name = ""
        nameless = first_notice(FakeGuild([nameless_member], [lobby]), [{"id": "1"}])
        assert ": 1 — you have" in nameless, "empty display name must use the id"
        # No lobby channel -> generic "a voice channel" wording.
        nolobby = first_notice(FakeGuild([], [other]), [{"id": "1"}])
        assert "a **voice channel**" in nolobby, nolobby
        # A short wait window must not claim a 10-minute deadline.
        assert "0:02" in first_notice(
            FakeGuild([], [lobby]), [{"id": "1"}], timeout_seconds=2
        )

        # Substitution reflected in the countdown (issue #248/#249): the
        # warning text is re-rendered from the live queue each edit, so an
        # admin swapping a player during the final two minutes moves the
        # ping from the old player to the new one without extra machinery.
        sub_guild = FakeGuild([], [lobby])

        async def sub_during_warning(task):
            await asyncio.sleep(0.04)
            sub_guild._members[9] = FakeMember(9)
            return "subbed"

        result, warning_sender, _ = wait_with_joiner(
            sub_guild, [{"id": "1"}, {"id": "9"}], sub_during_warning
        )
        assert result is False, "wait must still time out after the sub"
        warning = warning_sender.messages[1]
        assert "<@1>" in warning.content, warning.content
        first_edits = [e["content"] for e in warning.edits if "<@9>" in e["content"]]
        assert first_edits, "the subbed-in player must appear in the countdown"
        assert not [
            e["content"] for e in warning.edits if "<@2>" in e["content"]
        ], "the subbed-out player must never be pinged by the countdown"

        # Early join before the warning threshold: no countdown message at
        # all, and the success flow keeps its own announcement.
        early_guild = FakeGuild([], [lobby])

        async def join_early(task):
            await asyncio.sleep(0.01)
            early_guild._members[1] = FakeMember(1, lobby)
            return "joined"

        result, early_sender, _ = wait_with_joiner(
            early_guild, [{"id": "1"}], join_early
        )
        assert result is True
        assert (
            len(early_sender.messages) == 2
        ), "no countdown may be sent when everyone joins early"
        assert "Everyone is in the lobby" in early_sender.messages[1].content
        assert all(
            m.edits == [] for m in early_sender.messages
        ), "no countdown edits may happen without a countdown message"

        # No busy-spin (issue #248 review): the wait sleeps until the next
        # poll, not in 0-length chunks chasing a stale tick target. A
        # 0.05s wait with a 0.01s poll needs a handful of sleeps; the old
        # pre-warning loop made thousands.
        sleep_guild = FakeGuild([], [lobby])
        sleep_calls = []
        real_sleep = asyncio.sleep

        async def counting_sleep(delay):
            sleep_calls.append(delay)
            return await real_sleep(delay)

        asyncio.sleep = counting_sleep
        try:
            result = asyncio.run(
                wait_for_lobby(
                    sleep_guild,
                    [{"id": "1"}],
                    RecordingSender(),
                    lambda: False,
                    timeout_seconds=0.05,
                    poll_seconds=0.01,
                    tick_seconds=0.001,
                    warning_seconds=0.001,
                )
            )
        finally:
            asyncio.sleep = real_sleep
        assert result is False
        assert len(sleep_calls) <= 15, (
            f"lobby wait must sleep between polls, not busy-spin "
            f"({len(sleep_calls)} sleeps)"
        )

        # Warning send failure (issue #248): no retry, no double-send, and
        # the wait still reaches its timeout with the cancellation outcome.
        boom_guild = FakeGuild([], [lobby])
        boom_calls = []

        async def boom_send(msg):
            boom_calls.append(msg)
            raise discord.HTTPException(
                types.SimpleNamespace(status=500, reason="Server Error"),
                "boom",
            )

        result = asyncio.run(
            wait_for_lobby(
                boom_guild,
                [{"id": "1"}],
                boom_send,
                lambda: False,
                timeout_seconds=0.05,
                poll_seconds=0.01,
                tick_seconds=0.001,
                warning_seconds=0.03,
            )
        )
        assert result is False, "send failure must not break the timeout outcome"
        assert len(boom_calls) == 2, "warning must not retry or double-send"
        assert "will be cancelled in" in boom_calls[1], "the warning text itself"

        # Warning message whose edits fail (deleted / HTTP error) must not
        # crash the wait or change the cancellation outcome, and must stop
        # retrying the dead message (issue #248).
        dead_guild = FakeGuild([], [lobby])
        dead_sender = RecordingSender()
        dead_edit_calls = []

        async def dead_edit(self, **kwargs):
            dead_edit_calls.append(kwargs)
            raise discord.HTTPException(
                types.SimpleNamespace(status=404, reason="Not Found"),
                "Unknown Message",
            )

        original_edit = FakeMessage.edit
        FakeMessage.edit = dead_edit
        try:
            result, dead_sender = run_wait(dead_guild, [{"id": "1"}])
        finally:
            FakeMessage.edit = original_edit
        assert result is False, "edit failure must not break the timeout outcome"
        assert len(dead_sender.messages) == 2, "no extra warning after edit failure"
        assert len(dead_edit_calls) == 1, (
            "a dead countdown message must not be retried every tick "
            f"({len(dead_edit_calls)} attempts)"
        )

        # Case/whitespace-insensitive detection for all three channels.
        weird_lobby = FakeChannel(20, "  LOBBY ")
        guild = FakeGuild([FakeMember(1, weird_lobby)], [weird_lobby])
        assert missing_lobby_players(guild, [{"id": "1"}]) == []
        weird_atk = FakeChannel(21, "attackers")
        weird_def = FakeChannel(22, "DEFENDERS ")
        guild = FakeGuild([], [weird_atk, weird_def])
        m1, m2 = FakeMemberMovable(1, lobby), FakeMemberMovable(2, lobby)
        guild._members = {1: m1, 2: m2}
        asyncio.run(move_teams_to_voice(guild, [{"id": "1"}], [{"id": "2"}]))
        assert m1.moves == [weird_atk], "case-insensitive Attackers lookup failed"
        assert m2.moves == [weird_def], "case-insensitive Defenders lookup failed"
        assert guild.created == [], "existing channels must not be recreated"

        # Detection failure: guild exposes no voice_channels -> fail open
        # (skip the wait) instead of hanging or cancelling the match.
        class GuildWithBrokenChannels:
            @property
            def voice_channels(self):
                raise RuntimeError("cache unavailable")

        assert (
            missing_lobby_players(GuildWithBrokenChannels(), [{"id": "1"}]) == []
        ), "uninspectable voice channels must not be reported as missing"
        assert asyncio.run(
            wait_for_lobby(
                GuildWithBrokenChannels(),
                [{"id": "1"}],
                send,
                lambda: False,
                poll_seconds=0.01,
            )
        ), "wait_for_lobby must fail open when channels are uninspectable"
        asyncio.run(move_teams_to_voice(GuildWithBrokenChannels(), [{"id": "1"}], []))

        # No voice channels at all -> nothing to wait for; team channels are
        # created on first use.
        empty_guild = FakeGuild([], [])
        assert missing_lobby_players(empty_guild, [{"id": "1"}]) == []
        assert asyncio.run(
            wait_for_lobby(empty_guild, [{"id": "1"}], send, lambda: False)
        )
        asyncio.run(move_teams_to_voice(empty_guild, [{"id": "1"}], []))
        assert sorted(empty_guild.created) == ["Attackers", "Defenders"]

        # Malformed queue/team entries and missing members are skipped
        # without crashing the presence check or the team move.
        malformed = [{"nope": "x"}, "not-a-dict", {"id": "abc"}, {"id": "1"}]
        guild = FakeGuild([FakeMember(1, lobby)], [lobby])
        assert missing_lobby_players(guild, malformed) == []
        guild = FakeGuild([], [lobby])
        asyncio.run(move_teams_to_voice(guild, malformed, [{"id": "404"}]))
        assert sorted(guild.created) == ["Attackers", "Defenders"]

        # Team-channel creation failure is logged and skipped; other
        # permissions failures must not raise out of the move.
        class GuildCreateForbidden(FakeGuild):
            async def create_voice_channel(self, name):
                raise discord.Forbidden(
                    types.SimpleNamespace(status=403, reason="Forbidden"), "nope"
                )

        guild = GuildCreateForbidden([], [FakeChannel(30, "Attackers")])
        asyncio.run(move_teams_to_voice(guild, [], [{"id": "1"}]))
        assert guild.created == [], "failed channel creation must not be recorded"

        # A member whose voice state raises is skipped, not fatal, and is
        # not reported as missing (inspection error must never block a match).
        class BrokenVoiceMember(FakeMember):
            @property
            def voice(self):
                raise RuntimeError("voice state unavailable")

        class GuildBrokenMember(FakeGuild):
            def get_member(self, uid):
                return BrokenVoiceMember(uid)

        guild = GuildBrokenMember([], [lobby])
        assert missing_lobby_players(guild, [{"id": "1"}]) == []
        asyncio.run(move_teams_to_voice(guild, [{"id": "1"}], []))
    finally:
        globals.BOT_FEATURES = original

    print("all voice presence self-checks passed")


if __name__ == "__main__":
    demo()
