"""MMR rank tiers and their persistent Discord roles (issue #159).

Players with zero matches this season have no rank role; everyone who has
played at least one match gets exactly one. Roles are found by name and
created automatically with the tier color when missing. `!newseason` strips
them all.
"""

import discord

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


def rank_of(mmr: int, is_rank_one: bool = False) -> str | None:
    """Role name for an MMR value. Rank 1 overall is always SSR."""
    if is_rank_one:
        return SSR_NAME
    if mmr < 0:
        return None
    for threshold, name, _ in RANKS:
        if mmr >= threshold:
            return name
    return None


def help_menu_text() -> str:
    """Rank tier listing for the !help embed."""
    lines = []
    for threshold, name, color in RANKS:
        lines.append(f"• **{name}** — {threshold}+ ({color})")
    lines.append(f"• **{SSR_NAME}** — Rank 1 ({SSR_COLOR})")
    return "\n".join(lines)


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
        print(f"[ranks] Could not create role {name}: {e}")
        return None


async def sync_player_rank(bot, guild: discord.Guild, discord_id: str, mmr: int, is_rank_one: bool = False) -> None:
    """Ensure the member has exactly the role their MMR calls for."""
    member = guild.get_member(int(discord_id))
    if member is None:
        return

    tier_names = {name for _, name, _ in RANKS} | {SSR_NAME}
    target = rank_of(mmr, is_rank_one)

    for role in member.roles:
        if role.name in tier_names and (target is None or role.name != target):
            try:
                await member.remove_roles(role)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                print(f"[ranks] Could not remove {role.name} from {discord_id}: {e}")

    if target is None:
        return

    color = next((c for _, n, c in RANKS if n == target), SSR_COLOR)
    role = await _role_for(guild, target, color)
    if role is not None and role not in member.roles:
        try:
            await member.add_roles(role)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            print(f"[ranks] Could not grant {target} to {discord_id}: {e}")


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
            print(f"[ranks] Could not clear {role.name}: {e}")