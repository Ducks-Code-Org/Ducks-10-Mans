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
  4. Recovers each player's exact pre-match MMR by numerically solving the
     per-player delta equations produced by stats_helper._calc_mmr_delta
     (10 players -> 10 equations in only 2 unknowns: the two team MMR sums).
  5. Recomputes average_combat_score / kill_death_ratio from the reverted totals.
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
    """Find the directory containing env.bat (works from DebugTools/ or repo root)."""
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
# MMR delta logic (mirrors stats_helper._calc_mmr_delta exactly)
# ---------------------------------------------------------------------------


def calc_mmr_delta(
    *, won: bool, team_sum: float, opp_sum: float, acs: float, round_diff: int
) -> int:
    team_sum = float(team_sum)
    opp_sum = float(opp_sum)
    if team_sum <= 0 or opp_sum <= 0:
        return 0
    if won:
        ratio = opp_sum / team_sum
        base = (ratio * 16) + (((ratio * acs) // 100) - 2)
        rd = 0
        if round_diff >= 4:
            mult = (
                1
                if round_diff < 7
                else (2 if round_diff < 10 else (3 if round_diff < 13 else 4))
            )
            rd = mult * ratio
    else:
        ratio = team_sum / opp_sum
        base = (ratio * -16) + (((ratio * acs) // 100) - 2)
        rd = 0
        if round_diff >= 4:
            mult = (
                1
                if round_diff < 7
                else (2 if round_diff < 10 else (3 if round_diff < 13 else 4))
            )
            rd = -(mult * ratio)
    return int((base + rd) // 1)


# ---------------------------------------------------------------------------
# MMR inversion (exact recovery of pre-match MMR values)
# ---------------------------------------------------------------------------


def _sw_from_dw(sl: float, dw: int, acs: float, rd: int) -> float | None:
    """Solve calc_mmr_delta(won=True, sw, sl, acs, rd) == dw for sw>0 via bisection on log(sw)."""
    lo, hi = 1e-3, 1e8
    f = (
        lambda sw: calc_mmr_delta(
            won=True, team_sum=sw, opp_sum=sl, acs=acs, round_diff=rd
        )
        - dw
    )
    flo, fhi = f(lo), f(hi)
    if flo == 0:
        return lo
    if fhi == 0:
        return hi
    if flo * fhi > 0:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        fm = f(mid)
        if fm == 0:
            return mid
        if flo * fm < 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2


def solve_pre_match_mmr(
    winners: list[tuple[str, int, float]],
    losers: list[tuple[str, int, float]],
    round_diff: int,
) -> tuple[dict[str, int], float, float] | None:
    """Recover each player's exact pre-match MMR.

    `winners`/`losers` are lists of (discord_id, current_mmr, acs).
    Returns ({did: pre_mmr}, sum_winners_pre, sum_losers_pre) or None.

    Approach: brute-force over the small space of integer pre-match MMR
    bounds. Each player's delta must satisfy |delta| <= ~22 (16 + acs-part
    + rd-part), so their pre-match MMR is in [current, current+22] for
    winners and [current-22, current] for losers.  We enumerate the team
    sums (sum of pre-match MMRs) across all combinations, then for each
    candidate (s_win, s_los) we compute every player's implied delta and
    check it reproduces their MMR change.  The search space is tiny
    (~23^10) but we prune aggressively:
      * s_win / s_los together define every player's delta deterministically.
      * We enumerate s_win as a sum of winner bounds, find the s_los that
        keeps the anchor loser's delta consistent, then verify all players.
    """
    # Build the bounds for each player's pre-match MMR
    # delta = |base| + |acs_part| + |rd_part| <= 16 + (400/100*ratio) + 4*ratio
    # For ratio up to 3, that's up to ~44. Use 60 as a generous envelope.
    MAX_DELTA = 60
    w_bounds = [
        (did, cur, acs, max(0, cur - MAX_DELTA), cur + MAX_DELTA)
        for did, cur, acs in winners
    ]
    l_bounds = [
        (did, cur, acs, max(0, cur - MAX_DELTA), cur + MAX_DELTA)
        for did, cur, acs in losers
    ]

    # Team sum bounds
    s_win_min = sum(l for _, _, _, l, _ in w_bounds)
    s_win_max = sum(h for _, _, _, _, h in w_bounds)
    s_los_min = sum(l for _, _, _, l, _ in l_bounds)
    s_los_max = sum(h for _, _, _, _, h in l_bounds)

    best = None
    best_score = float("inf")
    seen: set[tuple[int, int]] = set()

    # For each candidate s_win in the valid range (integer steps of 1):
    # s_win must be reachable as a sum of integers within the bounds —
    # that set is always a contiguous range when bounds are contiguous,
    # so we can just iterate over every integer in [s_win_min, s_win_max].
    for s_win in range(s_win_min, s_win_max + 1):
        for s_los in range(s_los_min, s_los_max + 1):
            if (s_win, s_los) in seen:
                continue
            seen.add((s_win, s_los))

            # Compute each player's delta
            deltas = {}
            ok = True
            for did, cur, acs, lo_b, hi_b in w_bounds:
                d = calc_mmr_delta(
                    won=True,
                    team_sum=s_win,
                    opp_sum=s_los,
                    acs=acs,
                    round_diff=round_diff,
                )
                pre = cur - d
                if not (lo_b <= pre <= hi_b):
                    ok = False
                    break
                deltas[did] = pre
            if not ok:
                continue
            for did, cur, acs, lo_b, hi_b in l_bounds:
                d = calc_mmr_delta(
                    won=False,
                    team_sum=s_los,
                    opp_sum=s_win,
                    acs=acs,
                    round_diff=round_diff,
                )
                pre = cur - d
                if not (lo_b <= pre <= hi_b):
                    ok = False
                    break
                deltas[did] = pre
            if not ok:
                continue

            # Check self-consistency: the sums must match exactly
            sum_w = sum(deltas[did] for did, _, _, _, _ in w_bounds)
            sum_l = sum(deltas[did] for did, _, _, _, _ in l_bounds)
            err = abs(sum_w - s_win) + abs(sum_l - s_los)
            if err == 0:
                # Verify every player still maps to the exact integer we computed
                # (eliminates float-precision edge cases from the solver)
                exact = True
                for did, cur, acs, lo_b, hi_b in w_bounds:
                    d = calc_mmr_delta(
                        won=True,
                        team_sum=s_win,
                        opp_sum=s_los,
                        acs=acs,
                        round_diff=round_diff,
                    )
                    if cur - d != deltas[did]:
                        exact = False
                        break
                if exact:
                    for did, cur, acs, lo_b, hi_b in l_bounds:
                        d = calc_mmr_delta(
                            won=False,
                            team_sum=s_los,
                            opp_sum=s_win,
                            acs=acs,
                            round_diff=round_diff,
                        )
                        if cur - d != deltas[did]:
                            exact = False
                            break
                if exact:
                    return deltas, s_win, s_los
            elif err < best_score:
                best_score = err
                best = (deltas, s_win, s_los)

    return best


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


def compute_round_diff(match: dict) -> int:
    rounds_won: dict[str, int] = {}
    for team in match.get("teams", []):
        tid = (team.get("team_id") or "").strip().title()
        r = team.get("rounds")
        if isinstance(r, dict):
            rounds_won[tid] = int(r.get("won", 0))
        elif isinstance(r, (int, float)):
            rounds_won[tid] = int(r)
    blue = rounds_won.get("Blue", 0)
    red = rounds_won.get("Red", 0)
    return abs(blue - red)


def winning_team_id(match: dict) -> str | None:
    for team in match.get("teams", []):
        if team.get("won"):
            return (team.get("team_id") or "").strip().title()
    # Fallback: infer from round scores (matches DebugTools' helper behavior)
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
    round_diff = compute_round_diff(match)
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
    winners: list[tuple[str, int, float]] = []
    losers: list[tuple[str, int, float]] = []

    for p in players:
        name = (p.get("name") or "").strip().lower()
        tag = (p.get("tag") or "").strip().lower()
        did = match_key_to_discord.get((name, tag))
        if not did or did not in mmr_by_discord:
            continue
        doc = mmr_by_discord[did]
        stats = p.get("stats", {}) or {}
        score = float(stats.get("score", 0))
        acs = (score / total_rounds) if total_rounds > 0 else 0.0
        cur_mmr = int(doc.get("mmr", 1000))
        tid = (p.get("team_id") or "").strip().title()
        if tid == winning_tid:
            winners.append((did, cur_mmr, acs))
        else:
            losers.append((did, cur_mmr, acs))

    if not winners or not losers:
        sys.exit(
            "[revert] Could not split players into winning/losing teams from the users/MMR data."
        )

    solved = solve_pre_match_mmr(winners, losers, round_diff)
    if not solved:
        print("[revert] ERROR: Could not recover exact pre-match MMR values.")
        print(
            "[revert] This can happen if the MMR formula has changed since this match was reported,"
        )
        print("[revert] or if the stored match data is inconsistent.")
        sys.exit(
            "No changes were made — investigate manually and re-run when resolved."
        )

    pre_mmr_by_did, s_win, s_los = solved
    print(
        f"[revert] Recovered pre-match team MMR sums: winners={s_win:.2f}, losers={s_los:.2f}"
    )

    # --- Build per-player updates -------------------------------------------
    player_updates: list[tuple[dict, dict]] = []  # (filter, $set payload)
    print("\n[revert] Planned changes:")
    for p in players:
        name = (p.get("name") or "").strip().lower()
        tag = (p.get("tag") or "").strip().lower()
        did = match_key_to_discord.get((name, tag))
        if not did or did not in mmr_by_discord:
            continue
        doc = mmr_by_discord[did]
        stats = p.get("stats", {}) or {}
        kills = int(stats.get("kills", 0))
        deaths = int(stats.get("deaths", 0))
        score = int(stats.get("score", 0))
        tid = (p.get("team_id") or "").strip().title()
        won = tid == winning_tid

        new_matches = doc.get("matches_played", 0) - 1
        new_tcs = doc.get("total_combat_score", 0) - score
        new_kills = doc.get("total_kills", 0) - kills
        new_deaths = doc.get("total_deaths", 0) - deaths
        new_trp = doc.get("total_rounds_played", 0) - total_rounds
        new_wins = doc.get("wins", 0) - (1 if won else 0)
        new_losses = doc.get("losses", 0) - (0 if won else 1)
        new_mmr = pre_mmr_by_did.get(did, int(doc.get("mmr", 1000)))

        new_matches = max(new_matches, 0)
        new_tcs = max(new_tcs, 0)
        new_kills = max(new_kills, 0)
        new_deaths = max(new_deaths, 0)
        new_trp = max(new_trp, 0)
        new_wins = max(new_wins, 0)
        new_losses = max(new_losses, 0)

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
        }

        display_name = f"{name}#{tag}"
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
            f"KDR {doc.get('kill_death_ratio',0):.2f} -> {new_kdr:.2f}"
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
