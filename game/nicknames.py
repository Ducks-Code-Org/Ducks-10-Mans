"""Managed nicknames: `<rank icon> <Riot ID>` for linked players (issue #268).

Every nickname rule lives here so the link, report, join, purge, season-reset
and backfill hooks cannot drift. The `managed_nicknames` flag is read on each
call, so a live configuration change applies on the next sync. The bot never
reverts a manual edit; it only writes on the events listed above.
"""

import logging

import discord

from database import users
from game.identity import ensure_current_riot_identity
from game.ranking import has_played, position_of
from game.ranks import SSR_NAME, rank_of
from globals import feature_enabled

log = logging.getLogger(__name__)

# Discord caps server nicknames at 32 characters. Over-length targets are
# skipped rather than truncated so a Riot ID is never mangled.
NICKNAME_LIMIT = 32

# Unicode emoji only: custom server emoji and uploaded images do not render
# in a nickname. Iron uses the emoji-presentation shield (U+1F6E1 U+FE0F).
# Keyed by the same role names game.ranks uses, plus the Supersonic Radiant
# title the rank-1 player wears instead of their tier icon.
RANK_ICONS = {
    "Wood Rank": "🪵",
    "Stone Rank": "🪨",
    "Iron Rank": "🛡️",
    "Gold Rank": "🪙",
    "Diamond Rank": "💎",
    "Duck-Master Rank": "🦆",
    "Mother-Ducker Rank": "🦢",
    SSR_NAME: "🌟",
}


def managed_nicknames_enabled() -> bool:
    """Whether the managed_nicknames feature flag is on (default off)."""
    return feature_enabled("managed_nicknames", default=False)


def _riot_id_for(discord_id) -> str | None:
    """Complete Riot ID for a linked user, or None when the link is incomplete.

    Prefers the canonical display field and falls back to the stored lowercase
    `name#tag`, so existing links keep working without a migration.
    """
    doc = users.find_one({"discord_id": str(discord_id)})
    if not doc:
        return None
    name = (doc.get("name") or "").strip()
    tag = (doc.get("tag") or "").strip()
    if not name or not tag:
        return None
    return (doc.get("riot_display_name") or "").strip() or f"{name}#{tag}"


def _rank_icon(bot, discord_id) -> str | None:
    """Icon for the player's current standing, or None when unplayed.

    Reuses the shared "has played" and leaderboard-position helpers so the
    rank-1 icon and the Supersonic Radiant role cannot disagree.
    """
    player_mmr = getattr(bot, "player_mmr", None) or {}
    stats = player_mmr.get(str(discord_id))
    if not stats or not has_played(stats):
        return None
    if position_of(player_mmr, str(discord_id)) == 1:
        return RANK_ICONS[SSR_NAME]
    return RANK_ICONS.get(rank_of(stats.get("mmr", 0)))


def _format_nickname(riot_id: str, icon: str | None) -> str:
    """One space after the icon, no rank wording; icon-less when unplayed."""
    return f"{icon} {riot_id}" if icon else riot_id


def _over_limit(discord_id, target: str, action: str) -> bool:
    """True after logging when a target would exceed Discord's nickname cap."""
    if len(target) <= NICKNAME_LIMIT:
        return False
    log.warning(
        "Nickname %s skipped for %s: %r exceeds %s characters",
        action,
        discord_id,
        target,
        NICKNAME_LIMIT,
    )
    return True


def _member_skip_reason(guild, member_id) -> tuple[object, str | None]:
    """The guild member, or None plus the reason they must not be renamed."""
    try:
        member = guild.get_member(int(member_id))
    except (TypeError, ValueError):
        return None, "invalid Discord id"
    if member is None:
        return None, "member not in guild"
    if member.bot:
        return None, "bot account"
    if guild.owner_id == member.id:
        return None, "server owner"
    bot_member = getattr(guild, "me", None)
    if bot_member is None:
        return None, "bot member not cached"
    if member.top_role >= bot_member.top_role:
        return None, "role above the bot"
    return member, None


def _editable_member(guild, discord_id, action: str):
    """The member when a nickname write is allowed, logging every refusal."""
    member, reason = _member_skip_reason(guild, discord_id)
    if member is not None:
        return member
    log_skip = log.warning if reason == "role above the bot" else log.debug
    log_skip("Nickname %s skipped for %s: %s", action, discord_id, reason)
    return None


