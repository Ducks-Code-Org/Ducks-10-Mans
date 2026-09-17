"Admin maintenance commands (issue #166): rollback, editplayer, substitute, and friends."

import asyncio
import contextlib
import gzip
import inspect
import io
import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import discord
from discord.ext import commands

import globals as globals_mod
from commands import BotCommands
from database import all_matches, client, mmr_collection, seasons, users
from globals import BOT_CONFIG
from game.duck_coins import (
    DOUBLEDOWN_COST,
    add_coins,
    clear_season_coin_state,
    coins_of,
    duck_coins_enabled,
    duck_emote,
)
from services.riot_api import (
    RiotApiInconclusive,
    get_account_by_riot_id,
    get_match_by_id_async,
    verify_riot_account_async,
)
from game.stats_helper import DEFAULT_MMR, update_stats
from services.vlr_rating import estimate_ratings_v4
from game.voice_presence import move_teams_to_voice, voice_presence_enabled

log = logging.getLogger(__name__)


async def setup(bot):
    # Shared with commands/report.py: whichever cog loads first creates it.
    if not hasattr(bot, "report_lock"):
        bot.report_lock = asyncio.Lock()
    await bot.add_cog(MaintenanceCommands(bot))


BOT_INI_PATH = Path(globals_mod.__file__).parent / "bot.ini"

EDITABLE_FIELDS = {"mmr", "wins", "losses", "riot"}
_MENTION_RE = re.compile(r"<@!?(\d+)>$")


def _season_match_filter(season_num: int) -> dict:
    """Matches belonging to a season. !report stores the raw API payload, so
    most match docs have no season_number field; those belong to the current
    (only active) season."""
    return {
        "$or": [{"season_number": season_num}, {"season_number": {"$exists": False}}]
    }


# Conservative upload budget for !snapshotseason attachments: Discord caps
# message uploads (10 MiB non-boosted), so stay under it after compression.
SNAPSHOT_UPLOAD_LIMIT = 8 * 1024 * 1024


def _chunk_embed_lines(lines: list[str], limit: int = 1000) -> list[str]:
    """Split command-list lines into embed-field-sized chunks.

    Discord rejects any embed field whose value exceeds 1024 characters
    (error 50035), so a long command list must be spread across fields.
    Each returned chunk is a newline join of whole lines.
    """
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for line in lines:
        extra = len(line) + (1 if current else 0)  # +1 for the joining newline
        if current and length + extra > limit:
            chunks.append("\n".join(current))
            current = []
            length = 0
            extra = len(line)
        current.append(line)
        length += extra
    if current:
        chunks.append("\n".join(current))
    return chunks or ["—"]


# Curated help for the !adminhelp embed: (usage arguments, description of 10
# words max) per admin-gated command, keyed by command name, grouped by the
# section they render under. A command missing from this map falls back to a
# signature-derived usage line and its docstring's first line truncated to 10
# words, so new commands still show up reasonably until added here.
ADMIN_COMMAND_HELP: dict[str, tuple[str, str, str]] = {
    # section, usage args, short description
    "newseason": ("Season", "[noreset]", "Start a new season and crown the SSR winner"),
    "resetseason": ("Season", "confirm", "Wipe everyone's current-season stats"),
    "resetplayer": ("Season", "<@user|Name#Tag>", "Reset one player's season stats"),
    "snapshotseason": ("Season", "[full]", "Export season data as a .json.gz"),
    "recoverseason": (
        "Season",
        "confirm",
        "Restore season data from an attached snapshot",
    ),
    "rollback": ("Match", "", "Undo the most recent reported match"),
    "cancel": ("Match", "", "Cancel the active signup or match"),
    "substitute": ("Match", "<@out> <@in>", "Swap a substitute into the current match"),
    "fixmap": ("Match", "<map>", "Force-set the current match's map"),
    "forcereport": ("Match", "<match-id-or-URL>", "Report a specific match by id"),
    "matchinfo": ("Match", "", "Dump internal match and queue state"),
    "editplayer": (
        "Players",
        "<@user|Name#Tag> <mmr|wins|losses|riot> <value>",
        "Edit a player's stats or linked Riot ID",
    ),
    "addcoins": (
        "Players",
        "<@user|Name#Tag> <amount>",
        "Grant or remove a player's Duck Coins",
    ),
    "setconfig": ("Config", "<key> <value>", "Update a bot.ini feature flag live"),
    "showconfig": ("Config", "", "Show current bot.ini feature flags"),
    "toggledev": ("Config", "", "Toggle developer mode (switches the prefix)"),
    "simulate_queue": ("Config", "", "Fill the queue with 10 fake players"),
    "initialize_rounds": ("Config", "", "Zero every player's total rounds played"),
}


def _usage_args_of(cmd) -> str:
    """Usage arguments for a command: curated hint or signature-derived."""
    curated = ADMIN_COMMAND_HELP.get(cmd.name)
    if curated is not None:
        return curated[1]
    params = []
    for name, param in cmd.clean_params.items():
        if param.kind == inspect.Parameter.VAR_POSITIONAL:
            params.append(f"<{name}...>")
        elif param.default is inspect.Parameter.empty:
            params.append(f"<{name}>")
        else:
            params.append(f"[{name}]")
    return " ".join(params)


def _short_desc_of(cmd, max_words: int = 10) -> str:
    """A 10-words-max description: curated hint or docstring's first line."""
    curated = ADMIN_COMMAND_HELP.get(cmd.name)
    if curated is not None:
        return curated[2]
    doc = (cmd.help or "").strip()
    first = doc.splitlines()[0] if doc else ""
    words = first.split()
    if len(words) > max_words:
        return " ".join(words[:max_words]) + "..."
    return first or "—"


