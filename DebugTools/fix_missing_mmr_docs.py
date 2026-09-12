"""Diagnose missing mmr_data documents for players in the users collection.

Usage:
    python DebugTools/fix_missing_mmr_docs.py            # diagnose only (no writes)
    python DebugTools/fix_missing_mmr_docs.py --fix      # create missing default docs

Root cause of the 'sen babymcnerd#uwu' bug:
  1. ensure_player_mmr() skips players already in self.player_mmr (in-memory).
  2. sen babymcnerd#uwu was in player_mmr from load_mmr_data() with all-zero stats.
  3. update_stats() updated their stats in memory, but since ensure_player_mmr did
     nothing for them (already exists), their record was a barebones default.
  4. save_mmr_data() upserts only the changed fields; since this was their first
     match, the DB doc ended up with mmr=1000 and everything else at 0.

This script finds any user with a linked Riot account that has no mmr_data
document (or an all-zero one that shouldn't exist), and creates the proper
default record.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from pymongo import DESCENDING
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi


def find_repo_root() -> Path:
    d = Path(__file__).resolve().parent
    for candidate in (d, *d.parents):
        if (candidate / "env.bat").exists():
            return candidate
    return d


REPO_ROOT = find_repo_root()
BACKUP_DIR = REPO_ROOT / "backups"


def _load_env_bat(path: Path) -> None:
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
    val = os.getenv(name)
    if val:
        return val
    _load_env_bat(REPO_ROOT / "env.bat")
    val = os.getenv(name)
    if val:
        return val
    try:
        sys.path.insert(0, str(REPO_ROOT))
        gl = __import__("globals")
        return getattr(gl, name.upper(), None)
    except Exception:
        pass
    return None


def get_client() -> MongoClient:
    uri = _get_setting("uri_key")
    if not uri:
        sys.exit("[fix] Could not find uri_key in environment, env.bat, or globals.py")
    client = MongoClient(
        uri,
        tls=True,
        server_api=ServerApi("1"),
        serverSelectionTimeoutMS=10000,
    )
    client.admin.command("ping")
    return client


DEFAULT_MMR_DOC = {
    "mmr": 1000,
    "wins": 0,
    "losses": 0,
    "total_combat_score": 0,
    "total_kills": 0,
    "total_deaths": 0,
    "matches_played": 0,
    "total_rounds_played": 0,
    "average_combat_score": 0,
    "kill_death_ratio": 0,
}


def diagnose(db, *, fix: bool = False):
    users = db["users"]
    mmr_collection = db["mmr_data"]
    all_matches = db["matches"]

    # Build name->user map from the user collection
    users_by_did: dict[str, dict] = {}
    for u in users.find():
        users_by_did[u["discord_id"]] = u

    # Find the most recent match to check which players have played
    latest = all_matches.find_one(sort=[("metadata.started_at", DESCENDING)])
    players_in_latest = {}
    if latest:
        for p in latest.get("players", []):
            key = ((p.get("name") or "").lower(), (p.get("tag") or "").lower())
            players_in_latest[key] = p

    issues = []
    to_create: list[str] = []  # discord_ids to create/reset to defaults

    for did, u in users_by_did.items():
        name = u.get("name", "").lower()
        tag = u.get("tag", "").lower()
        mmr_doc = mmr_collection.find_one({"player_id": did})

        if mmr_doc is None:
            # No MMR doc at all — check if they've played any matches
            if (name, tag) in players_in_latest:
                issues.append(
                    f"NO_MMR_DOC: {name}#{tag} (id={did}): no mmr_data doc, "
                    f"but played in the most recent match"
                )
                if fix:
                    to_create.append(did)
            else:
                issues.append(
                    f"NO_MMR_DOC: {name}#{tag} (id={did}): no mmr_data doc "
                    f"(no recent matches either)"
                )
        elif mmr_doc.get("matches_played", 0) == 0 and (name, tag) in players_in_latest:
            # Doc exists but is all zeros and they DID play in the latest match
            issues.append(
                f"EMPTY_MMR_DOC: {name}#{tag} (id={did}): all-zero stats "
                f"but played in the most recent match (likely from double-report race condition)"
            )
            if fix:
                to_create.append(did)
        elif (
            mmr_doc.get("matches_played", 0) == 0
            and mmr_doc.get("total_combat_score", 0) == 0
        ):
            # Doc exists with all zeros and no matches — this is fine for new players
            pass

    print(f"[fix] Found {len(issues)} issue(s):")
    for issue in issues:
        print(f"  {issue}")
    if not issues:
        print("  None!")

    if fix and to_create:
        print(f"\n[fix] Resetting {len(to_create)} document(s) to defaults...")
        for did in to_create:
            u = users_by_did.get(did, {})
            name = u.get("name", "unknown")
            tag = u.get("tag", "unknown")
            mmr_collection.update_one(
                {"player_id": did},
                {
                    "$set": {
                        "player_id": did,
                        "name": f"{name}#{tag}",
                        **DEFAULT_MMR_DOC,
                    }
                },
                upsert=True,
            )
            print(f"  Reset to defaults: {name}#{tag} (id={did})")
    elif fix:
        print("[fix] Nothing to fix.")
    else:
        print("\n[fix] Run with --fix to create/reset the missing documents.")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--fix", action="store_true", help="Create missing default mmr_data documents."
    )
    args = parser.parse_args()

    client = get_client()
    db = client["valorant"]
    diagnose(db, fix=args.fix)


if __name__ == "__main__":
    main()
