"""Revert all player stats affected by the most recently reported 10-man match.

Usage:
    python revert_last_match.py              Revert the newest doc in the `matches`
                                             collection (creates a backup first).
    python revert_last_match.py --dry-run    Preview every change without writing.
    python revert_last_match.py --restore <backup.json>
                                             Roll the database back to a previously
                                             created backup (undo the revert).

The script:
  1. Finds the most recent reported match in the `matches` collection
     (by metadata.started_at, then insertion order).
  2. Backs up all affected db documents to backups/revert_backup_*.json.
  3. Subtracts each player's per-match stat totals (kills, deaths, score,
     rounds, matches_played, wins/losses).
  4. Recovers each player's exact pre-match MMR. Two paths:
       * Matches reported with a persisted `mmr_context` block (written by
         report.py since 2026-09-24) contain the exact inputs — pre-match
         MMRs, team averages, per-side rounds, VLR ratings, and doubledown
         multipliers — so the revert restores those pre-MMRs directly and
         cross-checks them against the live delta_mmr formula.
       * Older matches are inverted numerically against game/stats_helper's
         delta_mmr (imported live, so the tool can never drift from the
         production formula). Doubledown multipliers were not persisted for
         these, so the revert refuses to run when Duck Coins are enabled.
  5. Recomputes average_combat_score / kill_death_ratio from the reverted
     totals, and subtracts the match's VLR rating contribution.
  6. Removes the match document and decrements seasons.current.matches_played.

All writes happen in a single MongoDB transaction, so a failure leaves the
database untouched. The --restore mode provides a second layer of recovery.

Secrets are read from env vars, falling back to parsing env.bat (repo root)
and finally globals.py.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from pathlib import Path

from pymongo import DESCENDING
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi


def find_repo_root() -> Path:
    """Find the directory containing env.bat (works from tools/ops/ or repo root)."""
    d = Path(__file__).resolve().parent
    for candidate in (d, *d.parents):
        if (candidate / "env.bat").exists():
            return candidate
    return d


REPO_ROOT = find_repo_root()
BACKUP_DIR = REPO_ROOT / "backups"

# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------


def _load_env_bat(path: Path) -> None:
    """Inject active `set "key=value"` lines from a .bat file into os.environ."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.lower().startswith("set "):
            continue
        m = re.match(r'set\s+"?([^=\s"]+)=([^"]*)"?\s*$', line, re.IGNORECASE)
        if m:
            os.environ.setdefault(m.group(1), m.group(2))


def _get_setting(name: str) -> str | None:
    """Env var first, then env.bat, then globals.py (which itself reads env vars)."""
    val = os.getenv(name)
    if val:
        return val
    _load_env_bat(REPO_ROOT / "env.bat")
    val = os.getenv(name)
    if val:
        return val
    try:
        os.chdir(REPO_ROOT)
        sys.path.insert(0, str(REPO_ROOT))
        import importlib

        gl = importlib.import_module("globals")
        val = getattr(gl, name.upper(), None) or getattr(gl, name.lower(), None)
        if val:
            return val
    except Exception:
        pass
    return None


def get_client() -> MongoClient:
    uri = _get_setting("uri_key")
    if not uri:
        sys.exit(
            "[revert] Could not find uri_key in environment, env.bat, or globals.py"
        )
    client = MongoClient(
        uri,
        tls=True,
        server_api=ServerApi("1"),
        serverSelectionTimeoutMS=10000,
    )
    client.admin.command("ping")
    return client


# ---------------------------------------------------------------------------
# Live delta_mmr import (the single source of truth for the inversion)
# ---------------------------------------------------------------------------


def _load_delta_mmr():
    """Import the live delta_mmr from game/stats_helper without touching Mongo.

    game/stats_helper imports database, which connects to MongoDB at import
    time and SystemExits if unreachable. The tool only needs the pure math,
    so `database` is stubbed with inert attributes first — the same trick
    the test suite uses.
    """
    import types

    if "game.stats_helper" in sys.modules:
        return sys.modules["game.stats_helper"].delta_mmr

    stub = types.ModuleType("database")
    stub.users = stub.mmr_collection = stub.all_matches = None
    stub.seasons = stub.interests = stub.recent_queue = stub.coin_escrow = None
    stub.client = stub.db = None
    sys.modules["database"] = stub
    sys.path.insert(0, str(REPO_ROOT))
    import game.stats_helper as sh

    return sh.delta_mmr


