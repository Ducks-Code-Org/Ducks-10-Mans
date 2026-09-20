"Report the most recent match played to update MMR and stats."

import asyncio
import copy
import logging

import aiohttp
import discord
from discord.ext import commands

from commands import BotCommands
from database import all_matches, mmr_collection, seasons, users
from game.duck_coins import (
    DOUBLEDOWN_COST,
    award_match_coins,
    doubledown_multiplier_of,
    duck_coins_enabled,
    refund_open_bets,
    settle_bets,
)
from game.ranking import has_played
from game.ranks import RANKS, SSR_NAME, rank_of, role_mention, sync_player_rank
from game.recent_queue import remember_recent_queue
from game.stats_helper import update_stats
from game.voice_presence import move_players_to_lobby, voice_presence_enabled
from globals import feature_enabled
from services.riot_api import RiotApiInconclusive, get_recent_matches_async
from services.vlr_rating import estimate_ratings_v4
from tracker_links import tracker_link

log = logging.getLogger(__name__)


async def setup(bot):
    if not hasattr(bot, "report_lock"):
        bot.report_lock = asyncio.Lock()
    await bot.add_cog(ReportCommand(bot))


async def _delete_channel_safely(channel) -> None:
    """Delete a channel, tolerating NotFound/Forbidden."""
    try:
        await channel.delete()
    except discord.NotFound:
        log.debug("Match channel already deleted")
    except discord.Forbidden:
        log.warning("Missing permissions to delete match channel")


async def _remove_role_safely(role) -> None:
    """Strip the role from its members, then delete it."""
    try:
        for member in list(role.members):
            try:
                await member.remove_roles(role)
            except discord.HTTPException:
                log.warning("Error removing role from member")
    except discord.HTTPException:
        log.warning("Error removing roles from members")
    try:
        await role.delete()
    except discord.NotFound:
        log.debug("Match role already deleted")
    except discord.Forbidden:
        log.warning("Missing permissions to delete match role")


async def _delete_signup_message_safely(message) -> None:
    try:
        await message.delete()
    except discord.NotFound:
        pass


async def cleanup_match_resources(bot, cancelled: bool = False):
    """Delete the match channel/role and reset per-match state."""
    await bot.wait_until_ready()
    try:
        if bot.queue:
            remember_recent_queue(bot.queue, cancelled=cancelled)
        if bot.match_channel:
            await _delete_channel_safely(bot.match_channel)
            bot.match_channel = None

        if bot.match_role:
            await _remove_role_safely(bot.match_role)
            bot.match_role = None

        bot.match_not_reported = False
        bot.match_ongoing = False
        bot.queue.clear()

        # The channel/role these stamps belong to are gone; a later !signup
        # must see the leftovers as stale (not a live setup) or it would
        # refuse to run recovery cleanup.
        bot.match_setup_generation = None

        # Stop a stale signup view before its message disappears: its live
        # buttons would otherwise keep answering clicks against a deleted
        # match channel, and every reply there fails with 10003 Unknown
        # Channel (issue #216).
        stale_view = getattr(bot, "signup_view", None)
        if stale_view is not None:
            stale_view.cleanup()
            bot.signup_view = None

        if bot.current_signup_message:
            await _delete_signup_message_safely(bot.current_signup_message)
            bot.current_signup_message = None
    except Exception as e:
        log.error("Error during cleanup: %s", e, exc_info=e)


async def grant_season_roles(guild, players) -> None:
    """Give every player the persistent 'Season-#' role for the current season.

    Gated by the `season_role` flag in bot.ini's [features] section.
    """
    if not feature_enabled("season_role") or guild is None or not players:
        return

    season_doc = seasons.find_one({"_id": "current"})
    try:
        season_number = int((season_doc or {}).get("season_number", 0))
    except (TypeError, ValueError):
        log.warning("Invalid season_number stored; skipping season role grant")
        return
    if season_number < 0:
        return

    role_name = f"Season-{season_number}"
    season_role = discord.utils.get(guild.roles, name=role_name)
    if season_role is None:
        try:
            season_role = await guild.create_role(name=role_name)
        except discord.Forbidden:
            log.warning("Missing permissions to create the season role")
            return

    for player in players:
        try:
            player_id = int(player["id"])
            member = guild.get_member(player_id) or await guild.fetch_member(player_id)
        except (KeyError, TypeError, ValueError):
            log.warning("Skipping player with invalid id: %r", player)
            continue
        except discord.HTTPException as e:
            log.warning("Could not look up member %s: %s", player.get("id"), e)
            continue
        if season_role not in member.roles:
            try:
                await member.add_roles(season_role)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                log.warning("Could not grant role to %s: %s", player_id, e)


