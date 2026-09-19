"""Self-checks for !linkriot ownership conflicts and admin-lock hardening.

A Riot ID may only be linked to one Discord account. Linking an ID another
account already owns must be rejected WITHOUT deleting the owner's user or
MMR docs; and !cancel must serialize against the !report lock so it can't
tear down a match mid-report.
"""

import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeCollection:
    """In-memory stand-in for the pymongo collections !linkriot touches."""

    def __init__(self, docs=None):
        self.docs = list(docs or [])

    @staticmethod
    def _match(doc, f):
        if not f:
            return True
        if "$or" in f:
            return any(FakeCollection._match(doc, sub) for sub in f["$or"])
        return all(doc.get(k) == v for k, v in f.items())

    def find(self, f=None, *a, **k):
        return [d for d in self.docs if self._match(d, f)]

    def find_one(self, f=None, *a, **k):
        return next(iter(self.find(f)), None)

    def update_one(self, f, update, upsert=False):
        for doc in self.docs:
            if self._match(doc, f):
                doc.update(update.get("$set", {}))
                return
        if upsert:
            new = dict(update.get("$set", {}))
            self.docs.append(new)

    def delete_one(self, f, *a, **k):
        before = len(self.docs)
        self.docs[:] = [d for d in self.docs if not self._match(d, f)]
        return types.SimpleNamespace(deleted_count=before - len(self.docs))


# Stub import-time side effects (Mongo connection) before importing.
_database_stub = types.ModuleType("database")
_database_stub.users = FakeCollection()
_database_stub.mmr_collection = FakeCollection()
sys.modules["database"] = _database_stub

_discord_stub = types.ModuleType("discord")
_discord_stub.NotFound = type("NotFound", (Exception,), {})
_discord_stub.HTTPException = type("HTTPException", (Exception,), {})
_discord_stub.Forbidden = type("Forbidden", (Exception,), {})
_discord_stub.ext = types.SimpleNamespace()
_discord_stub.ext.commands = types.SimpleNamespace(
    command=lambda *a, **k: (lambda f: f),
    has_permissions=lambda **k: (lambda f: f),
    has_role=lambda *a, **k: (lambda f: f),
    Cog=type("Cog", (), {"__init_subclass__": classmethod(lambda cls, **kw: None)}),
)
sys.modules["discord"] = _discord_stub
sys.modules["discord.ext"] = _discord_stub.ext
sys.modules["discord.ext.commands"] = _discord_stub.ext.commands


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


import commands.linkriot as lk  # noqa: E402

OWNER_DOC = {
    "_id": "o1",
    "discord_id": "111",
    "name": "foo",
    "tag": "bar",
    "puuid": "puuid-1",
}
OWNER_MMR = {"player_id": "111", "mmr": 1400, "name": "foo#bar"}


class FakeCtx:
    def __init__(self, user_id="222"):
        self.author = types.SimpleNamespace(id=int(user_id), name="claimer")
        self.sent = []

    async def send(self, content=None, **kw):
        self.sent.append(content)


def reset():
    lk.users.docs = [dict(OWNER_DOC)]
    lk.mmr_collection.docs = [dict(OWNER_MMR)]


def patch_account(payload):
    async def fake_get(session, name, tag, **k):
        return payload

    lk.get_account_by_riot_id = fake_get


def demo():
    _orig_session = lk.aiohttp.ClientSession
    lk.aiohttp.ClientSession = _FakeSession
    try:
        # 1. Claiming another account's Riot ID is rejected; the owner's
        #    user and MMR docs are untouched (no stats deletion).
        reset()
        patch_account({"_raw": True, "puuid": "puuid-1"})
        ctx = FakeCtx("222")
        asyncio.run(lk.LinkRiotCommand.linkriot(None, ctx, riot_input="foo#bar"))
        assert any("already linked" in str(m) for m in ctx.sent), ctx.sent
        assert lk.users.docs == [OWNER_DOC], "owner's user doc must survive"
        assert lk.mmr_collection.docs == [
            OWNER_MMR
        ], "owner's MMR stats must never be deleted"

        # 2. A renamed-but-same-puuid account also conflicts (puuid match).
        reset()
        patch_account({"_raw": True, "puuid": "puuid-1"})
        ctx = FakeCtx("222")
        asyncio.run(lk.LinkRiotCommand.linkriot(None, ctx, riot_input="newname#newtag"))
        assert any("already linked" in str(m) for m in ctx.sent), ctx.sent
        assert lk.mmr_collection.docs == [OWNER_MMR]
        assert lk.users.docs == [OWNER_DOC]

        # 3. The owner re-linking their own ID still works (upsert, no dup).
        reset()
        patch_account({"_raw": True, "puuid": "puuid-1"})
        ctx = FakeCtx("111")
        asyncio.run(lk.LinkRiotCommand.linkriot(None, ctx, riot_input="foo#bar"))
        assert any("Successfully linked" in str(m) for m in ctx.sent), ctx.sent
        assert len(lk.users.docs) == 1, "re-link must not duplicate the user doc"
        assert lk.users.docs[0]["discord_id"] == "111"

        # 4. A genuinely new account links fine and only touches its own doc.
        reset()
        patch_account({"_raw": True, "puuid": "puuid-2"})
        ctx = FakeCtx("222")
        asyncio.run(lk.LinkRiotCommand.linkriot(None, ctx, riot_input="brand#new"))
        assert any("Successfully linked" in str(m) for m in ctx.sent), ctx.sent
        assert len(lk.users.docs) == 2
        assert lk.mmr_collection.docs == [OWNER_MMR], "owner stats still intact"
    finally:
        lk.aiohttp.ClientSession = _orig_session

    # 5. !cancel must serialize against the report lock (source contract).
    admin_src = open(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "commands",
            "admin_commands.py",
        ),
        encoding="utf-8",
    ).read()
    cancel_body = admin_src.split("async def cancel", 1)[1].split(
        "async def _cancel_locked", 1
    )[0]
    assert "report_lock" in cancel_body, "!cancel must take the report lock"
    assert "_cancel_locked" in cancel_body, "!cancel must run its work inside the lock"

    print("all linkriot/cancel-lock self-checks passed")


if __name__ == "__main__":
    demo()
