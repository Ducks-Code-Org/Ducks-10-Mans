"""Merge duplicate mmr_data documents that share the same player_id.

Usage:
    python DebugTools/dedupe_mmr_docs.py            # diagnose only (no writes)
    python DebugTools/dedupe_mmr_docs.py --fix      # merge & remove duplicates

Root cause (issue #144): mmr_data has no unique index on player_id, so
historical upserts could create multiple documents per player. When that
happened, load_mmr_data() kept whichever duplicate cursor order returned
last, which could silently zero out a player's stats (e.g. sen
babymcnerd#uwu, who has two docs — one stale "pookie babynerd#uwu" doc).

The merge keeps the doc with the most real stats (matches_played, then
wins+losses, then insertion order) and deletes the rest.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from pymongo import MongoClient
from pymongo.server_api import ServerApi


def find_repo_root() -> Path:
    d = Path(__file__).resolve().parent
    for candidate in (d, *d.parents):
        if (candidate / "env.bat").exists():
            return candidate
    return d


REPO_ROOT = find_repo_root()


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


def get_client() -> MongoClient:
    uri = os.getenv("uri_key")
    if not uri:
        _load_env_bat(REPO_ROOT / "env.bat")
        uri = os.getenv("uri_key")
    if not uri:
        sys.exit("[dedupe] Could not find uri_key in environment or env.bat")
    client = MongoClient(
        uri,
        tlsAllowInvalidCertificates=True,
        server_api=ServerApi("1"),
        serverSelectionTimeoutMS=10000,
    )
    client.admin.command("ping")
    return client


def _doc_score(doc: dict) -> tuple:
    return (
        doc.get("matches_played", 0),
        doc.get("wins", 0) + doc.get("losses", 0),
        doc.get("total_kills", 0) + doc.get("total_deaths", 0),
    )


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--fix", action="store_true", help="Merge and delete duplicate documents."
    )
    args = parser.parse_args()

    client = get_client()
    db = client["valorant"]
    mmr_collection = db["mmr_data"]

    by_pid: dict[str, list[dict]] = {}
    for doc in mmr_collection.find():
        pid = doc.get("player_id")
        if pid is None:
            continue
        by_pid.setdefault(pid, []).append(doc)

    dupes = {pid: docs for pid, docs in by_pid.items() if len(docs) > 1}
    print(f"[dedupe] Found {len(dupes)} player_id(s) with duplicate docs:")
    for pid, docs in sorted(dupes.items(), key=lambda kv: str(kv[0])):
        keep = max(docs, key=_doc_score)
        drop = [d for d in docs if d["_id"] != keep["_id"]]
        print(f"  player_id={pid!r}: keeping {keep['name']} ({keep['_id']})")
        for d in drop:
            print(f"    dropping duplicate {d.get('name', '?')} ({d['_id']})")
    if not dupes:
        print("  None!")
        return

    if args.fix:
        for pid, docs in dupes.items():
            keep = max(docs, key=_doc_score)
            result = mmr_collection.delete_many(
                {"player_id": pid, "_id": {"$ne": keep["_id"]}}
            )
            print(
                f"[dedupe] player_id={pid!r}: deleted {result.deleted_count} duplicate(s)"
            )
        print("[dedupe] Done. Re-run without --fix to verify.")
    else:
        print("\n[dedupe] Run with --fix to merge and delete the duplicates.")


if __name__ == "__main__":
    main()