class ReportCommand(BotCommands):
    @commands.hybrid_command(
        name="report",
        description="Report match results and update MMR",
    )
    async def report(self, ctx):
        log.info("Match report requested by %s", ctx.author)
        # ---------------------------------------------------------
        # Acquire report_lock to prevent concurrent double-reporting.
        # Only one /report command may run at a time.  We additionally
        # atomically clear match_not_reported so a second reporter who
        # acquires the lock after us sees the flag as already cleared.
        # ---------------------------------------------------------
        async with self.bot.report_lock:
            await ctx.send("Attempting to report latest match...")

            # linkage check
            current_user = users.find_one({"discord_id": str(ctx.author.id)})
            if not current_user:
                await ctx.send(
                    "You need to link your Riot account first using `/linkriot Name#Tag`",
                    ephemeral=True,
                )
                return

            name = (current_user.get("name") or "").lower().strip()
            tag = (current_user.get("tag") or "").lower().strip()
            if not name or not tag:
                await ctx.send(
                    "Your Riot account looks incomplete. Re-link with `/linkriot Name#Tag`.",
                    ephemeral=True,
                )
                return

            if not self.bot.match_ongoing:
                await ctx.send(
                    "No match is currently active, use `/signup` to start one",
                    ephemeral=True,
                )
                return
            if not self.bot.selected_map:
                await ctx.send("No map was selected for this match.", ephemeral=True)
                return

            # ------------------------------------------------------------
            # ATOMIC CLAIM: only the FIRST simultaneous reporter to get here
            # may proceed; later ones will see match_not_reported == False.
            # ------------------------------------------------------------
            if not self.bot.match_not_reported:
                await ctx.send(
                    "This match has already been reported (a report is in progress "
                    "or completed).",
                    ephemeral=True,
                )
                return
            self.bot.match_not_reported = False  # claim it right now

        # ------------------------------------------------------------
        # After this point we hold the sole right to write to the DB.
        # Everything outside the lock reads match state that won't change
        # until this handler finishes (cleanup at the end resets flags).
        #
        # CLAIM RELEASE ON FAILURE: the claim above is only "consumed" once
        # the report has committed ANY database write. Every earlier failure
        # path — match not yet visible on the API (404), network errors, map
        # mismatch, missing players — must restore match_not_reported so the
        # user can simply retry once the match appears. Without this, a
        # premature report attempt permanently blocked retrying with "already
        # been reported".
        #
        # A failure AFTER the first write must NOT release the claim: stats
        # and coins were already applied and a retry would apply them again.
        # _report_claimed calls on_commit() immediately before its first
        # database write; anything that fails before that point releases the
        # claim so the reporter can retry.
        # ------------------------------------------------------------
        committed = False

        def _on_commit():
            nonlocal committed
            committed = True

        try:
            await self._report_claimed(ctx, name, tag, on_commit=_on_commit)
        finally:
            if not committed:
                self.bot.match_not_reported = True
                log.info(
                    "Report claim released for retry (report did not complete): %s",
                    ctx.author,
                )

    async def _report_claimed(self, ctx, name: str, tag: str, on_commit=None) -> None:
        """Body of !report after the atomic claim has been taken.

        Invokes ``on_commit()`` immediately before the first database write.
        Early validation returns (and failures before that point) leave
        on_commit uncalled, releasing the claim for a retry; failures after
        it leave the claim consumed so a retry cannot double-apply stats or
        coins.
        """

        def _norm_map(s: str) -> str:
            m = (s or "").strip().lower()
            aliases = {
                "ice box": "icebox",
                "the abyss": "abyss",
                "fracc": "fracture",
            }
            return aliases.get(m, m)

        region, platform = "na", "pc"

        try:
            async with aiohttp.ClientSession() as session:
                data = await get_recent_matches_async(
                    session,
                    name,
                    tag,
                    region=region,
                    platform=platform,
                    priority=True,
                )
        except (RiotApiInconclusive, aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.error("Network error fetching recent matches: %s", e, exc_info=e)
            await ctx.send(f"Network error reaching HenrikDev API: {e}", ephemeral=True)
            return

        if data is None:
            await ctx.send(
                "No recent matches found for your Riot ID yet — the game may "
                "still be processing on the Riot API. Try again in a minute.",
                ephemeral=True,
            )
            return

        if not data.get("data"):
            await ctx.send("Could not retrieve match data.", ephemeral=True)
            return

        match = data["data"][0]
        metadata = match.get("metadata") or {}

        # Idempotency: if this match id was already recorded (e.g. a retried
        # report after a post-write failure), refuse instead of re-applying
        # MMR/coins and inserting a duplicate match doc.
        api_match_id = metadata.get("match_id")
        if api_match_id and all_matches.find_one({"metadata.match_id": api_match_id}):
            log.warning(
                "Report ignored: match %s was already recorded",
                api_match_id,
            )
            await ctx.send("This match has already been recorded.", ephemeral=True)
            return

        map_field = metadata.get("map")
        if isinstance(map_field, dict):
            api_map = _norm_map(map_field.get("name", ""))
        else:
            api_map = _norm_map(map_field or "")

        if _norm_map(self.bot.selected_map) != api_map:
            log.warning(
                "Report rejected: selected map %s does not match API map %s",
                self.bot.selected_map,
                api_map,
            )
            await ctx.send(
                "Map doesn't match your most recent match. Unable to report it.",
                ephemeral=True,
            )
            return

        # Get total rounds played from the match data
        teams = match.get("teams", [])
        if teams:
            total_rounds = metadata.get("rounds_played") or metadata.get("total_rounds")
            if not total_rounds:
                rounds_data = match.get("rounds") or []
                total_rounds = len(rounds_data)
            try:
                total_rounds = int(total_rounds)
            except (TypeError, ValueError):
                await ctx.send(
                    "Could not read the round count from the match data; "
                    "try again once the match finishes processing.",
                    ephemeral=True,
                )
                return
        else:
            await ctx.send("No team data found in match data.", ephemeral=True)
            return

        match_players = match.get("players", [])
        if not match_players:
            await ctx.send("No players found in match data.", ephemeral=True)
            return

        # Resolve every queued player to their Discord id (the persistent
        # identity) plus their current Riot name/tag (only a lookup label).
        # The Discord id is authoritative; Riot IDs can change at any time.
        queue_members = []
        for player in self.bot.queue:
            user_data = users.find_one({"discord_id": str(player["id"])})
            if user_data:
                queue_members.append(
                    {
                        "discord_id": str(player["id"]),
                        "puuid": (user_data.get("puuid") or "").strip().lower(),
                        "name": user_data.get("name", "").lower(),
                        "tag": user_data.get("tag", "").lower(),
                    }
                )

        # Map each API player back to a queue member's Discord id. Prefer the
        # puuid when both sides have one; fall back to the Riot name/tag.
        def _resolve_api_player(api_player):
            api_puuid = (api_player.get("puuid") or "").strip().lower()
            api_name = (api_player.get("name") or "").lower()
            api_tag = (api_player.get("tag") or "").lower()

            for member in queue_members:
                if api_puuid and member["puuid"] and api_puuid == member["puuid"]:
                    return member["discord_id"]
            for member in queue_members:
                if api_name == member["name"] and api_tag == member["tag"]:
                    return member["discord_id"]
            return None

        queue_riot_ids = {(member["name"], member["tag"]) for member in queue_members}

        log.debug("Queued players RIOT ID's: %s", queue_riot_ids)

        # get the list of players in the match
        match_player_names = set()
        for player in match_players:
            player_name = player.get("name", "").lower()
            player_tag = player.get("tag", "").lower()
            match_player_names.add((player_name, player_tag))

        log.debug("match_player_names from API: %s", match_player_names)

        if not queue_riot_ids.issubset(match_player_names):
            # Find which players don't match
            missing_players = queue_riot_ids - match_player_names
            log.warning(
                "Report rejected: API match is missing queue players %s",
                sorted(missing_players),
            )
            mismatch_message = (
                "The most recent match does not match the 10-man's match.\n\n"
            )
            mismatch_message += (
                "The following players' Riot IDs don't match the game data:\n"
            )

            for name, tag in missing_players:
                mismatch_message += f"• {tracker_link(name, tag)}\n"

            mismatch_message += "\nPossible reasons:\n"
            mismatch_message += (
                "1. Did you or someone make a change to their Riot name/tag?\n"
            )
            mismatch_message += "2. Are you trying to report the correct match?\n\n"
            mismatch_message += "If you changed your Riot ID, please use `/linkriot NewName#NewTag` to update it."

            await ctx.send(mismatch_message, ephemeral=True)
            return

        # Determine which team won
        teams = match.get("teams", [])
        if not teams:
            await ctx.send("No team data found in match data.", ephemeral=True)
            return

        winning_team_id = None
        for team in teams:
            if team.get("won"):
                winning_team_id = team.get("team_id", "").lower()
                break

        log.debug("Winning team: %s", winning_team_id)
        if not winning_team_id:
            await ctx.send("Could not determine the winning team.", ephemeral=True)
            return

        match_team_players = {"red": {}, "blue": {}}
        for player_info in match_players:
            raw_team_id = player_info.get("team_id", "").lower()  # "red" or "blue"
            p_discord_id = _resolve_api_player(player_info)
            if raw_team_id in match_team_players and p_discord_id:
                match_team_players[raw_team_id][p_discord_id] = player_info

        team1_ids_set = {str(player["id"]) for player in self.bot.team1}
        team2_ids_set = {str(player["id"]) for player in self.bot.team2}

        log.debug("team1 discord ids: %s", team1_ids_set)
        log.debug("team2 discord ids: %s", team2_ids_set)

        winning_match_team_ids = set(match_team_players.get(winning_team_id, {}))
        log.debug("Winning team Discord ID's: %s", winning_match_team_ids)

        if winning_match_team_ids == team1_ids_set:
            playing_team_ids = [str(p["id"]) for p in self.bot.team1] + [
                str(p["id"]) for p in self.bot.team2
            ]
        elif winning_match_team_ids == team2_ids_set:
            playing_team_ids = [str(p["id"]) for p in self.bot.team2] + [
                str(p["id"]) for p in self.bot.team1
            ]
        else:
            await ctx.send(
                "Could not match the winning team to our teams.", ephemeral=True
            )
            return

        for player_id in playing_team_ids:
            self.bot.ensure_player_mmr(player_id, self.bot.player_names)

        # Get top players
        self.bot.player_mmr = {str(k): v for k, v in self.bot.player_mmr.items()}
        pre_update_mmr = copy.deepcopy(self.bot.player_mmr)
        # Per-player match ratings for the post-match summary embed.
        player_ratings: dict[str, float] = {}

        # Doubledown multipliers are snapshotted before any MMR changes so
        # update_stats can apply them to the match delta only.
        double_down_multipliers = (
            {pid: doubledown_multiplier_of(self.bot, pid) for pid in playing_team_ids}
            if duck_coins_enabled()
            else {}
        )
        # Who actually doubled down (multiplier > 1), for the summary embed
        # tag. Snapshot at the same time as the multipliers so a refund that
        # clears double_downs mid-report can't flip the tag afterwards.
        doubledown_ids = {
            pid for pid, mult in double_down_multipliers.items() if mult > 1
        }

        # Snapshot each player's leaderboard rank before this match is applied
        pre_played_ids = {
            pid
            for pid, stats in pre_update_mmr.items()
            if stats.get("matches_played", 0) > 0
            or (stats.get("wins", 0) + stats.get("losses", 0)) > 0
        }
        pre_update_ranks = {
            pid: rank
            for rank, (pid, _) in enumerate(
                sorted(
                    (
                        (pid, stats)
                        for pid, stats in pre_update_mmr.items()
                        if pid in pre_played_ids
                    ),
                    key=lambda x: x[1]["mmr"],
                    reverse=True,
                ),
                start=1,
            )
        }

        valid_mmr_entries = [
            (pid, stats)
            for pid, stats in pre_update_mmr.items()
            if isinstance(stats, dict) and "mmr" in stats
        ]

        if valid_mmr_entries:
            sorted_mmr_before = sorted(
                valid_mmr_entries, key=lambda x: x[1]["mmr"], reverse=True
            )
            top_mmr_before = sorted_mmr_before[0][1]["mmr"]
            top_players_before = [
                str(pid)
                for pid, stats in sorted_mmr_before
                if stats["mmr"] == top_mmr_before
            ]
        else:
            top_mmr_before = 0
            top_players_before = []

        # Helper: discord id -> team label (the discord id is the persistent
        # identity; Riot name/tag is only a display label)
        discord_to_teamlabel = {}
        for p in self.bot.team1:
            discord_to_teamlabel[str(p["id"])] = "team1"
        for p in self.bot.team2:
            discord_to_teamlabel[str(p["id"])] = "team2"

        team1_ids = [str(p["id"]) for p in self.bot.team1]
        team2_ids = [str(p["id"]) for p in self.bot.team2]

        def _mmr_of(pid):
            d = pre_update_mmr.get(pid)
            if isinstance(d, dict):
                return int(d.get("mmr", 0))
            return 0

        # Estimated VLR Rating 2.0 per player (puuid-keyed). Best effort: a
        # missing/unusable kill timeline just means no ratings this match.
        try:
            match_ratings = estimate_ratings_v4(match)
        except Exception as e:
            log.warning("VLR rating estimation failed, skipping: %s", e)
            match_ratings = {}

        # Per-team MMR averages feed the new ΔMMR expectation term. New
        # players count as 0 for team selection, but once the match is
        # reported their seed (100×VLR) is assigned first and included in
        # the team average before deltas are applied (issue #159).
        team1_api_by_id = {
            _resolve_api_player(p): p for p in match_players if _resolve_api_player(p)
        }

        def _rating_of(pid):
            p = team1_api_by_id.get(pid)
            puuid = (p.get("puuid") or "").strip().lower() if p else ""
            return (match_ratings.get(puuid) or {}).get("rating")

        def _is_new(pid):
            stats = pre_update_mmr.get(pid)
            if not isinstance(stats, dict):
                return True
            return (
                stats.get("matches_played", 0) == 0
                and (stats.get("wins", 0) + stats.get("losses", 0)) == 0
            )

        def _effective_mmr(pid):
            if not _is_new(pid):
                return _mmr_of(pid)
            rating = _rating_of(pid)
            if isinstance(rating, (int, float)) and rating == rating:
                return max(0, round(100.0 * float(rating)))
            return 0

        team1_avg = (
            sum(_effective_mmr(pid) for pid in team1_ids) / len(team1_ids)
            if team1_ids
            else 0
        )
        team2_avg = (
            sum(_effective_mmr(pid) for pid in team2_ids) / len(team2_ids)
            if team2_ids
            else 0
        )

        # discord id -> API team color ("red"/"blue")
        discord_to_api_color = {}
        for p in match_players:
            p_discord_id = _resolve_api_player(p)
            if p_discord_id:
                discord_to_api_color[p_discord_id] = (p.get("team_id") or "").lower()

        # Helper to get the API color
        def _team_api_color(team_players):
            for pl in team_players:
                color = discord_to_api_color.get(str(pl["id"]))
                if color:
                    return color
            return None

        team1_api_color = _team_api_color(self.bot.team1)
        team2_api_color = _team_api_color(self.bot.team2)

        api_rounds = {}
        for t in teams:
            tid = (t.get("team_id") or "").lower()
            rw_raw = t.get("rounds_won", t.get("rounds", 0))
            rw = rounds_to_int(rw_raw)
            api_rounds[tid] = rw

        team1_rounds = int(api_rounds.get(team1_api_color, 0))
        team2_rounds = int(api_rounds.get(team2_api_color, 0))

        # All validation is done; every path below writes to the database.
        # Consume the claim now: a failure from here on must NOT release it,
        # because a retry would re-apply MMR/coins on top of the partial write.
        if on_commit is not None:
            on_commit()

        # Update stats for each player
        for player_stats in match_players:
            p_discord_id = _resolve_api_player(player_stats)
            if not p_discord_id:
                log.info(
                    "API player %s#%s is not in the queue; skipping",
                    player_stats.get("name"),
                    player_stats.get("tag"),
                )
                continue
            team_label = discord_to_teamlabel.get(p_discord_id)
            if not team_label:
                continue

            p_puuid = (player_stats.get("puuid") or "").strip().lower()
            rating_info = match_ratings.get(p_puuid) or {}
            if isinstance(rating_info.get("rating"), (int, float)):
                player_ratings[p_discord_id] = float(rating_info["rating"])

            update_stats(
                player_stats,
                total_rounds,
                self.bot.player_mmr,
                self.bot.player_names,
                discord_id=p_discord_id,
                team_avg_mmr=(team1_avg if team_label == "team1" else team2_avg),
                opp_avg_mmr=team2_avg if team_label == "team1" else team1_avg,
                our_rounds=(team1_rounds if team_label == "team1" else team2_rounds),
                opp_rounds=(team2_rounds if team_label == "team1" else team1_rounds),
                rating=rating_info.get("rating"),
                mmr_multiplier=(
                    double_down_multipliers.get(p_discord_id, 1)
                    if duck_coins_enabled()
                    else 1
                ),
            )
        log.info("Basic stats updated for all players")

        await ctx.send("Match stats and MMR updated!")
        log.info(
            "Match %s reported by %s (winner: %s)",
            metadata.get("match_id", "?"),
            ctx.author,
            winning_team_id,
        )

        # ------------------------------------------------------------
        # Duck Coins: betting payout and +1 coin per match played
        # (feature-gated on the duck_coins flag in bot.ini). The
        # doubledown multiplier was applied inside update_stats above.
        # ------------------------------------------------------------
        winner_side = (
            "attackers" if winning_match_team_ids == team1_ids_set else "defenders"
        )
        bet_settlement_embed = None
        if duck_coins_enabled():
            award_match_coins(playing_team_ids)
            try:
                # Pays out immediately; returns the summary embed to post
                # after the match results embed (kept for display order).
                bet_settlement_embed = await settle_bets(self.bot, winner_side)
            except Exception as e:
                log.error("Bet settlement failed: %s", e, exc_info=e)

        # Build a per-player MMR gain/loss summary
        match_name = getattr(self.bot, "match_name", "") or "match-?"
        mmr_lines = []
        for label, team, rounds in (
            ("Attackers", self.bot.team1, team1_rounds),
            ("Defenders", self.bot.team2, team2_rounds),
        ):
            entries = []
            for p in team:
                pid = str(p["id"])
                old = pre_update_mmr.get(pid, {}).get("mmr", 0)
                new = self.bot.player_mmr.get(pid, {}).get("mmr", 0)
                delta = new - old
                rating = player_ratings.get(pid)
                rating_part = f"({rating:.2f})" if rating is not None else ""
                sign = "+" if delta >= 0 else ""
                # Doubledown marker: bold the delta and tag doubled players
                # with the coin emote ×2, so it's visible who paid to double
                # their MMR change this match.
                doubled = pid in doubledown_ids
                delta_part = f"**{sign}{delta}**" if doubled else f"{sign}{delta}"
                tag_part = " ×2" if doubled else ""
                # Placement marker (issue #211): first match this season, so
                # the MMR shown is the fresh 100×VLR seed rather than a
                # normal delta.
                if _is_new(pid):
                    tag_part += " placement"
                # Mention by Discord ID (<@id>) rather than the Riot ID: the
                # summary posts in #10-mans, where a live mention is the
                # clearest way to see who gained/lost MMR (and it survives
                # Riot renames and purged links).
                entries.append(f"<@{pid}>{rating_part}: {delta_part}{tag_part}")
            mmr_lines.append((f"{label} ({rounds})", "\n".join(entries)))

        results_embed = discord.Embed(
            title=f"Match Summary | {match_name}",
            color=discord.Color.green(),
        )
        for label, entries_text in mmr_lines:
            results_embed.add_field(name=label, value=entries_text, inline=True)
        footer_parts = []
        if doubledown_ids:
            footer_parts.append(
                f"×2 = doubledown ({DOUBLEDOWN_COST} coins): this match's MMR change doubled"
            )
        if any(_is_new(str(p["id"])) for p in self.bot.team1 + self.bot.team2):
            footer_parts.append(
                "placement = first match this season: MMR seeded from this game's performance"
            )
        if footer_parts:
            results_embed.set_footer(text=" · ".join(footer_parts))

        # Issue #204: rank-tier changes this match (unranked → ranked, or
        # moving up/down between traditional MMR tiers). Optional section
        # below the MMR gain/loss fields; omitted when nobody changed tier.
        rank_changes = []
        for p in self.bot.team1 + self.bot.team2:
            pid = str(p["id"])
            pre_stats = pre_update_mmr.get(pid, {})
            post_stats = self.bot.player_mmr.get(pid, {})
            tier_before = (
                rank_of(pre_stats.get("mmr", 0)) if has_played(pre_stats) else None
            )
            tier_after = (
                rank_of(post_stats.get("mmr", 0)) if has_played(post_stats) else None
            )
            if tier_before == tier_after:
                continue
            if tier_before is None:
                rank_changes.append(
                    f"<@{pid}> is now ranked: {role_mention(ctx.guild, tier_after)}"
                )
            elif tier_after is None:
                # Can't lose rank by playing: MMR never decreases to unranked
                # here (a played player always keeps at least one match).
                continue
            else:
                before_pos = next(
                    i for i, (_, name, _) in enumerate(RANKS) if name == tier_before
                )
                after_pos = next(
                    i for i, (_, name, _) in enumerate(RANKS) if name == tier_after
                )
                arrow = "⬆️" if after_pos < before_pos else "⬇️"
                rank_changes.append(
                    f"<@{pid}> {arrow} {role_mention(ctx.guild, tier_before)} → "
                    f"{role_mention(ctx.guild, tier_after)}"
                )
        if rank_changes:
            results_embed.add_field(
                name="🏅 Rank Changes",
                value="\n".join(rank_changes),
                inline=False,
            )

        # Post the results in the persistent #10-mans channel; the match
        # channel gets deleted during cleanup, so posting there would lose
        # the summary.
        results_channel = None
        if ctx.guild:
            for channel in ctx.guild.text_channels:
                if channel.name.lower() == "10-mans":
                    results_channel = channel
                    break
        if results_channel:
            await results_channel.send(embed=results_embed)
        else:
            await ctx.send(embed=results_embed)

        # Bet settlement summary right after the match results embed — in
        # #10-mans ONLY. Betting chatter lives in the persistent channel; if
        # #10-mans can't be found the settlement is skipped rather than
        # spilling into another channel. Coins were already paid during
        # settlement; this post is display only.
        if bet_settlement_embed is not None:
            if results_channel:
                try:
                    await results_channel.send(embed=bet_settlement_embed)
                except discord.HTTPException:
                    log.warning("Could not post the bet settlement summary")
            else:
                log.warning(
                    "Skipped posting the bet settlement summary: no #10-mans channel found"
                )

        self.bot.save_mmr_data()
        log.info("MMR data saved")

        # Record each player's previous leaderboard rank so the
        # leaderboard can display rank gain/loss since the last match
        played_ids = {
            pid
            for pid, stats in self.bot.player_mmr.items()
            if stats.get("matches_played", 0) > 0
            or (stats.get("wins", 0) + stats.get("losses", 0)) > 0
        }
        new_ranks = {
            pid: rank
            for rank, (pid, _) in enumerate(
                sorted(
                    (
                        (pid, stats)
                        for pid, stats in self.bot.player_mmr.items()
                        if pid in played_ids
                    ),
                    key=lambda x: x[1].get("mmr", 0),
                    reverse=True,
                ),
                start=1,
            )
        }
        for discord_id in self.bot.player_mmr:
            previous_rank = pre_update_ranks.get(discord_id)
            new_rank = new_ranks.get(discord_id)
            mmr_collection.update_one(
                {"player_id": discord_id},
                {
                    "$set": {
                        "previous_rank": previous_rank,
                        "current_rank": new_rank,
                    }
                },
                upsert=True,
            )

        log.info("All stats saved to database")

        sorted_mmr_after = sorted(
            self.bot.player_mmr.items(), key=lambda x: x[1]["mmr"], reverse=True
        )
        top_mmr_after = sorted_mmr_after[0][1]["mmr"]
        top_players_after = [
            pid for pid, stats in sorted_mmr_after if stats["mmr"] == top_mmr_after
        ]

        new_top_players = set(top_players_after) - set(top_players_before)
        if new_top_players:
            for new_top_player_id in new_top_players:
                user_data = users.find_one({"discord_id": str(new_top_player_id)})
                # A rank-1 announcement only makes sense with a linked account;
                # unlinked players (no Riot ID) keep stats but skip the banner.
                if user_data and user_data.get("name") and user_data.get("tag"):
                    riot_name = user_data.get("name", "Unknown").lower()
                    riot_tag = user_data.get("tag", "Unknown").lower()
                    # Try to send to 'announcements' channel if it exists
                    announcement_channel = None
                    if ctx.guild:
                        for channel in ctx.guild.text_channels:
                            if channel.name.lower() == "announcements":
                                announcement_channel = channel
                                break
                    ssr = role_mention(ctx.guild, SSR_NAME)
                    message = f"{tracker_link(riot_name, riot_tag)} is now {ssr}!"
                    if announcement_channel:
                        await announcement_channel.send(message)
                    else:
                        await ctx.send(message)

        # Sync each player's rank roles to their new MMR (no rank role until
        # the first match of the season; rank 1 overall also wears Supersonic
        # Radiant on top of their traditional tier).
        if ctx.guild:
            played_sorted = [
                (pid, stats)
                for pid, stats in sorted_mmr_after
                if stats.get("matches_played", 0) > 0
                or (stats.get("wins", 0) + stats.get("losses", 0)) > 0
            ]
            for position, (pid, stats) in enumerate(played_sorted):
                try:
                    await sync_player_rank(
                        self.bot,
                        ctx.guild,
                        pid,
                        stats.get("mmr", 0),
                        is_rank_one=(position == 0),
                    )
                except Exception as e:
                    log.warning("Rank sync failed for %s: %s", pid, e)

        # Record every match played in a new collection
        all_matches.insert_one(match)

        # Increment Current Season Match Count
        seasons.update_one(
            {"_id": "current"}, {"$inc": {"matches_played": 1}}, upsert=True
        )

        # Grant the persistent Season-# role to everyone who played
        try:
            await grant_season_roles(ctx.guild, self.bot.team1 + self.bot.team2)
        except Exception as e:
            log.error("Failed to grant season roles: %s", e, exc_info=e)

        # Issue #213: with voice_presence enabled, move everyone still in the
        # Attackers/Defenders team channels back to #lobby. Players who
        # already left or moved themselves elsewhere are left alone. Must
        # run BEFORE the team lists are reset below.
        if voice_presence_enabled() and ctx.guild:
            try:
                await move_players_to_lobby(ctx.guild, self.bot.team1 + self.bot.team2)
            except Exception as e:
                log.warning("Lobby move failed: %s", e)

        await asyncio.sleep(5)
        self.bot.match_not_reported = False
        self.bot.match_ongoing = False
        # Reset remaining match state so !cancel reports "nothing to cancel"
        # instead of pretending a match is still active.
        self.bot.selected_map = None
        self.bot.chosen_mode = None
        self.bot.captain1 = None
        self.bot.captain2 = None
        self.bot.team1 = []
        self.bot.team2 = []
        self.bot.double_downs = set()
        self.bot.map_override_last = 0
        self.bot.map_override_last_by = None
        self.bot.map_override_deadline = None
        self.bot.map_override_chain = []
        # Bets were already settled above; this just clears an empty session
        # (and refunds a stray bet if settlement never ran). Map override
        # coins are NOT refunded here: the match happened, the coins bought
        # the map that was played.
        refund_open_bets(self.bot)
        await cleanup_match_resources(self.bot)


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
        if numeric_vals:
            return int(max(numeric_vals))
        return 0

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
