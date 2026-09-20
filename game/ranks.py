"""MMR rank tiers and their persistent Discord roles (issue #159).

Players with zero matches this season have no rank role; everyone who has
played at least one match gets their traditional MMR tier role, and the rank
1 player additionally carries the Supersonic Radiant role on top of it. Roles
are found by name and created automatically with the tier color when missing.
`!newseason` strips them all.
"""

import logging

import discord

log = logging.getLogger(__name__)

# (threshold, role name, color hex) — checked high-to-low.
RANKS = [
    (750, "Mother-Ducker Rank", "#f6e62f"),
    (500, "Duck-Master Rank", "#a376ec"),
    (400, "Diamond Rank", "#33ebcb"),
    (300, "Gold Rank", "#fdff75"),
    (200, "Iron Rank", "#ffffff"),
    (100, "Stone Rank", "#989898"),
    (0, "Wood Rank", "#866526"),
]

SSR_NAME = "Supersonic Radiant"
SSR_COLOR = "#fb36f5"


def rank_of(mmr: int) -> str | None:
    """Traditional MMR tier name for an MMR value (never Supersonic Radiant)."""
    if mmr < 0:
        return None
    for threshold, name, _ in RANKS:
        if mmr >= threshold:
            return name
    return None


def tiers_for_player(mmr: int, *, position: int, matches_played: int) -> list[str]:
    """Every rank tier role a player should hold, traditional tier first.

    The rank 1 player wears Supersonic Radiant *in addition to* their
    traditional MMR tier. Unplayed players have no rank role, so they hold no
    tiers either (issue #159).
    """
    if matches_played <= 0:
        return []
    tiers = []
    tier = rank_of(mmr)
    if tier:
        tiers.append(tier)
    if position == 1:
        tiers.append(SSR_NAME)
    return tiers


def tier_for_player(mmr: int, *, matches_played: int) -> str | None:
    """Traditional MMR tier for the !stats display, or None if unplayed.

    Unplayed players have no rank role, so they must show no tier either
    (issue #159). A rank 1 player's Supersonic Radiant status is shown
    separately by the rank line and worn as an extra role on top of this tier.
    """
    if matches_played <= 0:
        return None
    return rank_of(mmr)


def role_mention(guild: discord.Guild, name: str) -> str:
    """Mention text for a rank role by name, falling back to the raw name."""
    role = discord.utils.get(guild.roles, name=name) if guild else None
    return role.mention if role else f"@{name}"


def display_rank_for(
    guild: discord.Guild, mmr: int, *, matches_played: int, mention: bool = True
) -> str:
    """Rank-role mention for display, or plaintext 'Unranked' (issue #212).

    Replaces raw MMR on the team displays: match setup summary, captains
    draft, and the Duck Coin betting embed. Unplayed players have no rank
    role, so they show 'Unranked' in plaintext.

    Pass `mention=False` for surfaces that cannot render a mention as a pill
    (select-menu option labels are plain text), so they show the tier name
    instead of a literal `<@&id>`.
    """
    tier = rank_of(mmr) if matches_played > 0 else None
    if tier is None:
        return "Unranked"
    return role_mention(guild, tier) if mention else tier


async def _role_for(guild: discord.Guild, name: str, color_hex: str):
    """Find a role by name, creating it with the tier color if missing."""
    role = discord.utils.get(guild.roles, name=name)
    if role is not None:
        return role
    try:
        return await guild.create_role(
            name=name, color=discord.Colour.from_str(color_hex)
        )
    except (discord.Forbidden, discord.HTTPException) as e:
        log.warning("Could not create role %s: %s", name, e)
        return None


async def sync_player_rank(
    bot, guild: discord.Guild, discord_id: str, mmr: int, is_rank_one: bool = False
) -> None:
    """Ensure the member holds exactly the roles their standing calls for.

    The traditional MMR tier always follows the player's MMR, and the rank 1
    player additionally wears Supersonic Radiant on top of it rather than
    instead of it.
    """
    member = guild.get_member(int(discord_id))
    if member is None:
        return

    tier_names = {name for _, name, _ in RANKS} | {SSR_NAME}
    target = rank_of(mmr)

    # Remove tier roles the player no longer deserves. Supersonic Radiant is
    # kept while the member is still rank 1 and stripped otherwise; the
    # traditional tier is kept only while it matches the current MMR.
    for role in member.roles:
        if role.name not in tier_names:
            continue
        if role.name == SSR_NAME:
            if is_rank_one:
                continue
        elif role.name == target:
            continue
        try:
            await member.remove_roles(role)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            log.warning("Could not remove %s from %s: %s", role.name, discord_id, e)

    if target is not None:
        color = next((c for _, n, c in RANKS if n == target), None)
        role = await _role_for(guild, target, color)
        if role is not None and role not in member.roles:
            try:
                await member.add_roles(role)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                log.warning("Could not grant %s to %s: %s", target, discord_id, e)

    # Supersonic Radiant for the rank 1 player, stacked on their tier role.
    if is_rank_one:
        role = await _role_for(guild, SSR_NAME, SSR_COLOR)
        if role is not None and role not in member.roles:
            try:
                await member.add_roles(role)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                log.warning("Could not grant %s to %s: %s", SSR_NAME, discord_id, e)


async def remove_all_rank_roles(guild: discord.Guild) -> None:
    """Strip every rank role from all members (season reset)."""
    tier_names = {name for _, name, _ in RANKS} | {SSR_NAME}
    for role in list(guild.roles):
        if role.name not in tier_names:
            continue
        try:
            for member in list(role.members):
                try:
                    await member.remove_roles(role)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
        except (discord.Forbidden, discord.HTTPException) as e:
            log.warning("Could not clear %s: %s", role.name, e)
