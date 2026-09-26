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
# When this much of the lobby window is left, everyone still missing gets
# pinged with a countdown synced to the auto-cancel deadline (issue #233).
LOBBY_PING_SECONDS = 120

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


def _display_name(guild, player_id) -> str:
    """Plain-text display name for a notice, or the raw id if unavailable.

    Deliberately not a mention — the notice must never ping anyone; the
    final warning is the only nudge (issue #247).
    """
    try:
        member = guild.get_member(int(player_id))
        name = getattr(member, "display_name", None)
    except Exception as e:  # fail open: an unreadable member is not fatal
        log.warning("Could not read display name for %s: %r", player_id, e)
        return str(player_id)
    return name if isinstance(name, str) and name else str(player_id)


def _deadline_text(seconds: float) -> str:
    """Notice deadline text for the wait window actually in use.

    Whole minutes read as words ("10 minutes"); anything else uses the
    m:ss clock so an overridden window never overstates itself.
    """
    minutes = int(seconds // 60)
    if minutes and seconds % 60 == 0:
        return f"{minutes} minute" + ("s" if minutes > 1 else "")
    return f"{minutes}:{int(seconds % 60):02d}"


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

    Messaging is staged (issue #233): the first message only lists who is
    missing and tells them to join — nobody gets pinged right away. When
    the auto-cancel window is down to LOBBY_PING_SECONDS, everyone still
    outside the lobby gets pinged with a countdown synced to the timeout.
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
    lobby = _find_channel(channels, LOBBY_CHANNEL_NAME)
    room = (
        f"the **lobby voice channel** <#{lobby.id}>"
        if lobby is not None
        else "a **voice channel**"
    )
    # List who is missing without pinging them (issue #233); the pinged
    # nudge only comes with the final warning near the timeout. The
    # deadline is derived from the wait window so the wording cannot lie.
    who = ", ".join(_display_name(guild, pid) for pid in missing)
    deadline_text = _deadline_text(timeout_seconds)
    try:
        await send(
            f"Waiting for these players to join {room}: "
            + who
            + f" — you have {deadline_text} to join or the match will be cancelled."
        )
    except discord.HTTPException:
        pass

    deadline = asyncio.get_event_loop().time() + timeout_seconds
    pinged = False
    while True:
        await asyncio.sleep(poll_seconds)
        if is_cancelled():
            return False
        missing = missing_lobby_players(guild, queue)
        if not missing:
            log.info("Everyone joined the lobby; starting match setup")
            try:
                await send("Everyone is in the lobby! Starting match setup...")
            except discord.HTTPException:
                pass
            return True
        if asyncio.get_event_loop().time() >= deadline:
            log.warning("Lobby wait timed out with players still missing")
            return False
        remaining = int(deadline - asyncio.get_event_loop().time())
        if not pinged and remaining <= LOBBY_PING_SECONDS:
            # Final warning: ping everyone still outside the lobby with a
            # countdown matching the auto-cancel window.
            pinged = True
            minutes = max(remaining // 60, 1)
            try:
                await send(
                    "⚠️ Still waiting in the lobby for match setup: "
                    + " ".join(f"<@{pid}>" for pid in missing)
                    + f" — the match is auto-cancelled in about {minutes} minute(s)!"
                )
            except discord.HTTPException:
                pass


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


async def move_players_to_lobby(guild, players) -> None:
    """Move match players back to the lobby voice channel after a report.

    Players who already left voice (or moved themselves elsewhere) are left
    alone: only members still sitting in the Attackers/Defenders team
    channels are moved. Missing permissions and API errors are logged and
    skipped so the report pipeline never breaks on voice cleanup.
    """
    channels = _guild_voice_channels(guild)
    if channels is None:
        log.warning("Could not inspect voice channels; skipping lobby move")
        return

    lobby = _find_channel(channels, LOBBY_CHANNEL_NAME)
    team_channels = {
        channel.id
        for name in (ATTACKERS_CHANNEL_NAME, DEFENDERS_CHANNEL_NAME)
        if (channel := _find_channel(channels, name)) is not None
    }
    if not team_channels:
        log.info("No team voice channels to move players out of")
        return
    if lobby is None:
        # Nowhere to move them to; disconnecting would be worse.
        log.warning("No #lobby voice channel found; skipping lobby move")
        return

    moved = 0
    for player in players or []:
        player_id = _player_id(player)
        if player_id is None:
            log.warning("Skipping malformed player entry: %r", player)
            continue
        try:
            member = guild.get_member(player_id)
            if (
                not member
                or not member.voice
                or member.voice.channel is None
                or member.voice.channel.id not in team_channels
            ):
                # Already left, disconnected, or not in a team channel.
                continue
            if member.voice.channel.id == lobby.id:
                continue
            await member.move_to(lobby)
            moved += 1
        except (discord.HTTPException, asyncio.TimeoutError) as e:
            log.warning("Could not move %s back to the lobby: %s", player_id, e)
        except (AttributeError, TypeError) as e:
            log.warning("Could not inspect member %s: %s", player_id, e)
    if moved:
        log.info("Moved %s player(s) back to the lobby after the report", moved)