# Season stat fields wiped by !resetplayer / !resetseason (matches the
# new-season reset in bot.py). Duck Coins are per-season, so they reset too.
SEASON_STAT_DEFAULTS = {
    "mmr": DEFAULT_MMR,
    "wins": 0,
    "losses": 0,
    "total_combat_score": 0,
    "total_kills": 0,
    "total_deaths": 0,
    "matches_played": 0,
    "total_rounds_played": 0,
    "average_combat_score": 0,
    "kill_death_ratio": 0,
    "total_rating_points": 0.0,
    "total_rating_rounds": 0,
    "avg_rating": None,
    "previous_rank": None,
    "current_rank": None,
    "duck_coins": 0,
}


def resolve_user_arg(arg: str, guild=None) -> str | None:
    """Discord id for a @mention, linked Name#Tag, or guild display name."""
    arg = (arg or "").strip()
    m = _MENTION_RE.match(arg)
    if m:
        return m.group(1)
    if "#" in arg:
        name, tag = arg.rsplit("#", 1)
        u = users.find_one({"name": name.lower().strip(), "tag": tag.lower().strip()})
        return str(u["discord_id"]) if u else None
    if guild is not None:
        target = arg.casefold()
        for member in guild.members:
            if (
                member.display_name.casefold() == target
                or member.name.casefold() == target
            ):
                return str(member.id)
    return None


def parse_edit_args(args: str):
    """Split '!editplayer <user> <field> <value...>' allowing spaces in user/value.

    Scans for the first token that is a known field name; everything before it
    is the player (Riot IDs may contain spaces), everything after is the value.
    Returns (user_arg, field, value) or (None, None, None).
    """
    tokens = (args or "").split()
    for i, tok in enumerate(tokens):
        if tok.lower() in EDITABLE_FIELDS:
            return " ".join(tokens[:i]), tok.lower(), " ".join(tokens[i + 1 :])
    return None, None, None