# ---------------------------------------------------------------------------
# Legacy numeric inversion of the live delta_mmr (matches without mmr_context)
# ---------------------------------------------------------------------------


def solve_pre_match_mmr_legacy(
    delta_mmr,
    players: list[dict],
    rounds_won: int,
    rounds_lost: int,
) -> tuple[dict[str, int], float, float] | None:
    """Recover each player's pre-match MMR by fixed-point iteration.

    `players`: [{"discord_id", "current_mmr", "vlr": float|None,
                 "was_new": bool, "won": bool}]. Winners share rounds_won as
    their our_rounds; losers share rounds_lost. Returns ({did: pre_mmr},
    winners_pre_avg, losers_pre_avg) or None.

    The delta never depends on a player's own pre-MMR — only on the two team
    averages, which are themselves built from the pre-MMRs being solved for.
    report.py averages the WHOLE team, counting each first-match player at
    their 100×vlr seed (0 when this match has no usable rating), so those
    seeds stay fixed while the veterans' pre-MMRs are being solved. The
    mutual dependency is resolved by iteration: start from the current MMRs,
    compute each veteran's pre = post − round(delta), rebuild the averages,
    repeat until stable. Convergence is quick because the delta moves by
    only O(Δavg/10) per step.

    Doubledown multipliers are NOT invertible on this path (they were not
    persisted for legacy matches): a doubled and an undoubled result can
    produce the same post-MMR. Callers must only use it when no player
    doubled down.
    """
    if not players:
        return None

    by_id = {p["discord_id"]: p for p in players}
    winners = [p["discord_id"] for p in players if p["won"]]
    losers = [p["discord_id"] for p in players if not p["won"]]
    new_ids = [p["discord_id"] for p in players if p["was_new"]]
    vet_winners = [d for d in winners if not by_id[d]["was_new"]]
    vet_losers = [d for d in losers if not by_id[d]["was_new"]]
    cur = {p["discord_id"]: int(p["current_mmr"]) for p in players}

    def rating_of(d: str) -> float:
        # update_stats uses vlr=1.0 when this match has no usable rating,
        # while report.py's team average counts a rating-less placement at 0.
        v = by_id[d].get("vlr")
        return float(v) if isinstance(v, (int, float)) and v == v else 1.0

    def seed_of(d: str) -> int:
        v = by_id[d].get("vlr")
        if isinstance(v, (int, float)) and v == v:
            return max(0, round(100.0 * float(v)))
        return 0

    seed = {d: seed_of(d) for d in new_ids}

    def _avg(mmr_by_did: dict[str, float], dids: list[str]) -> float:
        return sum(mmr_by_did[d] for d in dids) / len(dids) if dids else 0.0

    # Team averages exactly as report.py computed them: every player on the
    # side counts, placements at their seed rather than a solved pre-MMR.
    eff = {d: float(cur[d]) for d in cur}
    eff.update(seed)
    w_avg = _avg(eff, winners)
    l_avg = _avg(eff, losers)

    pre: dict[str, int] = {}
    for _ in range(200):
        for did in vet_winners:
            d = delta_mmr(
                our_rounds=rounds_won,
                opp_rounds=rounds_lost,
                our_mmr=w_avg,
                opp_mmr=l_avg,
                vlr=rating_of(did),
            )
            pre[did] = max(0, cur[did] - round(d))
        for did in vet_losers:
            d = delta_mmr(
                our_rounds=rounds_lost,
                opp_rounds=rounds_won,
                our_mmr=l_avg,
                opp_mmr=w_avg,
                vlr=rating_of(did),
            )
            pre[did] = max(0, cur[did] - round(d))
        new_eff = {d: float(m) for d, m in pre.items()}
        new_eff.update(seed)
        new_w = _avg(new_eff, winners)
        new_l = _avg(new_eff, losers)
        if abs(new_w - w_avg) < 0.25 and abs(new_l - l_avg) < 0.25:
            w_avg, l_avg = new_w, new_l
            break
        w_avg, l_avg = new_w, new_l

    # First-match players revert to unplayed: pre-MMR is DEFAULT_MMR (0) and
    # their stats doc goes back to zeroed totals handled by the caller.
    for did in new_ids:
        pre[did] = 0
    for did, c in cur.items():
        pre.setdefault(did, c)
    return pre, w_avg, l_avg