async def sync_member_nickname(bot, guild, discord_id) -> str:
    """Apply the managed nickname to one linked member.

    Returns `updated`, `unchanged`, `skipped`, or `failed`. Expected Discord
    permission/HTTP failures are logged and returned, never raised.
    """
    if not managed_nicknames_enabled() or guild is None:
        return "skipped"

    member = _editable_member(guild, discord_id, "sync")
    if member is None:
        return "skipped"

    riot_id = _riot_id_for(discord_id)
    if riot_id is None:
        log.debug("Nickname sync skipped for %s: incomplete Riot link", discord_id)
        return "skipped"

    target = _format_nickname(riot_id, _rank_icon(bot, discord_id))
    if _over_limit(discord_id, target, "sync"):
        return "skipped"
    if member.nick == target:
        return "unchanged"

    try:
        await member.edit(nick=target)
    except (discord.Forbidden, discord.NotFound, discord.HTTPException) as e:
        log.warning("Nickname sync failed for %s: %s", discord_id, e)
        return "failed"
    log.info("Set nickname for %s to %r", discord_id, target)
    return "updated"


async def refresh_signup_nickname(bot, guild, discord_id) -> tuple[bool, str]:
    """Refresh a button signup's identity, then its nickname (issue #268).

    The `/signup` host already runs the puuid-based identity refresh; the
    other nine join from the button, which otherwise only checks that the
    stored Riot ID exists. With the flag on, follow a Riot ID rename and fill
    canonical casing before the nickname write. Returns `(ok, message)`; a
    nickname failure never fails the signup.
    """
    ok, message, doc = await ensure_current_riot_identity(discord_id)
    if not ok:
        return False, message

    doc = doc or {}
    name = (doc.get("name") or "").lower().strip()
    tag = (doc.get("tag") or "").lower().strip()
    linked = users.find_one({"name": name, "tag": tag})
    if not linked or str(linked.get("discord_id")) != str(discord_id):
        return False, (
            "Your Riot ID is linked to a different Discord account, or was "
            "changed after another user linked it. Please re-link it using "
            "`/linkriot <Name#Tag>`."
        )

    try:
        await sync_member_nickname(bot, guild, discord_id)
    except Exception as e:
        log.error("Nickname sync failed for %s: %s", discord_id, e, exc_info=e)
    return True, ""


async def sync_guild_nicknames(bot, guild) -> dict[str, int]:
    """Sync every current human member; counts for the backfill summary."""
    counts = {"updated": 0, "unchanged": 0, "skipped": 0, "failed": 0}
    if guild is None:
        return counts
    for member in list(getattr(guild, "members", [])):
        if member.bot:
            continue
        counts[await sync_member_nickname(bot, guild, member.id)] += 1
    return counts


async def clear_member_nickname(guild, discord_id) -> str:
    """Clear a member's nickname on unlink. Returns `cleared`/`unchanged`/...

    Flag-gated: disabling managed nicknames leaves existing names alone.
    """
    if not managed_nicknames_enabled() or guild is None:
        return "skipped"

    member = _editable_member(guild, discord_id, "clear")
    if member is None:
        return "skipped"
    if not member.nick:
        return "unchanged"
    # Only clear a name that still carries the Riot ID the bot would have
    # written; a manual admin rename survives the unlink (issue #268).
    riot_id = _riot_id_for(discord_id)
    if riot_id is None or not member.nick.casefold().endswith(riot_id.casefold()):
        log.info("Nickname clear skipped for %s: not the managed name", discord_id)
        return "unchanged"

    try:
        await member.edit(nick=None)
    except (discord.Forbidden, discord.NotFound, discord.HTTPException) as e:
        log.warning("Nickname clear failed for %s: %s", discord_id, e)
        return "failed"
    log.info("Cleared nickname for %s", discord_id)
    return "cleared"


async def strip_rank_icons(guild) -> int:
    """Rewrite linked members' nicknames to the Riot ID only (season reset).

    Returns the number of members updated. Flag-gated like every other write.
    """
    if not managed_nicknames_enabled() or guild is None:
        return 0

    updated = 0
    for member in list(getattr(guild, "members", [])):
        member = _editable_member(guild, member.id, "rank strip")
        if member is None:
            continue
        riot_id = _riot_id_for(member.id)
        if riot_id is None or member.nick == riot_id:
            continue
        if _over_limit(member.id, riot_id, "rank strip"):
            continue
        try:
            await member.edit(nick=riot_id)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as e:
            log.warning("Rank icon strip failed for %s: %s", member.id, e)
            continue
        updated += 1
    return updated
