"""Voice-channel presence helpers behind the `voice_presence` feature flag.

When the flag is enabled:
- A full signup queue waits for every player to join the #lobby voice
  channel before match setup starts; the match auto-cancels after 10
  minutes if someone never joins.
- When the match is set to start, players already connected to a voice
  channel are moved to their team channel (Attackers/Defenders).

Channel lookup is case-insensitive and whitespace-tolerant. Every helper
fails open: when voice state or channels cannot be inspected (no guild,
missing channels, cache/API errors), the match continues rather than
hanging or being cancelled.
"""

import asyncio
import logging

import discord

from globals import feature_enabled

log = logging.getLogger(__name__)

LOBBY_CHANNEL_NAME = "lobby"
ATTACKERS_CHANNEL_NAME = "Attackers"
DEFENDERS_CHANNEL_NAME = "Defenders"
LOBBY_WAIT_SECONDS = 600  # 10 minutes to join the lobby
POLL_SECONDS = 15

# Sentinel: voice state could not be inspected, so presence is unknown.
_UNKNOWN = object()


def voice_presence_enabled() -> bool:
    """Whether the voice_presence [features] flag in bot.ini is on."""
    return feature_enabled("voice_presence", default=False)


def _find_channel(channels, name):
    """Find a channel by name, case-insensitively and whitespace-tolerantly."""
    if not channels or not isinstance(name, str):
        return None
    target = name.strip().casefold()
    for channel in channels:
        channel_name = getattr(channel, "name", None)
        if isinstance(channel_name, str) and channel_name.strip().casefold() == target:
            return channel
    return None


def _guild_voice_channels(guild):
    """The guild's voice channels, or None when they can't be inspected."""
    if guild is None:
        return None
    try:
        return list(guild.voice_channels)
    except Exception as e:  # fail open: presence checks must never break setup
        log.warning("Could not list voice channels: %r", e)
        return None


def _player_id(player) -> int | None:
    """Parse a queue entry's discord id, or None when the entry is malformed."""
    if not isinstance(player, dict):
        return None
    try:
        return int(player.get("id"))
    except (TypeError, ValueError):
        return None


def _voice_channel_of(guild, player_id: int):
    """Channel a player is connected to, None if not connected, or _UNKNOWN.

    _UNKNOWN means the voice state could not be read, which callers must
    not treat as absence (fail open).
    """
    try:
        member = guild.get_member(player_id)
        return member.voice.channel if member and member.voice else None
    except Exception as e:  # fail open: an unreadable voice state is not "absent"
        log.warning("Could not read voice state for %s: %r", player_id, e)
        return _UNKNOWN


def missing_lobby_players(guild, queue) -> list[str]:
    """Queued player ids not connected to the lobby voice channel.

    Returns [] (nobody missing) when voice state can't be inspected, and
    falls back to "any voice channel" when the guild has no #lobby.
    """
    channels = _guild_voice_channels(guild)
    if not channels:
        log.info("No inspectable voice channels; skipping presence check")
        return []
    lobby = _find_channel(channels, LOBBY_CHANNEL_NAME)

    missing = []
    for player in queue or []:
        player_id = _player_id(player)
        if player_id is None:
            log.warning("Skipping malformed queue entry: %r", player)
            continue
        channel = _voice_channel_of(guild, player_id)
        if channel is _UNKNOWN:
            continue  # can't tell; never block a match on an inspection error
        if channel is None or (lobby is not None and channel.id != lobby.id):
            missing.append(str(player_id))
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

    Returns True once everyone is connected, or when presence cannot be
    checked at all. Returns False if the wait timed out or setup was
    cancelled (e.g. !cancel). Progress messages go through `send`.
    """
    channels = _guild_voice_channels(guild)
    if not channels:
        log.info("No voice channels to monitor; skipping lobby wait")
        return True

    missing = missing_lobby_players(guild, queue)
    if not missing:
        return True

    log.info(
        "Waiting up to %ss for %s player(s) to join the lobby",
        timeout_seconds,
        len(missing),
    )
    room = (
        "**#lobby**"
        if _find_channel(channels, LOBBY_CHANNEL_NAME) is not None
        else "a **voice channel**"
    )
    try:
        await send(
            f"Waiting for everyone to join {room} before match setup: "
            + " ".join(f"<@{pid}>" for pid in missing)
            + f" — you have {timeout_seconds // 60} minutes or the match is cancelled."
        )
    except discord.HTTPException:
        pass

    deadline = asyncio.get_event_loop().time() + timeout_seconds
    while True:
        await asyncio.sleep(poll_seconds)
        if is_cancelled():
            return False
        if asyncio.get_event_loop().time() >= deadline:
            log.warning("Lobby wait timed out with players still missing")
            return False
        missing = missing_lobby_players(guild, queue)
        if not missing:
            log.info("Everyone joined the lobby; starting match setup")
            try:
                await send("Everyone is in the lobby! Starting match setup...")
            except discord.HTTPException:
                pass
            return True


async def move_teams_to_voice(guild, team1, team2) -> None:
    """Move voice-connected players into their team channel at match start.

    Team channels are matched case-insensitively or created once and then
    persist between matches. Missing permissions, API errors and malformed
    queue entries are logged and skipped so match start never crashes.
    """
    channels = _guild_voice_channels(guild)
    if channels is None:
        log.warning("Could not inspect voice channels; skipping team move")
        return

    log.info("Moving teams into their voice channels")
    for name, team in (
        (ATTACKERS_CHANNEL_NAME, team1),
        (DEFENDERS_CHANNEL_NAME, team2),
    ):
        channel = _find_channel(channels, name)
        if channel is None:
            try:
                channel = await guild.create_voice_channel(name)
                channels.append(channel)
                log.info("Created '%s' voice channel", name)
            except (discord.HTTPException, asyncio.TimeoutError) as e:
                log.warning("Could not create '%s' voice channel: %s", name, e)
                continue

        for player in team or []:
            player_id = _player_id(player)
            if player_id is None:
                log.warning("Skipping malformed team entry: %r", player)
                continue
            try:
                member = guild.get_member(player_id)
                if not member or not member.voice or member.voice.channel == channel:
                    continue
                await member.move_to(channel)
            except (discord.HTTPException, asyncio.TimeoutError) as e:
                log.warning("Could not move %s to '%s': %s", player_id, name, e)
            except (AttributeError, TypeError) as e:
                log.warning("Could not inspect member %s: %s", player_id, e)
