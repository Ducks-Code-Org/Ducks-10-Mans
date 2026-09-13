"""Voice-channel presence helpers behind the `voice_presence` feature flag.

When the flag is enabled:
- A full signup queue waits for every player to join the #lobby voice
  channel before match setup starts; the match auto-cancels after 10
  minutes if someone never joins.
- When the match is set to start, players already connected to a voice
  channel are moved to their team channel (Attackers/Defenders).
"""

import asyncio

import discord

from globals import feature_enabled

LOBBY_CHANNEL_NAME = "lobby"
ATTACKERS_CHANNEL_NAME = "Attackers"
DEFENDERS_CHANNEL_NAME = "Defenders"
LOBBY_WAIT_SECONDS = 600  # 10 minutes to join the lobby
POLL_SECONDS = 15


def voice_presence_enabled() -> bool:
    """Whether the voice_presence [features] flag in bot.ini is on."""
    return feature_enabled("voice_presence", default=False)


def _find_channel(channels, name):
    return next((c for c in channels if c.name.lower() == name.lower()), None)


def missing_lobby_players(guild, queue) -> list[str]:
    """Queued player ids not connected to the lobby voice channel.

    If the guild has no voice channel named "lobby", any voice
    channel counts as present.
    """
    lobby = _find_channel(guild.voice_channels, LOBBY_CHANNEL_NAME) if guild else None
    missing = []
    for player in queue:
        member = None
        try:
            member = guild.get_member(int(player["id"])) if guild else None
        except (TypeError, ValueError):
            pass
        channel = member.voice.channel if member and member.voice else None
        if channel is None or (lobby is not None and channel.id != lobby.id):
            missing.append(str(player["id"]))
    return missing


async def wait_for_lobby(
    guild,
    queue,
    send,
    is_cancelled,
    timeout_seconds: int = LOBBY_WAIT_SECONDS,
    poll_seconds: float = POLL_SECONDS,
) -> bool:
    """Wait until every queued player has joined the lobby voice channel.

    Returns True once everyone is connected, False if the wait timed out
    or the match setup was cancelled (e.g. !cancel). Progress messages go
    through `send`.
    """
    missing = missing_lobby_players(guild, queue)
    if not missing:
        return True

    try:
        await send(
            "Waiting for everyone to join the **#lobby** voice channel before match "
            "setup: "
            + " ".join(f"<@{pid}>" for pid in missing)
            + f" — you have {timeout_seconds // 60} minutes or the match is cancelled."
        )
    except discord.HTTPException:
        pass

    deadline = asyncio.get_event_loop().time() + timeout_seconds
    while True:
        await asyncio.sleep(poll_seconds)
        if is_cancelled() or asyncio.get_event_loop().time() >= deadline:
            return False
        missing = missing_lobby_players(guild, queue)
        if not missing:
            try:
                await send("Everyone is in the lobby! Starting match setup...")
            except discord.HTTPException:
                pass
            return True


async def move_teams_to_voice(guild, team1, team2) -> None:
    """Move voice-connected players into their team channel at match start.

    Team channels are found by name or created once and persist between
    matches. Players not connected to any voice channel are left alone.
    """
    if guild is None:
        return
    for name, team in (
        (ATTACKERS_CHANNEL_NAME, team1),
        (DEFENDERS_CHANNEL_NAME, team2),
    ):
        channel = _find_channel(guild.voice_channels, name)
        if channel is None:
            try:
                channel = await guild.create_voice_channel(name)
            except (discord.Forbidden, discord.HTTPException):
                continue
        for player in team:
            try:
                member = guild.get_member(int(player["id"]))
            except (TypeError, ValueError):
                continue
            if not member or not member.voice or member.voice.channel == channel:
                continue
            try:
                await member.move_to(channel)
            except (discord.Forbidden, discord.HTTPException):
                pass
