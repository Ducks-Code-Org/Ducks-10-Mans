"""This file provides functions for updating players stats."""

import logging

from database import mmr_collection

log = logging.getLogger(__name__)

# ΔMMR coefficients: rounds-diff, team-MMR expectation (result-conditional),
# and VLR-skill-curve terms. The result branch makes a loss never pay the
# win bonus (B·(2−m) → B·(1−m)); see MMR_EXPLAINER.md.
# 2026-09-24: A 10→12 (round differentials matter more), C 20/7→3 (slightly
# stronger skill spread); the old "carry" corner-bonus term was removed.
# Also 2026-09-24: the h-curve's no-bonus floor moved from vlr 0.5 to 0.4
# (ratings 0.4–0.7 now climb to (0.7, 1) at slope 10/3).
# 2026-09-26: result-conditional minimum swing (see docs/adr/0001):
# every win pays at least MIN_GAIN, every loss costs at least MIN_LOSS.
A, B, C = 12.0, 60 / 7, 3.0

# Minimum swing per result, applied to the raw delta before any multiplier
# (doubledown doubles the clamped delta). Draws are not possible.
MIN_GAIN, MIN_LOSS = 5.0, -5.0

# ponytail: DEFAULT_MMR 0 is the new-player display fallback; first report
# seeds real MMR from 100*VLR rating.
DEFAULT_MMR = 0


# Round-weighted VLR rating accumulation helpers -----------------------------
# "avg_rating" is the round-weighted mean of every recorded per-match rating:
# total_rating_points / total_rating_rounds. Kept as additive totals so
# each new match just adds its rating and rounds; N/A is represented by
# total_rating_rounds == 0 (or a missing field for pre-existing players).
def _apply_rating(player_data: dict, rating: float, rounds: int) -> None:
    if rounds <= 0 or not isinstance(rating, (int, float)) or rating != rating:
        return
    player_data["total_rating_points"] = (
        player_data.get("total_rating_points", 0.0) + float(rating) * rounds
    )
    player_data["total_rating_rounds"] = (
        player_data.get("total_rating_rounds", 0) + rounds
    )
    player_data["avg_rating"] = (
        player_data["total_rating_points"] / player_data["total_rating_rounds"]
    )


def _rating_fields(player_data: dict) -> dict:
    # avg_rating is always derived from the totals, never read from a
    # possibly-missing/stale in-memory key, so a match with no rating
    # can't clobber a player's recorded average.
    return {
        "total_rating_points": player_data.get("total_rating_points", 0.0),
        "total_rating_rounds": player_data.get("total_rating_rounds", 0),
        "avg_rating": avg_rating_of(player_data),
    }


def avg_rating_of(player_data: dict) -> float | None:
    """Round-weighted average VLR rating, or None when none recorded.

    Derived from the additive totals so it is always consistent, even for
    players loaded before the rating fields existed.
    """
    rounds = player_data.get("total_rating_rounds", 0)
    if not rounds:
        return None
    return player_data.get("total_rating_points", 0.0) / rounds


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _h(v: float) -> float:
    """VLR skill curve through (0.4,0) (0.7,1) (1.0,4) (1.3,5)."""
    if v <= 0.4:
        return 0.0
    if v < 0.7:
        return (10.0 / 3.0) * (v - 0.4)
    if v < 1.0:
        return 1.0 + 10.0 * (v - 0.7)
    return 4.0 + (v - 1.0) / 0.3


def delta_mmr(
    our_rounds: float, opp_rounds: float, our_mmr: float, opp_mmr: float, vlr: float
) -> float:
    """ΔMMR = A·r + B_result + C·(h(vlr) − 4), min ±5 by result.

    r = round differential / 4.3, clamped to ±1
    B_result = B·(2−m) on a win (our_rounds > opp_rounds), B·(1−m) on a
        loss (the recentred branch)
    m = team-MMR expectation: sqrt(ratio) if underdog, ratio^0.75 if
        favorite, clamped [0.33, 5]
    h = piecewise VLR curve (0.4,0)(0.7,1)(1.0,4)(1.3,5), uncapped above 1.3
    The raw delta is then clamped to the result-conditional minimum swing:
    a win never pays less than MIN_GAIN, a loss never more than MIN_LOSS.
    """
    r = _clamp((our_rounds - opp_rounds) / 4.3, -1.0, 1.0)
    ratio = _clamp(our_mmr / max(opp_mmr, 1e-9), 0.33, 5.0)
    m = ratio**0.5 if ratio < 1.0 else ratio**0.75
    expectation = B * (2.0 - m) if our_rounds > opp_rounds else B * (1.0 - m)
    delta = A * r + expectation + C * (_h(vlr) - 4.0)
    return max(delta, MIN_GAIN) if our_rounds > opp_rounds else min(delta, MIN_LOSS)