def set_ini_value(path, section: str, key: str, value: str) -> None:
    """Update one key in an ini file, preserving comments, order, and sections."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    out = []
    in_section = False
    seen_section = False
    replaced = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_section and not replaced:
                out.append(f"{key} = {value}")
                replaced = True
            in_section = stripped.lower() == f"[{section.lower()}]"
            seen_section = seen_section or in_section
        elif in_section and re.fullmatch(
            rf"{re.escape(key)}\s*=.*", stripped, re.IGNORECASE
        ):
            out.append(f"{key} = {value}")
            replaced = True
            continue
        out.append(line)
    if not seen_section:
        out.append(f"[{section}]")
    if not replaced:
        out.append(f"{key} = {value}")
    Path(path).write_text("\n".join(out) + "\n", encoding="utf-8")


def sync_ranks(bot) -> None:
    """Recompute previous/current leaderboard ranks for every cached player.

    Mirrors the rank snapshot report.py writes after each match; used after an
    admin edits someone's MMR so the leaderboard's rank column stays truthful.
    """
    played = {
        pid
        for pid, s in bot.player_mmr.items()
        if s.get("matches_played", 0) > 0 or (s.get("wins", 0) + s.get("losses", 0)) > 0
    }
    ranks = {
        pid: rank
        for rank, (pid, _) in enumerate(
            sorted(
                ((pid, s) for pid, s in bot.player_mmr.items() if pid in played),
                key=lambda x: x[1].get("mmr", 0),
                reverse=True,
            ),
            start=1,
        )
    }
    for pid in bot.player_mmr:
        mmr_collection.update_one(
            {"player_id": pid},
            {"$set": {"previous_rank": ranks.get(pid), "current_rank": ranks.get(pid)}},
            upsert=True,
        )


_TRACKER_URL_RE = re.compile(
    r"tracker\.gg/valorant/match/([0-9a-fA-F-]{36})", re.IGNORECASE
)
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")


def _extract_match_id(ref: str) -> str | None:
    """Match id from a bare id or a tracker.gg match URL."""
    ref = (ref or "").strip()
    m = _TRACKER_URL_RE.search(ref)
    if m:
        return m.group(1).lower()
    if _UUID_RE.fullmatch(ref):
        return ref.lower()
    return None


def rounds_to_int(value: object) -> int:
    """Best-effort conversion of an API rounds field to a non-negative int."""
    if isinstance(value, dict):
        for key in ("won", "w", "value", "wins", "count"):
            v = value.get(key)
            if isinstance(v, (int, float, str)):
                try:
                    return int(v)
                except Exception:
                    pass
        numeric_vals = [v for v in value.values() if isinstance(v, (int, float))]
        return int(max(numeric_vals)) if numeric_vals else 0
    if isinstance(value, (list, tuple)):
        if value:
            try:
                return int(value[0])
            except Exception:
                return 0
        return 0
    try:
        return int(value)
    except Exception:
        return 0


def resolve_match_players(players) -> tuple[dict[int, str], list[str]]:
    """Map each API player to a linked Discord id.

    Returns ({id(player): discord_id}, [unlinked "Name#Tag", ...]). Keyed by
    the player object's identity, not its name: two players sharing a game
    name but different tags must not collide.
    """
    pid_of: dict[int, str] = {}
    unlinked: list[str] = []
    for p in players:
        u = users.find_one({"puuid": (p.get("puuid") or "").strip().lower()})
        if u is None:
            u = users.find_one(
                {
                    "name": (p.get("name") or "").lower().strip(),
                    "tag": (p.get("tag") or "").strip().lower(),
                }
            )
        if u is None:
            unlinked.append(f"{p.get('name')}#{p.get('tag')}")
        else:
            pid_of[id(p)] = str(u["discord_id"])
    return pid_of, unlinked


class MaintenanceCommands(BotCommands):
    @commands.command(name="rollback")
    @commands.has_permissions(administrator=True)
    async def rollback(self, ctx):
        """Revert the database to a snapshot from before the most recent match."""
        log.info("Rollback requested by %s", ctx.author)
        warning = (
            "⚠️ A match is currently active; rolling back the last *reported* match.\n"
            if self.bot.match_ongoing
            else ""
        )
        buf = io.StringIO()
        error = None
        async with self.bot.report_lock:
            # Reuses the tools.ops revert script: it snapshots every affected
            # document to backups/ before writing, so a backup file is always
            # produced even if the revert itself fails midway.
            with contextlib.redirect_stdout(buf):
                try:
                    from tools.ops.revert_last_match import revert

                    revert(client, dry_run=False)
                except SystemExit as e:
                    error = str(e.code or "rollback aborted")
                except Exception as e:
                    error = f"{type(e).__name__}: {e}"
        out = buf.getvalue().strip()
        if error:
            msg = f"Rollback failed: {error}"
            log.error("Rollback failed: %s", error)
            if out:
                msg += f"\n```\n{out[-1500:]}\n```"
            await ctx.send(msg)
            return
        log.info("Rollback completed by %s", ctx.author)
        self.bot.load_mmr_data()
        await ctx.send(
            f"{warning}Rolled back the most recent match and resynced stats.```\n{out[-1700:]}\n```"
        )

    @commands.command(name="editplayer")
    @commands.has_permissions(administrator=True)
    async def editplayer(self, ctx, *, args: str = ""):
        """
        Edit a player's stats or linked Riot ID.
        Usage: !editplayer <@user|Name#Tag> <mmr|wins|losses|riot> <value>
        e.g. !editplayer @Pyr mmr 1500
             !editplayer @Pyr riot New Name#TAG
        """
        user_arg, field, value = parse_edit_args(args)
        if not field or not value:
            await ctx.send(
                "Usage: `!editplayer <@user|Name#Tag> <mmr|wins|losses|riot> <value>`"
            )
            return
        pid = resolve_user_arg(user_arg, ctx.guild)
        if not pid:
            await ctx.send(
                f"Could not resolve player `{user_arg}` — use an @mention or a linked `Name#Tag`."
            )
            return

        if field == "riot":
            await self._relink_riot(ctx, pid, value)
            return

        try:
            amount = int(value)
        except ValueError:
            await ctx.send(f"`{value}` must be a whole number.")
            return
        if amount < 0:
            await ctx.send("Value must be zero or positive.")
            return

        if pid in self.bot.player_mmr:
            self.bot.player_mmr[pid][field] = amount
        mmr_collection.update_one(
            {"player_id": pid}, {"$set": {field: amount}}, upsert=True
        )
        if field == "mmr":
            sync_ranks(self.bot)
        log.info("%s set %s = %s for %s", ctx.author, field, amount, pid)
        await ctx.send(f"Set `{field}` = {amount} for <@{pid}>.")

    async def _relink_riot(self, ctx, pid: str, value: str) -> None:
        try:
            riot_name, riot_tag = value.rsplit("#", 1)
        except ValueError:
            await ctx.send("Riot ID must be in `Name#Tag` format.")
            return
        riot_name, riot_tag = riot_name.strip(), riot_tag.strip()
        if not riot_name or not riot_tag:
            await ctx.send("Riot ID must be in `Name#Tag` format.")
            return

        async with aiohttp.ClientSession() as session:
            try:
                payload = await get_account_by_riot_id(
                    session, riot_name, riot_tag, priority=True
                )
            except (
                RiotApiInconclusive,
                aiohttp.ClientError,
                asyncio.TimeoutError,
            ) as e:
                await ctx.send(f"Network error reaching HenrikDev API: {e}")
                return
        if payload is None or not payload.get("_raw"):
            await ctx.send(
                f"Could not find Riot account `{riot_name}#{riot_tag}`; nothing was changed."
            )
            return

        # Same stale-link cleanup as !linkriot: a Riot ID links to one Discord account.
        for stale in users.find({"name": riot_name.lower(), "tag": riot_tag.lower()}):
            if str(stale.get("discord_id")) != pid:
                users.delete_one({"_id": stale["_id"]})
                mmr_collection.delete_one({"player_id": stale.get("discord_id")})
                log.info(
                    "Removed stale Riot ID link %s#%s from discord id %s",
                    riot_name,
                    riot_tag,
                    stale.get("discord_id"),
                )

        set_fields = {"name": riot_name.lower(), "tag": riot_tag.lower()}
        puuid = (payload.get("puuid") or "").strip()
        if puuid:
            set_fields["puuid"] = puuid
        users.update_one({"discord_id": pid}, {"$set": set_fields}, upsert=True)
        mmr_collection.update_one(
            {"player_id": pid},
            {"$set": {"name": f"{riot_name}#{riot_tag}"}},
            upsert=False,
        )
        log.info("%s relinked %s to %s#%s", ctx.author, pid, riot_name, riot_tag)
        await ctx.send(f"Relinked <@{pid}> to `{riot_name}#{riot_tag}`.")

    @commands.command(name="substitute")
    @commands.has_permissions(administrator=True)
    async def substitute(self, ctx, *, args: str = ""):
        """
        Replace a player in the current match (teams must already be decided).
        Usage: !substitute <@OutPlayer|OutName#Tag> <@InPlayer|InName#Tag>
        (Riot IDs containing spaces must use the @mention form.)
        """
        if not self.bot.match_ongoing:
            await ctx.send(
                "Substitutions only work on a match whose teams are already decided."
            )
            return
        parts = (args or "").split()
        if len(parts) < 2:
            await ctx.send(
                "Usage: `!substitute <@OutPlayer|OutName#Tag> <@InPlayer|InName#Tag>`"
            )
            return

        # Riot IDs and display names can contain spaces, so try every split
        # point: everything left of it is the outgoing player, right is the
        # incoming one. The first fully-resolved pair wins.
        out_pid = in_pid = None
        for i in range(1, len(parts)):
            left = resolve_user_arg(" ".join(parts[:i]), ctx.guild)
            right = resolve_user_arg(" ".join(parts[i:]), ctx.guild)
            if left and right:
                out_pid, in_pid = left, right
                break
        if not out_pid or not in_pid:
            await ctx.send(
                "Could not resolve one of the players — use @mentions or linked `Name#Tag`s."
            )
            return
        out_pid, in_pid = str(out_pid), str(in_pid)
        if in_pid == out_pid:
            await ctx.send("Outgoing and incoming player are the same.")
            return
        if in_pid in {str(p["id"]) for p in self.bot.queue}:
            await ctx.send("That player is already in this match.")
            return

        team = None
        for t in (self.bot.team1, self.bot.team2):
            if any(str(p["id"]) == out_pid for p in t):
                team = t
                break
        if team is None:
            await ctx.send(f"<@{out_pid}> is not on either team in the current match.")
            return

        # The incoming player must have a valid linked Riot ID (issue requirement).
        u = users.find_one({"discord_id": in_pid})
        if not u or not u.get("name") or not u.get("tag"):
            await ctx.send(
                f"<@{in_pid}> has no linked Riot ID; they must run `!linkriot Name#Tag` first."
            )
            return
        async with aiohttp.ClientSession() as session:
            ok, reason = await verify_riot_account_async(session, u["name"], u["tag"])
        if ok is False:
            await ctx.send(
                f"<@{in_pid}>'s linked Riot ID `{u['name']}#{u['tag']}` "
                f"could not be verified: {reason}"
            )
            return

        member = ctx.guild.get_member(int(in_pid)) if ctx.guild else None
        in_name = member.name if member else u["name"]
        in_player = {"id": in_pid, "name": in_name}

        # Hold the report lock while mutating queue/teams: !report reads this
        # state to resolve players and award MMR, so a concurrent report could
        # otherwise see a half-swapped roster.
        async with self.bot.report_lock:
            # Swap in bot.queue too: !report resolves API players through the
            # queue, so a substitute missing there would break reporting.
            for i, p in enumerate(self.bot.queue):
                if str(p["id"]) == out_pid:
                    self.bot.queue[i] = in_player
                    break
            for i, p in enumerate(team):
                if str(p["id"]) == out_pid:
                    team[i] = in_player
                    break
        self.bot.ensure_player_mmr(in_pid, self.bot.player_names)
        self.bot.player_names[in_pid] = in_name

        # The outgoing player's doubledown no longer applies; refund it.
        if duck_coins_enabled() and out_pid in self.bot.double_downs:
            self.bot.double_downs.discard(out_pid)
            add_coins(out_pid, DOUBLEDOWN_COST)

        # Swap match roles and move the incoming player to their team voice
        # channel (best effort).
        if self.bot.match_role:
            if member:
                try:
                    await member.add_roles(self.bot.match_role)
                except discord.HTTPException:
                    pass
            out_member = ctx.guild.get_member(int(out_pid)) if ctx.guild else None
            if out_member:
                try:
                    await out_member.remove_roles(self.bot.match_role)
                except discord.HTTPException:
                    pass
        if voice_presence_enabled() and ctx.guild:
            try:
                await move_teams_to_voice(ctx.guild, self.bot.team1, self.bot.team2)
            except Exception as e:
                log.warning("Voice move failed: %s", e)

        side = "Attackers" if team is self.bot.team1 else "Defenders"
        log.info(
            "Substitute by %s: %s in for %s (%s)",
            ctx.author,
            in_pid,
            out_pid,
            side,
        )
        await ctx.send(
            f"Substituted <@{in_pid}> in for <@{out_pid}> ({side}). "
            "Report with `!report` as usual once the game is done."
        )

    @commands.command(name="fixmap")
    @commands.has_permissions(administrator=True)
    async def fixmap(self, ctx, *, map_name: str = ""):
        """Force-set the current match's map (fixes 'map doesn't match' report errors)."""
        if not (self.bot.match_ongoing or self.bot.selected_map):
            await ctx.send("No active match to set a map for.")
            return
        from services.maps_service import get_standard_maps

        try:
            pool = get_standard_maps()
        except Exception as e:
            await ctx.send(f"Could not fetch the map pool: {e}")
            return
        wanted = map_name.strip().lower()
        canonical = next((m for m in pool if m.lower() == wanted), None)
        if not canonical:
            await ctx.send(
                f"`{map_name}` isn't a standard map. Choose one of: {', '.join(pool)}."
            )
            return
        old = self.bot.selected_map
        self.bot.selected_map = canonical
        log.info("%s set the match map from %s to %s", ctx.author, old, canonical)
        await ctx.send(f"Map for the current match set to **{canonical}** (was {old}).")

    @commands.command(name="setconfig")
    @commands.has_permissions(administrator=True)
    async def setconfig(self, ctx, key: str, value: str):
        """Update a bot.ini setting; applies immediately without a restart."""
        if not re.fullmatch(r"[A-Za-z0-9_]+", key):
            await ctx.send("Invalid key name (letters, digits, underscores only).")
            return
        set_ini_value(BOT_INI_PATH, "features", key, value)
        # Live reload: re-read the file, then rebind the features proxy so
        # feature_enabled() sees the new value even if [features] was missing.
        BOT_CONFIG.read(BOT_INI_PATH)
        if BOT_CONFIG.has_section("features"):
            globals_mod.BOT_FEATURES = BOT_CONFIG["features"]
        log.info("%s set bot.ini [features] %s = %s", ctx.author, key, value)
        await ctx.send(f"Set [features] `{key}` = `{value}` (applied immediately).")

    @commands.command(name="showconfig")
    @commands.has_permissions(administrator=True)
    async def showconfig(self, ctx):
        """Show the current bot.ini [features] settings."""
        lines = [f"{k} = {v}" for k, v in globals_mod.BOT_FEATURES.items()]
        await ctx.send(
            f"**bot.ini [features]**\n```\n{chr(10).join(lines) or '(empty)'}\n```"
        )

    @commands.command(name="adminhelp")
    @commands.has_permissions(administrator=True)
    async def adminhelp(self, ctx):
        """List admin commands with usage."""
        embed = discord.Embed(
            title="Admin Commands",
            description="Maintenance and management commands (admins only).",
            color=discord.Color.red(),
        )
        # Walk every registered prefix command and keep the admin-gated ones
        # (discord.py permission checks like has_permissions/has_role), so this
        # list stays correct as commands come and go.
        sections: dict[str, list[str]] = {}
        for cmd in sorted(self.bot.commands, key=lambda c: c.name):
            if not cmd.enabled or cmd.name == "adminhelp":
                continue
            if not any(
                getattr(check, "__module__", "").startswith("discord")
                for check in cmd.checks
            ):
                continue
            usage_args = _usage_args_of(cmd)
            usage = f"!{cmd.name} {usage_args}".strip()
            desc = _short_desc_of(cmd)
            section = ADMIN_COMMAND_HELP.get(cmd.name, ("Other",))[0]
            sections.setdefault(section, []).append(f"`{usage}` — {desc}")
        # Sections first (curated order), then any unlisted commands.
        order = [s for s in ("Season", "Match", "Players", "Config") if s in sections]
        for section in order:
            section_lines = sections.pop(section)
            chunks = _chunk_embed_lines(section_lines)
            for i, chunk in enumerate(chunks, start=1):
                name = section if len(chunks) == 1 else f"{section} ({i}/{len(chunks)})"
                embed.add_field(name=name, value=chunk, inline=False)
        for section, section_lines in sorted(sections.items()):
            chunks = _chunk_embed_lines(section_lines)
            for i, chunk in enumerate(chunks, start=1):
                name = section if len(chunks) == 1 else f"{section} ({i}/{len(chunks)})"
                embed.add_field(name=name, value=chunk, inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="matchinfo")
    @commands.has_permissions(administrator=True)
    async def matchinfo(self, ctx):
        """Dump the bot's internal match/queue state for debugging."""
        bot = self.bot

        def team_lines(team):
            lines = []
            for p in team:
                ud = users.find_one({"discord_id": str(p["id"])})
                riot = (
                    f"{ud.get('name', '?')}#{ud.get('tag', '?')}" if ud else "unlinked"
                )
                lines.append(f"{p.get('name', '?')} ({riot})")
            return "\n".join(lines) or "—"

        session = getattr(bot, "bet_session", None) or {}
        info = (
            f"signup_active: {bot.signup_active}\n"
            f"match_ongoing: {bot.match_ongoing}\n"
            f"match_not_reported: {bot.match_not_reported}\n"
            f"setup_generation: {bot.setup_generation}\n"
            f"chosen_mode: {bot.chosen_mode}\n"
            f"selected_map: {bot.selected_map}\n"
            f"captain1: {bot.captain1['name'] if bot.captain1 else None}\n"
            f"captain2: {bot.captain2['name'] if bot.captain2 else None}\n"
            f"match_channel: {getattr(bot.match_channel, 'name', None)}\n"
            f"queue ({len(bot.queue)}): "
            f"{', '.join(p.get('name', '?') for p in bot.queue) or '—'}\n"
            f"team1 (Attackers):\n{team_lines(bot.team1)}\n"
            f"team2 (Defenders):\n{team_lines(bot.team2)}\n"
            f"bet_window_open: {bool(session.get('open'))}\n"
            f"double_downs: {', '.join(bot.double_downs) or '—'}"
        )
        await ctx.send(f"```\n{info[:1900]}\n```")

    @commands.command(name="addcoins")
    @commands.has_permissions(administrator=True)
    async def addcoins(self, ctx, *, args: str = ""):
        """Grant (or, with a negative amount, remove) Duck Coins for a player."""
        if not duck_coins_enabled():
            await ctx.send("Duck Coins features are disabled.")
            return
        parts = (args or "").split()
        if not parts:
            await ctx.send("Usage: `!addcoins <@user|Name#Tag> <amount>`")
            return
        try:
            amount = int(parts[-1])
        except ValueError:
            await ctx.send("Usage: `!addcoins <@user|Name#Tag> <amount>`")
            return
        user_arg = " ".join(parts[:-1])
        pid = resolve_user_arg(user_arg, ctx.guild)
        if not pid:
            await ctx.send(f"Could not resolve player `{user_arg}`.")
            return
        if amount < 0 and coins_of(pid) + amount < 0:
            await ctx.send(
                f"<@{pid}> only has {coins_of(pid)} coins; can't remove {-amount}."
            )
            return
        add_coins(pid, amount)
        log.info("%s adjusted %s's coins by %s", ctx.author, pid, amount)
        await ctx.send(f"<@{pid}> now has {coins_of(pid)} {duck_emote(self.bot)}.")

    @commands.command(name="resetplayer")
    @commands.has_permissions(administrator=True)
    async def resetplayer(self, ctx, *, args: str = ""):
        """Reset one player's season stats and MMR (corrupt-data recovery)."""
        user_arg = (args or "").strip()
        if not user_arg:
            await ctx.send("Usage: `!resetplayer <@user|Name#Tag>`")
            return
        pid = resolve_user_arg(user_arg, ctx.guild)
        if not pid:
            await ctx.send(f"Could not resolve player `{user_arg}`.")
            return
        zeroed = SEASON_STAT_DEFAULTS
        mmr_collection.update_one({"player_id": pid}, {"$set": zeroed}, upsert=True)
        if pid in self.bot.player_mmr:
            self.bot.player_mmr[pid].update(zeroed)
        # Their coins were just reset, so drop any active doubledown (and the
        # map-override turn if they held it) rather than granting it for free.
        self.bot.double_downs.discard(pid)
        if self.bot.map_override_last_by == pid:
            self.bot.map_override_last = 0
            self.bot.map_override_last_by = None
            self.bot.map_override_deadline = None
        log.info("%s reset season stats for %s", ctx.author, pid)
        await ctx.send(f"Reset season stats and MMR for <@{pid}>.")

    @commands.command(name="forcereport")
    @commands.has_permissions(administrator=True)
    async def forcereport(self, ctx, *, match_ref: str = ""):
        """
        Report a specific match by id, even when a normal !report fails.
        Usage: !forcereport <match-id-or-tracker.gg-url>
        e.g. !forcereport https://tracker.gg/valorant/match/2233f144-...
        Every player must resolve to a linked Discord account, and the match
        must not already be reported.
        """
        match_id = _extract_match_id(match_ref)
        if not match_id:
            await ctx.send(
                "Pass a match id or tracker.gg URL: "
                "`!forcereport https://tracker.gg/valorant/match/<id>`"
            )
            return
        log.info("%s requested a force report of %s", ctx.author, match_id)

        if all_matches.find_one({"metadata.match_id": match_id}):
            await ctx.send("That match has already been reported.")
            return

        await ctx.send(f"Fetching match `{match_id}`...")
        async with aiohttp.ClientSession() as session:
            try:
                match = await self._fetch_match_by_id(session, match_id)
            except (
                RiotApiInconclusive,
                aiohttp.ClientError,
                asyncio.TimeoutError,
            ) as e:
                await ctx.send(f"Network error reaching HenrikDev API: {e}")
                return
        if match is None:
            await ctx.send(f"Could not find match `{match_id}` on the HenrikDev API.")
            return

        players = match.get("players") or []
        if len(players) != 10:
            await ctx.send(f"Match has {len(players)} players (expected 10); aborting.")
            return

        # Every player must map to a linked Discord account (puuid first,
        # Riot name/tag fallback — same resolution order !report uses).
        pid_of, unlinked = resolve_match_players(players)
        if unlinked:
            await ctx.send(
                "These players have no linked Discord account; fix with "
                f"`!editplayer <user> riot Name#Tag` first: {', '.join(unlinked)}"
            )
            return

        # Hold the report lock: a live !report must not race this.
        async with self.bot.report_lock:
            # Re-check after acquiring: a concurrent report may have landed.
            if all_matches.find_one({"metadata.match_id": match_id}):
                await ctx.send("That match has already been reported.")
                return

            total_rounds = len(match.get("rounds") or [])
            if total_rounds <= 0:
                await ctx.send("Match has no round data; aborting.")
                return
            try:
                ratings = estimate_ratings_v4(match)
            except Exception as e:
                log.warning("VLR rating estimation failed: %s", e)
                ratings = {}
            wtid = next(
                (t["team_id"].lower() for t in match.get("teams", []) if t.get("won")),
                None,
            )
            if wtid not in ("red", "blue"):
                await ctx.send("Could not determine the winning team; aborting.")
                return
            lose_tid = "blue" if wtid == "red" else "red"
            rounds_by_side = {
                t["team_id"].lower(): rounds_to_int(
                    t.get("rounds_won", t.get("rounds", 0))
                )
                for t in match.get("teams", [])
            }

            # Initialize the bot's cache entries for every player (what
            # report.py's flow relies on), then compute team averages: new
            # players seed at 100x their match rating (same rule !report
            # applies via _effective_mmr).
            for p in players:
                self.bot.ensure_player_mmr(pid_of[id(p)], self.bot.player_names)
            # Team averages: new players seed at 100x their match rating
            # (same rule !report applies via _effective_mmr).
            eff = {}
            for p in players:
                pid = pid_of[id(p)]
                doc = mmr_collection.find_one({"player_id": pid})
                played = doc and (
                    doc.get("matches_played", 0) > 0
                    or (doc.get("wins", 0) + doc.get("losses", 0)) > 0
                )
                if played:
                    eff[id(p)] = doc.get("mmr", 0)
                else:
                    r = ratings.get((p.get("puuid") or "").lower(), {}).get("rating")
                    eff[id(p)] = (
                        max(0, round(100.0 * r)) if isinstance(r, (int, float)) else 0
                    )
            sides = defaultdict(list)
            for p in players:
                sides[p["team_id"].lower()].append(eff[id(p)])
            side_avg = {s: sum(v) / len(v) for s, v in sides.items() if v}

            for p in players:
                pid = pid_of[id(p)]
                side = p["team_id"].lower()
                won = side == wtid
                r = ratings.get((p.get("puuid") or "").lower(), {}).get("rating")
                update_stats(
                    p,
                    total_rounds,
                    self.bot.player_mmr,
                    self.bot.player_names,
                    discord_id=pid,
                    team_avg_mmr=side_avg[side],
                    opp_avg_mmr=side_avg[lose_tid if won else wtid],
                    our_rounds=rounds_by_side.get(side, 0),
                    opp_rounds=rounds_by_side.get(lose_tid if won else wtid, 0),
                    rating=r,
                )

            # Same tail as !report: ranks, match doc, season counter, cache flush.
            self._rebuild_ranks()
            all_matches.insert_one(match)
            seasons.update_one(
                {"_id": "current"}, {"$inc": {"matches_played": 1}}, upsert=True
            )
            self.bot.load_mmr_data()

            summary = "\n".join(
                f"**{p['name']}**: "
                f"{self.bot.player_mmr[pid_of[id(p)]]['mmr']} MMR "
                f"({self.bot.player_mmr[pid_of[id(p)]]['wins']}W/"
                f"{self.bot.player_mmr[pid_of[id(p)]]['losses']}L)"
                for p in players
            )
            await ctx.send(
                f"Reported match `{match_id}` "
                f"({match['metadata'].get('map', {}).get('name', '?')}).\n{summary[:1800]}"
            )
            log.info("Reported match %s", match_id)

    @staticmethod
    async def _fetch_match_by_id(session, match_id: str):
        """Fetch a match by id through the shared riot_api rate limiter."""
        return await get_match_by_id_async(session, match_id, priority=True)

    def _rebuild_ranks(self) -> None:
        """Rewrite previous/current rank fields from the current MMR order
        (mirrors the rank snapshot report.py writes after each match)."""
        data = list(mmr_collection.find())
        played = [
            d
            for d in data
            if d.get("matches_played", 0) > 0
            or (d.get("wins", 0) + d.get("losses", 0)) > 0
        ]
        played.sort(key=lambda x: x.get("mmr", 0), reverse=True)
        previous = {d["player_id"]: d.get("current_rank") for d in data}
        for pos, d in enumerate(played, 1):
            mmr_collection.update_one(
                {"player_id": d["player_id"]},
                {
                    "$set": {
                        "previous_rank": previous.get(d["player_id"]),
                        "current_rank": pos,
                    }
                },
            )

    @commands.command(name="resetseason")
    @commands.has_permissions(administrator=True)
    async def resetseason(self, ctx, *, confirm: str = ""):
        """
        Wipe all stats for the current season, without ending it.
        Requires `!resetseason confirm` (two-step, no accidental wipes).
        Reversible: snapshots every player doc and the season counter to a
        backup file in the same format tools.ops revert backups use
        ({"$oid": ...} ObjectIds), restorable via
        `python tools/ops/revert_last_match.py --restore <file>`.
        """
        if confirm.strip().lower() != "confirm":
            await ctx.send(
                "This wipes **everyone's** MMR and season stats. "
                "If you're sure, run `!resetseason confirm`."
            )
            return

        log.warning("%s is wiping all season stats", ctx.author)
        from tools.ops.revert_last_match import _jsonify

        # Snapshot every player doc + the current season doc to a backup file
        # (same layout the revert script's --restore reads).
        docs = list(mmr_collection.find())
        season_doc = seasons.find_one({"_id": "current"})
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = (
            Path(globals_mod.__file__).parent / "backups" / f"season_reset_{stamp}.json"
        )
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup = {
            "backup_created_at": backup_path.name,
            "collections": {
                # "matches" is required by the revert script's restore.
                "matches": [],
                "mmr_data": [_jsonify(d) for d in docs],
                "seasons": [_jsonify(season_doc)] if season_doc else [],
            },
        }
        backup_path.write_text(json.dumps(backup), encoding="utf-8")

        # Wipe season stats for everyone. Identity survives; duck coins are
        # per-season and reset with the rest of the stats (see
        # SEASON_STAT_DEFAULTS). Per-match coin state is dropped as well.
        mmr_collection.update_many({}, {"$set": SEASON_STAT_DEFAULTS})
        clear_season_coin_state(self.bot)
        seasons.update_one(
            {"_id": "current"}, {"$set": {"matches_played": 0}}, upsert=True
        )
        self.bot.load_mmr_data()
        await ctx.send(
            "Wiped season stats for all players. "
            f"Backup: `{backup_path.name}` (restorable via "
            "`python tools/ops/revert_last_match.py --restore`)."
        )
        log.info("Season stats wiped; backup %s", backup_path.name)

    @commands.command(name="snapshotseason")
    @commands.has_permissions(administrator=True)
    async def snapshotseason(self, ctx, *, arg: str = ""):
        """
        Export the current season's match + player data to a .json file.
        Read-only: the bot's data is not modified. Add `full` to include the
        persistent users (Riot link) collection. Restore with !recoverseason.
        """
        include_users = arg.strip().lower() == "full"
        season_doc = seasons.find_one({"_id": "current"})
        if not season_doc:
            await ctx.send("No current season found; nothing to snapshot.")
            return
        season_num = int(season_doc.get("season_number", 0))

        from tools.ops.revert_last_match import _jsonify

        async with self.bot.report_lock:
            matches = list(all_matches.find(_season_match_filter(season_num)))
            mmr_docs = list(mmr_collection.find())
        backup = {
            "backup_created_at": datetime.now(timezone.utc).isoformat(),
            "format": "season-snapshot",
            "season_number": season_num,
            "collections": {
                "matches": [_jsonify(d) for d in matches],
                "mmr_data": [_jsonify(d) for d in mmr_docs],
                "seasons": [_jsonify(season_doc)],
                **(
                    {"users": [_jsonify(d) for d in users.find()]}
                    if include_users
                    else {}
                ),
            },
        }
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"season_snapshot_{season_num}_{stamp}.json.gz"
        summary = (
            f"Season {season_num} snapshot: {len(matches)} match(es), "
            f"{len(mmr_docs)} player doc(s)"
            + (
                f", {len(backup['collections']['users'])} users"
                if include_users
                else ""
            )
            + "."
        )
        # Season payloads are highly repetitive JSON, so gzip shrinks them
        # ~10-20x; !recoverseason sniffs the gzip magic bytes. Snapshots that
        # still exceed the upload budget are written to the bot's backups
        # folder instead of failing with a 413.
        payload = gzip.compress(json.dumps(backup, default=str).encode("utf-8"))
        if len(payload) > SNAPSHOT_UPLOAD_LIMIT:
            disk_path = Path(globals_mod.__file__).parent / "backups" / filename
            disk_path.parent.mkdir(parents=True, exist_ok=True)
            disk_path.write_bytes(payload)
            await ctx.send(
                f"{summary} Snapshot is too large to upload here; saved to "
                f"`backups/{filename}`."
            )
            log.info(
                "%s snapshotted season %s to disk (%s bytes): %s",
                ctx.author,
                season_num,
                len(payload),
                disk_path,
            )
            return
        await ctx.send(
            summary,
            file=discord.File(io.BytesIO(payload), filename=filename),
        )
        log.info(
            "%s snapshotted season %s (%s matches, %s player docs) to %s",
            ctx.author,
            season_num,
            len(matches),
            len(mmr_docs),
            filename,
        )

    @commands.command(name="recoverseason")
    @commands.has_permissions(administrator=True)
    async def recoverseason(self, ctx, *, arg: str = ""):
        """
        Overwrite the current season's data from a !snapshotseason .json file
        attached to this message. Requires `!recoverseason confirm` (two-step,
        no accidental overwrites). The attachment replaces all current-season
        matches and player data; docs absent from the snapshot are deleted.
        """
        if arg.strip().lower() != "confirm":
            await ctx.send(
                "This **overwrites** the current season's matches and player "
                "data with the attached snapshot. Attach the .json file and "
                "run `!recoverseason confirm`."
            )
            return
        if not ctx.message.attachments:
            await ctx.send("Attach the snapshot .json file to this message.")
            return

        from tools.ops.revert_last_match import _dejsonify, _jsonify

        attachment = ctx.message.attachments[0]
        if not attachment.filename.lower().endswith((".json", ".json.gz")):
            await ctx.send("Snapshot must be a .json or .json.gz file.")
            return
        try:
            raw = await attachment.read()
            # !snapshotseason gzips its payload; sniff the magic bytes so
            # both plain and gzipped snapshots are accepted.
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            backup = json.loads(raw)
        except Exception as e:
            await ctx.send(f"Could not read the attachment as JSON: {e}")
            return
        collections = backup.get("collections")
        if not isinstance(collections, dict) or "mmr_data" not in collections:
            await ctx.send(
                "Invalid snapshot: expected a `!snapshotseason` file with a "
                "`collections` section (mmr_data required)."
            )
            return

        season_doc = seasons.find_one({"_id": "current"})
        log.warning(
            "%s is recovering season data from %s (snapshot season: %s)",
            ctx.author,
            attachment.filename,
            backup.get("season_number"),
        )
        counts = {}
        safety_path = None
        async with self.bot.report_lock:
            # Pre-recovery safety snapshot to disk (restorable via
            # python tools/ops/revert_last_match.py --restore).
            if season_doc:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                safety = {
                    "backup_created_at": stamp,
                    "collections": {
                        "matches": [
                            _jsonify(d)
                            for d in all_matches.find(
                                _season_match_filter(
                                    int(season_doc.get("season_number", 0))
                                )
                            )
                        ],
                        "mmr_data": [_jsonify(d) for d in mmr_collection.find()],
                        "seasons": [_jsonify(season_doc)],
                    },
                }
                safety_path = (
                    Path(globals_mod.__file__).parent
                    / "backups"
                    / f"season_pre_recovery_{stamp}.json"
                )
                safety_path.parent.mkdir(parents=True, exist_ok=True)
                safety_path.write_text(json.dumps(safety), encoding="utf-8")

            try:
                for name in ("matches", "mmr_data", "seasons"):
                    docs = collections.get(name) or []
                    coll = {
                        "matches": all_matches,
                        "mmr_data": mmr_collection,
                        "seasons": seasons,
                    }[name]
                    # Complete overwrite: drop existing docs (matches scoped
                    # to this season) then write exactly what the snapshot
                    # contains. Seasons use replace_one so the "current" doc
                    # is swapped in place (archived seasons never inserted
                    # by snapshots can't collide on _id).
                    if name == "matches":
                        season_num = (
                            int(season_doc.get("season_number", 0)) if season_doc else 0
                        )
                        coll.delete_many(_season_match_filter(season_num))
                        for raw in docs:
                            coll.insert_one(_dejsonify(raw))
                    elif name == "mmr_data":
                        coll.delete_many({})
                        for raw in docs:
                            coll.insert_one(_dejsonify(raw))
                    else:
                        for raw in docs:
                            doc = _dejsonify(raw)
                            coll.replace_one({"_id": doc["_id"]}, doc, upsert=True)
                    counts[name] = len(docs)
            except Exception as e:
                log.error("Season recovery failed: %s", e, exc_info=True)
                safety_note = (
                    f" The safety backup `{safety_path.name}` can restore the "
                    "pre-recovery state "
                    "(`python tools/ops/revert_last_match.py --restore`)."
                    if safety_path
                    else ""
                )
                await ctx.send(f"Recovery failed partway ({e}).{safety_note}")
                return

            clear_season_coin_state(self.bot)
            self.bot.load_mmr_data()

        await ctx.send(
            f"Recovered season data from `{attachment.filename}`: "
            + ", ".join(f"{k}={v}" for k, v in counts.items())
            + (f". Safety backup: `{safety_path.name}`" if season_doc else "")
        )
        log.info("Season recovered from %s; counts=%s", attachment.filename, counts)
