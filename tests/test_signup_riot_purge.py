"""Self-checks for the !signup Riot ID purge (issue #182).

A renamed Riot ID must preserve stats, and an inconclusive API response
(network error, rate limit, 5xx) must never delete a player's user/MMR docs.
"""

import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeCollection:
    """In-memory stand-in for the pymongo collections the purge touches."""

    def __init__(self, docs=None):
        self.docs = list(docs or [])

    @staticmethod
    def _match(doc, f):
        return all(doc.get(k) == v for k, v in (f or {}).items())

    def find(self, f=None, *a, **k):
        return [d for d in self.docs if self._match(d, f)]

    def find_one(self, f=None, *a, **k):
        return next(iter(self.find(f)), None)

    def update_one(self, f, update, upsert=False):
        for doc in self.docs:
            if self._match(doc, f):
                doc.update(update.get("$set", {}))
                return

    def delete_one(self, f, *a, **k):
        self.docs[:] = [d for d in self.docs if not self._match(d, f)]


# Stub modules with import-time side effects (Mongo connection, map pools)
# so this self-check can run without a database or API access.
_database_stub = types.ModuleType("database")
_database_stub.users = FakeCollection()
_database_stub.mmr_collection = FakeCollection()
_database_stub.seasons = types.SimpleNamespace()
_database_stub.all_matches = types.SimpleNamespace()
_database_stub.recent_queue = types.SimpleNamespace()
_database_stub.coin_escrow = types.SimpleNamespace(
    update_one=lambda *a, **k: None, find_one=lambda *a, **k: None
)
sys.modules["database"] = _database_stub

_maps_stub = types.ModuleType("services.maps_service")
_maps_stub.get_competitive_maps = lambda: ["Ascent", "Bind"]
_maps_stub.get_standard_maps = lambda: ["Ascent", "Bind"]
sys.modules["services.maps_service"] = _maps_stub

import commands.signup as su  # noqa: E402
import services.riot_api as riot_api  # noqa: E402

USER_DOC = {
    "_id": "u1",
    "discord_id": "111",
    "name": "oldname",
    "tag": "oldtag",
    "puuid": "puuid-1",
}
MMR_DOC = {"player_id": "111", "mmr": 1400}

_orig_get_json = riot_api._henrik_get_json


def reset(doc=None):
    su.users.docs = [dict(doc or USER_DOC)]
    su.mmr_collection.docs = [dict(MMR_DOC)]


def patch_primary(result):
    """Force the stored-Riot-ID check to a known outcome."""

    async def fake_exists(session, name, tag, **k):
        return result

    su.riot_account_exists_async = fake_exists


def patch_puuid_response(status, data):
    """Drive the real get_account_by_puuid through a canned HTTP response."""

    async def fake_get_json(session, url, **k):
        return (status, data)

    riot_api._henrik_get_json = fake_get_json


RENAMED = {"data": {"puuid": "puuid-1", "name": "NewName", "tag": "NewTag"}}


async def demo():
    try:
        # 1. Riot ID 404 + puuid resolves to a new name: stats preserved.
        reset()
        patch_primary(False)
        patch_puuid_response(200, RENAMED)
        removed = await su.purge_invalid_riot_ids()
        assert removed == [], removed
        assert su.users.docs[0]["name"] == "newname"
        assert su.users.docs[0]["tag"] == "newtag"
        assert su.mmr_collection.docs == [MMR_DOC], "rename wiped MMR data"

        # 2. Network error on the puuid check (status 0): never purged.
        reset()
        patch_primary(False)
        patch_puuid_response(0, None)
        removed = await su.purge_invalid_riot_ids()
        assert removed == [], "network error purged a player"
        assert su.mmr_collection.docs == [MMR_DOC], "stats wiped on network error"

        # 3. Persistent 429 raises inside the helper: never purged.
        reset()
        patch_primary(False)

        async def raise_inconclusive(session, url, **k):
            raise riot_api.RiotApiInconclusive("429 rate limit persisted")

        riot_api._henrik_get_json = raise_inconclusive
        removed = await su.purge_invalid_riot_ids()
        assert removed == [], "rate limit purged a player"
        assert su.mmr_collection.docs == [MMR_DOC], "stats wiped on 429"

        # 4. Unexpected API status (503): never purged.
        reset()
        patch_primary(False)
        patch_puuid_response(503, None)
        removed = await su.purge_invalid_riot_ids()
        assert removed == [], "503 purged a player"
        assert su.mmr_collection.docs == [MMR_DOC], "stats wiped on 503"

        # 5. Inconclusive primary check (network error): never purged.
        reset()
        patch_primary(None)
        patch_puuid_response(404, None)
        removed = await su.purge_invalid_riot_ids()
        assert removed == [], "inconclusive Riot ID check purged a player"

        # 6. Riot ID 404 + puuid also 404: genuinely gone, purged.
        reset()
        patch_primary(False)
        patch_puuid_response(404, None)
        removed = await su.purge_invalid_riot_ids()
        assert removed == ["oldname#oldtag"], removed
        assert su.users.docs == [] and su.mmr_collection.docs == [], "not purged"

        # 7. No stored puuid + Riot ID 404: purged (nothing to resolve with).
        reset({**USER_DOC, "puuid": ""})
        patch_primary(False)
        patch_puuid_response(200, RENAMED)
        removed = await su.purge_invalid_riot_ids()
        assert removed == ["oldname#oldtag"], removed
        assert su.mmr_collection.docs == []
    finally:
        riot_api._henrik_get_json = _orig_get_json

    print("all signup Riot purge self-checks passed")


if __name__ == "__main__":
    asyncio.run(demo())