def _seed_mmr(rating) -> float:
    """First-match MMR seed: 100× this match's estimated VLR rating."""
    if isinstance(rating, (int, float)) and rating == rating:  # NaN check
        return 100.0 * float(rating)
    return float(DEFAULT_MMR)


# Update stats
def update_stats(
    player_stats,
    total_rounds,
    player_mmr,
    player_names,
    *,
    discord_id=None,
    team_avg_mmr=None,
    opp_avg_mmr=None,
    our_rounds=None,
    opp_rounds=None,
    rating=None,
    mmr_multiplier: int = 1,
):
    """Update player stats with proper initialization and error handling"""
    if discord_id is None:
        log.warning(
            "Player %s#%s could not be resolved to a Discord account.",
            player_stats.get("name", ""),
            player_stats.get("tag", ""),
        )
        return

    discord_id = str(discord_id)

    # Get the stats with proper defaults
    stats = player_stats.get("stats", {})
    score = stats.get("score", 0)
    kills = stats.get("kills", 0)
    deaths = stats.get("deaths", 0)

    if discord_id in player_mmr:
        player_data = player_mmr[discord_id]
        # Initialize missing fields with defaults
        player_data.setdefault("matches_played", 0)
        player_data.setdefault("total_combat_score", 0)
        player_data.setdefault("total_kills", 0)
        player_data.setdefault("total_deaths", 0)
        player_data.setdefault("total_rounds_played", 0)
        player_data.setdefault("total_rating_points", 0.0)
        player_data.setdefault("total_rating_rounds", 0)
        player_data.setdefault("mmr", DEFAULT_MMR)

        # Update stats
        total_matches = player_data["matches_played"] + 1
        total_combat_score = player_data["total_combat_score"] + score
        total_kills = player_data["total_kills"] + kills
        total_deaths = player_data["total_deaths"] + deaths
        total_rounds_played = player_data["total_rounds_played"] + total_rounds

        # Calculate averages
        average_combat_score = (
            total_combat_score / total_rounds_played if total_rounds_played > 0 else 0
        )
        kill_death_ratio = (
            total_kills / total_deaths if total_deaths > 0 else total_kills
        )

        _apply_rating(player_data, rating, total_rounds)
        player_mmr[discord_id].update(
            {
                "total_combat_score": total_combat_score,
                "total_kills": total_kills,
                "total_deaths": total_deaths,
                "matches_played": total_matches,
                "total_rounds_played": total_rounds_played,
                "average_combat_score": average_combat_score,
                "kill_death_ratio": kill_death_ratio,
                **_rating_fields(player_data),
            }
        )

        won = (
            our_rounds is not None
            and opp_rounds is not None
            and our_rounds > opp_rounds
        )
        if team_avg_mmr is not None and opp_avg_mmr is not None:
            first_match = (
                player_data["matches_played"] == 1
                and (player_data.get("wins", 0) + player_data.get("losses", 0)) == 0
            )
            # First match this season: MMR starts at 100× this match's VLR
            # rating (the same estimate the performance-based delta uses);
            # veterans just accumulate the delta on top of current MMR.
            base = _seed_mmr(rating) if first_match else float(player_data["mmr"])
            delta = delta_mmr(
                our_rounds=our_rounds or 0,
                opp_rounds=opp_rounds or 0,
                our_mmr=float(team_avg_mmr),
                opp_mmr=float(opp_avg_mmr),
                vlr=float(rating) if isinstance(rating, (int, float)) else 1.0,
            )
            # The multiplier (e.g. doubledown) doubles only this match's
            # delta, never the first-match seed.
            new_mmr = max(0, round(base + delta * mmr_multiplier))
            player_mmr[discord_id]["mmr"] = new_mmr

            # Wins/Losses total
            if won:
                player_mmr[discord_id]["wins"] = (
                    player_mmr[discord_id].get("wins", 0) + 1
                )
            else:
                player_mmr[discord_id]["losses"] = (
                    player_mmr[discord_id].get("losses", 0) + 1
                )

        # Update database with all fields
        mmr_collection.update_one(
            {"player_id": discord_id},
            {
                "$set": {
                    "mmr": player_mmr[discord_id].get("mmr", DEFAULT_MMR),
                    "wins": player_mmr[discord_id].get("wins", 0),
                    "losses": player_mmr[discord_id].get("losses", 0),
                    "total_combat_score": total_combat_score,
                    "total_rounds_played": total_rounds_played,
                    "matches_played": total_matches,
                    "average_combat_score": average_combat_score,
                    "kill_death_ratio": kill_death_ratio,
                    "total_kills": total_kills,
                    "total_deaths": total_deaths,
                    **_rating_fields(player_data),
                }
            },
            upsert=True,
        )

    else:
        # Initialize new player stats
        riot_name = f"{player_stats.get('name', '').lower()}#{player_stats.get('tag', '').lower()}"
        total_matches = 1
        total_combat_score = score
        total_kills = kills
        total_deaths = deaths
        total_rounds_played = total_rounds
        average_combat_score = (
            total_combat_score / total_rounds_played if total_rounds_played > 0 else 0
        )
        kill_death_ratio = (
            total_kills / total_deaths if total_deaths > 0 else total_kills
        )

        player_mmr[discord_id] = {
            "mmr": DEFAULT_MMR,
            "wins": 0,
            "losses": 0,
            "total_combat_score": total_combat_score,
            "total_kills": total_kills,
            "total_deaths": total_deaths,
            "matches_played": total_matches,
            "total_rounds_played": total_rounds_played,
            "average_combat_score": average_combat_score,
            "kill_death_ratio": kill_death_ratio,
            "total_rating_points": 0.0,
            "total_rating_rounds": 0,
            "avg_rating": None,
        }
        _apply_rating(player_mmr[discord_id], rating, total_rounds)
        player_names[discord_id] = riot_name

        if (
            team_avg_mmr is not None
            and opp_avg_mmr is not None
            and our_rounds is not None
            and opp_rounds is not None
        ):
            # First match this season: MMR starts at 100× this match's VLR
            # rating, then the standard delta is applied on top. The
            # multiplier applies to the delta only.
            won = our_rounds > opp_rounds
            seed = _seed_mmr(rating)
            delta = delta_mmr(
                our_rounds=our_rounds,
                opp_rounds=opp_rounds,
                our_mmr=float(team_avg_mmr),
                opp_mmr=float(opp_avg_mmr),
                vlr=float(rating) if isinstance(rating, (int, float)) else 1.0,
            )
            player_mmr[discord_id]["mmr"] = max(0, round(seed + delta * mmr_multiplier))
            if won:
                player_mmr[discord_id]["wins"] = 1
                player_mmr[discord_id]["losses"] = 0
            else:
                player_mmr[discord_id]["wins"] = 0
                player_mmr[discord_id]["losses"] = 1

        # Insert new player into database with all fields
        mmr_collection.update_one(
            {"player_id": discord_id},
            {
                "$set": {
                    "mmr": player_mmr[discord_id]["mmr"],
                    "wins": player_mmr[discord_id]["wins"],
                    "losses": player_mmr[discord_id]["losses"],
                    "name": riot_name,
                    "total_combat_score": total_combat_score,
                    "total_kills": total_kills,
                    "total_deaths": total_deaths,
                    "matches_played": total_matches,
                    "total_rounds_played": total_rounds_played,
                    "average_combat_score": average_combat_score,
                    "kill_death_ratio": kill_death_ratio,
                    **_rating_fields(player_mmr[discord_id]),
                }
            },
            upsert=True,
        )