# ---------------------------------------------------------------------------
# BSON -> JSON helpers
# ---------------------------------------------------------------------------


def _jsonify(obj):
    if isinstance(obj, dict):
        return {k: _jsonify(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonify(v) for v in obj]
    if isinstance(obj, datetime.datetime):
        return {"$date": obj.isoformat()}
    try:
        from bson import ObjectId

        if isinstance(obj, ObjectId):
            return {"$oid": str(obj)}
    except Exception:
        pass
    return obj


def _dejsonify(obj):
    if isinstance(obj, dict):
        if "$oid" in obj and len(obj) == 1:
            from bson import ObjectId

            return ObjectId(obj["$oid"])
        if "$date" in obj and len(obj) == 1:
            return datetime.datetime.fromisoformat(obj["$date"])
        return {k: _dejsonify(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_dejsonify(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# Cross-check the persisted mmr_context against the live formula
# ---------------------------------------------------------------------------


def replay_mmr_context(
    delta_mmr,
    player_ctx: dict,
    team_ctx: dict,
    *,
    rating: float | None,
) -> int:
    """Replay one player's stored post-MMR from the persisted mmr_context.

    Mirrors update_stats exactly: first-match players seed at 100×vlr then
    apply the delta (multiplier applies to the delta only); veterans apply
    the delta to their persisted pre-MMR. The 0 floor is applied as
    update_stats does. Used as a sanity check on the persisted inputs.

    `player_ctx` is the player's entry in mmr_context["players"];
    `team_ctx` is the match-level mmr_context (team averages and rounds).
    """
    side = player_ctx.get("side", "team1")
    t_avg = float(team_ctx.get("team1_avg") or 0)
    o_avg = float(team_ctx.get("team2_avg") or 0)
    r_won = int(team_ctx.get("team1_rounds") or 0)
    r_lost = int(team_ctx.get("team2_rounds") or 0)
    if side == "team2":
        t_avg, o_avg = o_avg, t_avg
        r_won, r_lost = r_lost, r_won
    vlr = float(rating) if isinstance(rating, (int, float)) else 1.0
    if player_ctx.get("was_new"):
        base = max(0, round(100.0 * float(rating))) if rating is not None else 0
    else:
        base = int(player_ctx.get("pre_mmr", 0))
    d = delta_mmr(
        our_rounds=r_won,
        opp_rounds=r_lost,
        our_mmr=t_avg,
        opp_mmr=o_avg,
        vlr=vlr,
    )
    return max(0, round(base + d * int(player_ctx.get("multiplier", 1))))


# ---------------------------------------------------------------------------
# Core revert logic
# ---------------------------------------------------------------------------


def find_latest_match(all_matches):
    return all_matches.find_one(
        sort=[("metadata.started_at", DESCENDING), ("_id", DESCENDING)]
    )


def get_total_rounds(match: dict) -> int:
    meta = match.get("metadata", {}) or {}
    rounds = meta.get("rounds_played") or meta.get("total_rounds")
    if rounds:
        return int(rounds)
    return len(match.get("rounds") or [])


def winning_team_id(match: dict) -> str | None:
    for team in match.get("teams", []):
        if team.get("won"):
            return (team.get("team_id") or "").strip().title()
    # Fallback: infer from round scores (matches tools.ops.helpers' behavior)
    rounds_won: dict[str, int] = {}
    for team in match.get("teams", []):
        tid = (team.get("team_id") or "").strip().title()
        r = team.get("rounds")
        if isinstance(r, dict):
            rounds_won[tid] = int(r.get("won", 0))
        elif isinstance(r, (int, float)):
            rounds_won[tid] = int(r)
    blue, red = rounds_won.get("Blue", 0), rounds_won.get("Red", 0)
    if blue > red:
        return "Blue"
    if red > blue:
        return "Red"
    return None


def build_backup(client, db, match, mmr_docs, seasons_doc):
    def _oid_set(docs):
        return [str(d["_id"]) for d in docs]

    return {
        "backup_created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "match_id": match.get("metadata", {}).get("match_id", str(match.get("_id"))),
        "match_started_at": match.get("metadata", {}).get("started_at"),
        "match_mongo_id": str(match.get("_id")),
        "mmr_player_ids": _oid_set([d for d in mmr_docs]),
        "collections": {
            "matches": [_jsonify(match)],
            "mmr_data": [_jsonify(d) for d in mmr_docs],
            "seasons": [_jsonify(seasons_doc)] if seasons_doc else [],
        },
    }


def restore_from_backup(client, db, backup_path: Path):
    backup = json.loads(backup_path.read_text(encoding="utf-8"))
    with client.start_session() as session:
        with session.start_transaction():
            for coll_name, docs in backup["collections"].items():
                coll = db[coll_name]
                for raw in docs:
                    doc = _dejsonify(raw)
                    if coll_name == "seasons" and doc.get("_id") == "current":
                        coll.replace_one({"_id": "current"}, doc, upsert=True)
                    elif coll_name == "matches":
                        # Restore the deleted match doc with its original _id
                        coll.replace_one({"_id": doc["_id"]}, doc, upsert=True)
                    else:
                        coll.replace_one({"_id": doc["_id"]}, doc, upsert=True)
    print(
        f"[revert] Restored {len(backup['collections']['mmr_data'])} mmr_data docs, "
        f"{len(backup['collections']['matches'])} match doc(s), "
        f"{len(backup['collections'].get('seasons', []))} season doc(s) from "
        f"{backup_path.name}"
    )


def revert(client, *, dry_run: bool) -> None:
    db = client["valorant"]
    users = db["users"]
    mmr_collection = db["mmr_data"]
    all_matches = db["matches"]
    seasons = db["seasons"]

    match = find_latest_match(all_matches)
    if not match:
        sys.exit("[revert] No documents in the `matches` collection to revert.")

    meta = match.get("metadata", {}) or {}
    mid = meta.get("match_id", str(match.get("_id")))
    print(
        f"[revert] Match to revert: id={mid} started_at={meta.get('started_at')} "
        f"map={meta.get('map', {}).get('name') if isinstance(meta.get('map'), dict) else meta.get('map')}"
    )

    players = match.get("players", [])
    if not players:
        sys.exit("[revert] Match has no player data; aborting.")

    total_rounds = get_total_rounds(match)
    winning_tid = winning_team_id(match)
    if winning_tid not in ("Blue", "Red"):
        sys.exit(f"[revert] Couldn't determine winner for {mid}; aborting.")

    # --- Gather affected MMR documents -------------------------------------
    match_key_to_discord: dict[tuple[str, str], str] = {}
    missing_links = []
    for p in players:
        name = (p.get("name") or "").strip().lower()
        tag = (p.get("tag") or "").strip().lower()
        match_key_to_discord[(name, tag)] = None  # placeholder
        u = users.find_one({"name": name, "tag": tag})
        if not u:
            missing_links.append(f"{name}#{tag}")
            continue
        match_key_to_discord[(name, tag)] = str(u["discord_id"])

    if missing_links:
        print(
            "[revert] WARNING: these players are not linked to Discord accounts and will be skipped:"
        )
        for n in missing_links:
            print(f"  - {n}")

    mmr_docs = []
    mmr_by_discord: dict[str, dict] = {}
    for (name, tag), did in match_key_to_discord.items():
        if did is None:
            continue
        doc = mmr_collection.find_one({"player_id": did})
        if not doc:
            print(
                f"[revert] WARNING: no mmr_data doc for {name}#{tag} ({did}); skipping"
            )
            continue
        mmr_by_discord[did] = doc
        mmr_docs.append(doc)

    if not mmr_docs:
        sys.exit(
            "[revert] None of the match participants had MMR documents; nothing to do."
        )

    # --- Verify the stored stats include this match's totals ---------------
    for p in players:
        name = (p.get("name") or "").strip().lower()
        tag = (p.get("tag") or "").strip().lower()
        did = match_key_to_discord.get((name, tag))
        if not did or did not in mmr_by_discord:
            continue
        doc = mmr_by_discord[did]
        stats = p.get("stats", {}) or {}
        kills = int(stats.get("kills", 0))
        if doc.get("matches_played", 0) < 1 or doc.get("total_kills", 0) < kills:
            print(
                f"[revert] WARNING: {name}#{tag}'s stored totals look smaller than this match's "
                f"contribution (matches_played={doc.get('matches_played')}, total_kills={doc.get('total_kills')} < {kills}). "
                "The revert may be incorrect for this player."
            )

    # --- Recover pre-match MMRs ----------------------------------------------
    delta_mmr = _load_delta_mmr()
    mmr_context = match.get("mmr_context") or {}

    # Per-player info for the recovery and the update step below.
    info: list[dict] = []
    for p in players:
        name = (p.get("name") or "").strip().lower()
        tag = (p.get("tag") or "").strip().lower()
        did = match_key_to_discord.get((name, tag))
        if not did or did not in mmr_by_discord:
            continue
        doc = mmr_by_discord[did]
        stats = p.get("stats", {}) or {}
        tid = (p.get("team_id") or "").strip().title()
        info.append(
            {
                "did": did,
                "name": f"{name}#{tag}",
                "cur_mmr": int(doc.get("mmr", 1000)),
                "kills": int(stats.get("kills", 0)),
                "deaths": int(stats.get("deaths", 0)),
                "score": int(stats.get("score", 0)),
                "won": tid == winning_tid,
                # First match this season: the doc went 0 → 1 this match.
                "was_new": int(doc.get("matches_played", 0)) == 1,
            }
        )

    if not info:
        sys.exit(
            "[revert] Could not split players into winning/losing teams from the users/MMR data."
        )

    if not any(i["won"] for i in info) or all(i["won"] for i in info):
        sys.exit("[revert] Match has no winning/losing split; aborting.")

    # Per-team rounds from the match doc (winners share one count, losers the
    # other; they sum to total_rounds).
    rounds_by_tid: dict[str, int] = {}
    for team in match.get("teams", []):
        tid = (team.get("team_id") or "").strip().title()
        r = team.get("rounds")
        if isinstance(r, dict):
            rounds_by_tid[tid] = int(r.get("won", 0))
        elif isinstance(r, (int, float)):
            rounds_by_tid[tid] = int(r)
    winner_rounds = rounds_by_tid.get(winning_tid, 0)
    loser_rounds = max(0, total_rounds - winner_rounds)

    # Per-player VLR rating: from mmr_context when present, else estimated
    # from the match payload exactly like report.py did.
    rating_by_did = {
        did: float(c["rating"])
        for did, c in (mmr_context.get("players") or {}).items()
        if isinstance(c.get("rating"), (int, float))
    }
    if not rating_by_did:
        try:
            from services.vlr_rating import estimate_ratings_v4

            match_ratings = estimate_ratings_v4(match) or {}
            for i in info:
                u = users.find_one({"discord_id": i["did"]})
                puuid = (u.get("puuid") or "").strip().lower() if u else ""
                r = (match_ratings.get(puuid) or {}).get("rating")
                if isinstance(r, (int, float)):
                    rating_by_did[i["did"]] = float(r)
        except Exception as e:
            print(
                f"[revert] WARNING: could not estimate VLR ratings ({e}); "
                "rating totals will not be reverted."
            )

    if mmr_context.get("players"):
        # --- Path A: exact pre-MMRs from the persisted context --------------
        pre_mmr_by_did: dict[str, int] = {}
        mismatched = []
        for i in info:
            c = (mmr_context.get("players") or {}).get(i["did"])
            if c is None:
                sys.exit(
                    f"[revert] Match has mmr_context but {i['name']} is missing "
                    "from it; refusing to guess — investigate manually."
                )
            pre_mmr_by_did[i["did"]] = int(c["pre_mmr"])
            # Cross-check: replay the stored post-MMR through the live
            # formula. A mismatch means the formula changed since the match
            # was reported; the persisted pre-MMR is still the exact truth.
            expect = replay_mmr_context(
                delta_mmr,
                c,
                mmr_context,
                rating=rating_by_did.get(i["did"]),
            )
            if expect != i["cur_mmr"]:
                mismatched.append(i["name"])
        if mismatched:
            print(
                "[revert] NOTE: replaying the persisted inputs through the live "
                "delta_mmr does not reproduce the stored post-MMRs for: "
                f"{', '.join(mismatched)}. The formula likely changed since this "
                "match was reported — the persisted pre-match values are still "
                "exact and will be restored."
            )
        w_sum = sum(pre_mmr_by_did[i["did"]] for i in info if i["won"])
        l_sum = sum(pre_mmr_by_did[i["did"]] for i in info if not i["won"])
        print(
            f"[revert] Pre-match MMRs restored from the match's persisted mmr_context "
            f"(winners sum={w_sum}, losers sum={l_sum})"
        )
    else:
        # --- Path B: numeric inversion of the live formula (legacy match) ---
        # Doubledown multipliers were not persisted for legacy matches, so
        # this path is only safe when nobody doubled down. That cannot be
        # proven after the fact; refuse when duck coins are enabled now,
        # which is when doubledowns could have been bought.
        try:
            from game.duck_coins import duck_coins_enabled

            if duck_coins_enabled():
                sys.exit(
                    "[revert] This match predates persisted MMR contexts and Duck Coins are "
                    "currently enabled — a doubledown multiplier cannot be reconstructed, "
                    "so exact MMR recovery is impossible. Restore a backup instead "
                    "(python tools/ops/revert_last_match.py --restore <file>)."
                )
        except SystemExit:
            raise
        except Exception:
            pass  # duck_coins config unreadable; assume disabled and warn
        print(
            "[revert] WARNING: legacy match (no persisted mmr_context); inverting "
            "the live delta_mmr assuming no doubledowns. Verify the plan below."
        )

        legacy_players = [
            {
                "discord_id": i["did"],
                "current_mmr": i["cur_mmr"],
                "vlr": rating_by_did.get(i["did"]),
                "was_new": i["was_new"],
                "won": i["won"],
            }
            for i in info
        ]
        solved = solve_pre_match_mmr_legacy(
            delta_mmr, legacy_players, winner_rounds, loser_rounds
        )
        if not solved:
            print("[revert] ERROR: Could not recover exact pre-match MMR values.")
            print(
                "[revert] This can happen if the MMR formula has changed since this match was reported,"
            )
            print("[revert] or if the stored match data is inconsistent.")
            sys.exit(
                "No changes were made — investigate manually and re-run when resolved."
            )
        pre_mmr_by_did, w_sum, l_sum = solved
        print(
            f"[revert] Recovered pre-match team MMR sums: winners={w_sum:.2f}, losers={l_sum:.2f}"
        )

    # --- Build per-player updates -------------------------------------------
    player_updates: list[tuple[dict, dict]] = []  # (filter, $set payload)
    print("\n[revert] Planned changes:")
    for i in info:
        did = i["did"]
        doc = mmr_by_discord[did]
        new_matches = doc.get("matches_played", 0) - 1
        new_tcs = doc.get("total_combat_score", 0) - i["score"]
        new_kills = doc.get("total_kills", 0) - i["kills"]
        new_deaths = doc.get("total_deaths", 0) - i["deaths"]
        new_trp = doc.get("total_rounds_played", 0) - total_rounds
        new_wins = doc.get("wins", 0) - (1 if i["won"] else 0)
        new_losses = doc.get("losses", 0) - (0 if i["won"] else 1)
        new_mmr = pre_mmr_by_did.get(did, i["cur_mmr"])

        new_matches = max(new_matches, 0)
        new_tcs = max(new_tcs, 0)
        new_kills = max(new_kills, 0)
        new_deaths = max(new_deaths, 0)
        new_trp = max(new_trp, 0)
        new_wins = max(new_wins, 0)
        new_losses = max(new_losses, 0)

        # VLR rating totals: subtract this match's rating×rounds contribution
        # (mirrors stats_helper._apply_rating; no-op when no rating exists).
        new_points = doc.get("total_rating_points", 0.0)
        new_rounds_rated = doc.get("total_rating_rounds", 0)
        vlr = rating_by_did.get(did)
        if isinstance(vlr, (int, float)):
            new_points = max(0.0, new_points - float(vlr) * total_rounds)
            new_rounds_rated = max(0, new_rounds_rated - total_rounds)
        new_avg_rating = (
            (new_points / new_rounds_rated) if new_rounds_rated > 0 else None
        )

        new_acs = (new_tcs / new_trp) if new_trp > 0 else 0
        new_kdr = (new_kills / new_deaths) if new_deaths > 0 else new_kills

        set_payload = {
            "mmr": new_mmr,
            "wins": new_wins,
            "losses": new_losses,
            "matches_played": new_matches,
            "total_combat_score": new_tcs,
            "total_kills": new_kills,
            "total_deaths": new_deaths,
            "total_rounds_played": new_trp,
            "average_combat_score": new_acs,
            "kill_death_ratio": new_kdr,
            "total_rating_points": new_points,
            "total_rating_rounds": new_rounds_rated,
            "avg_rating": new_avg_rating,
        }

        display_name = i["name"]
        print(f"  {display_name}:")
        print(
            f"    mmr {doc.get('mmr')} -> {new_mmr}   W/L {doc.get('wins',0)}/{doc.get('losses',0)} -> {new_wins}/{new_losses}"
        )
        print(
            f"    matches {doc.get('matches_played',0)} -> {new_matches}   "
            f"TRP {doc.get('total_rounds_played',0)} -> {new_trp}   "
            f"TCS {doc.get('total_combat_score',0)} -> {new_tcs}"
        )
        print(
            f"    K/D {doc.get('total_kills',0)}/{doc.get('total_deaths',0)} -> {new_kills}/{new_deaths}   "
            f"ACS {doc.get('average_combat_score',0):.2f} -> {new_acs:.2f}   "
            f"KDR {doc.get('kill_death_ratio',0):.2f} -> {new_kdr:.2f}   "
            f"avgVLR {doc.get('avg_rating', '—')} -> {new_avg_rating}"
        )
        player_updates.append(({"player_id": did}, set_payload))

    seasons_doc = seasons.find_one({"_id": "current"})
    new_season_count = None
    if seasons_doc:
        new_season_count = max(0, seasons_doc.get("matches_played", 0) - 1)
        print(
            f"  seasons.current.matches_played: "
            f"{seasons_doc.get('matches_played', 0)} -> {new_season_count}"
        )

    print(f"  matches: remove document _id={match['_id']} (match_id={mid})")

    # --- Backup --------------------------------------------------------------
    backup = build_backup(client, db, match, mmr_docs, seasons_doc)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"revert_backup_{mid}_{stamp}.json"
    backup_path.write_text(json.dumps(backup, indent=2), encoding="utf-8")
    print(f"\n[revert] Backup written to {backup_path}")

    if dry_run:
        print("[revert] Dry run complete — no database changes made.")
        return

    # --- Apply (transactional) ----------------------------------------------
    with client.start_session() as session:
        with session.start_transaction():
            for filt, payload in player_updates:
                mmr_collection.update_one(filt, {"$set": payload}, session=session)
            if seasons_doc:
                seasons.update_one(
                    {"_id": "current"},
                    {"$set": {"matches_played": new_season_count}},
                    upsert=False,
                    session=session,
                )
            all_matches.delete_one({"_id": match["_id"]}, session=session)

    print(
        f"\n[revert] Success. Reverted {len(player_updates)} player doc(s), "
        f"removed match {mid}, decremented season counter to {new_season_count}."
    )
    print(
        f"[revert] To undo this revert, run: python {Path(__file__).name} --restore {backup_path}"
    )


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the planned changes without writing to the database.",
    )
    parser.add_argument(
        "--restore",
        metavar="BACKUP_JSON",
        help="Restore the database from a backup file created by a previous run.",
    )
    args = parser.parse_args()

    client = get_client()
    db = client["valorant"]

    if args.restore:
        restore_from_backup(client, db, Path(args.restore))
        return

    revert(client, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
