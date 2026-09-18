import asyncio
import gzip
import json
import os
import sys
import tempfile
import types
from datetime import datetime, timezone
from pathlib import Path

from bson import ObjectId

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub modules with import-time side effects (Mongo connection) so this
# self-check can run without a database.
_database_stub = types.ModuleType("database")
_database_stub.users = types.SimpleNamespace(find_one=lambda *a, **k: None)
_database_stub.mmr_collection = types.SimpleNamespace(
    update_one=lambda *a, **k: None,
    find_one=lambda *a, **k: None,
    find=lambda *a, **k: [],
    delete_one=lambda *a, **k: None,
)
_database_stub.seasons = types.SimpleNamespace()
_database_stub.all_matches = types.SimpleNamespace()
_database_stub.recent_queue = types.SimpleNamespace()
_database_stub.coin_escrow = types.SimpleNamespace(
    update_one=lambda *a, **k: None, find_one=lambda *a, **k: None
)
_database_stub.client = types.SimpleNamespace()
sys.modules["database"] = _database_stub


class _FakeEmbed:
    def __init__(self, *a, **k):
        pass


_discord_stub = types.ModuleType("discord")
_discord_stub.Embed = _FakeEmbed
_discord_stub.File = lambda *a, **k: types.SimpleNamespace(
    fp=a[0] if a else None, filename=k.get("filename", "")
)
_discord_stub.utils = types.SimpleNamespace(get=lambda *a, **k: None)
_discord_stub.NotFound = type("NotFound", (Exception,), {})
_discord_stub.HTTPException = type("HTTPException", (Exception,), {})
_discord_stub.ext = types.SimpleNamespace()
_discord_stub.ext.commands = types.SimpleNamespace(
    command=lambda *a, **k: (lambda f: f),
    has_permissions=lambda **k: (lambda f: f),
    Cog=type("Cog", (), {"__init_subclass__": classmethod(lambda cls, **kw: None)}),
)
sys.modules["discord"] = _discord_stub
sys.modules["discord.ext"] = _discord_stub.ext
sys.modules["discord.ext.commands"] = _discord_stub.ext.commands

import commands.maintenance_commands as mc  # noqa: E402
from tools.ops.revert_last_match import _dejsonify, _jsonify  # noqa: E402


class FakeCollection:
    """In-memory pymongo stand-in supporting only the filters these commands use."""

    def __init__(self, docs=None):
        self.docs = list(docs or [])

    def _match(self, doc, f):
        if not f:
            return True
        if "$or" in f:
            return any(self._match(doc, sub) for sub in f["$or"])
        if "_id" in f and isinstance(f["_id"], dict) and "$ne" in f["_id"]:
            return doc.get("_id") != f["_id"]["$ne"]
        for k, v in f.items():
            if isinstance(v, dict) and "$exists" in v:
                if (k in doc) != v["$exists"]:
                    return False
            elif doc.get(k) != v:
                return False
        return True

    def find(self, f=None, *a, **k):
        return [d for d in self.docs if self._match(d, f or {})]

    def find_one(self, f=None, *a, **k):
        for d in self.docs:
            if self._match(d, f or {}):
                return d
        return None

    def delete_many(self, f=None):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not self._match(d, f or {})]
        return types.SimpleNamespace(deleted_count=before - len(self.docs))

    def insert_one(self, doc):
        self.docs.append(doc)
        return types.SimpleNamespace(inserted_id=doc.get("_id"))

    def replace_one(self, f, doc, upsert=False):
        for i, d in enumerate(self.docs):
            if self._match(d, f):
                self.docs[i] = doc
                return types.SimpleNamespace(matched_count=1)
        if upsert:
            self.docs.append(doc)
            return types.SimpleNamespace(matched_count=0)
        return types.SimpleNamespace(matched_count=0)


class FakeBot:
    def __init__(self):
        self.report_lock = asyncio.Lock()
        self.load_mmr_data = lambda: None
        self.bet_session = None
        self.double_downs = set()
        self.map_override_last = 0
        self.map_override_last_by = None
        self.map_override_deadline = None


class FakeMessage:
    def __init__(self, attachments):
        self.attachments = attachments


class FakeAttachment:
    def __init__(self, filename, data):
        self.filename = filename
        self._data = data

    async def read(self):
        return self._data


class FakeCtx:
    def __init__(self, message):
        self.author = "tester"
        self.message = message
        self.sent = []

    async def send(self, *a, **k):
        self.sent.append((a, k))


class FakeAuthor:
    def __init__(self):
        self.id = 42
        self.name = "Test User"
        self.discriminator = "0001"
        self.display_name = "Test User"


def make_cog():
    cog = object.__new__(mc.MaintenanceCommands)
    cog.bot = FakeBot()
    return cog


def demo():
    season_num = 7
    # Keep the command's on-disk safety backup out of the repo working tree.
    tmpdir = tempfile.TemporaryDirectory()
    mc.globals_mod = types.SimpleNamespace(
        __file__=str(Path(tmpdir.name) / "globals.py")
    )
    oid_hex = "6512f8a4f3e2b1a0c9d8e7f6"
    oid = ObjectId(oid_hex)
    match_doc = {
        "_id": oid,
        "season_number": season_num,
        "metadata": {
            "match_id": "abc-123",
            "started_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
        },
    }
    legacy_match = {
        "_id": "legacy",
        "metadata": {"match_id": "old"},
    }  # no season_number
    other_season_match = {"_id": "other", "season_number": 6}
    mmr_doc = {
        "_id": oid,
        "player_id": "42",
        "mmr": 1234.5,
        "wins": 3,
        "losses": 1,
        "duck_coins": 9,
    }
    season_doc = {"_id": "current", "season_number": season_num, "matches_played": 2}
    user_doc = {"_id": oid, "discord_id": "42", "name": "Duck", "tag": "0001"}

    mc.all_matches = FakeCollection([match_doc, legacy_match, other_season_match])
    mc.mmr_collection = FakeCollection([mmr_doc])
    mc.seasons = FakeCollection([season_doc])
    mc.users = FakeCollection([user_doc])

    cog = make_cog()

    # --- snapshotseason -----------------------------------------------------
    ctx = FakeCtx(FakeMessage([]))
    asyncio.run(cog.snapshotseason(ctx, arg=""))
    assert ctx.sent, "snapshotseason sent nothing"
    msg, kw = ctx.sent[0][0][0], ctx.sent[0][1]
    assert f"Season {season_num} snapshot: 2 match(es)" in msg, msg
    assert "1 player doc(s)" in msg, msg
    # Snapshots are gzipped (Discord upload limits); the filename says so.
    assert kw["file"].filename.endswith(".json.gz"), kw["file"].filename
    payload = gzip.decompress(kw["file"].fp.getvalue())
    backup = json.loads(payload)
    assert backup["format"] == "season-snapshot"
    assert backup["season_number"] == season_num
    cols = backup["collections"]
    # current season + legacy (no season_number) matches; not other seasons
    assert len(cols["matches"]) == 2
    assert {
        str(d["_id"]["$oid"]) if isinstance(d["_id"], dict) else d["_id"]
        for d in cols["matches"]
    } == {oid_hex, "legacy"}
    assert len(cols["mmr_data"]) == 1
    assert cols["seasons"][0]["_id"] == "current"
    assert "users" not in cols, "users only included with `full`"
    # snapshot must not modify live data
    assert len(mc.all_matches.docs) == 3 and len(mc.mmr_collection.docs) == 1

    # full snapshot includes users
    ctx2 = FakeCtx(FakeMessage([]))
    asyncio.run(cog.snapshotseason(ctx2, arg="full"))
    backup2 = json.loads(gzip.decompress(ctx2.sent[0][1]["file"].fp.getvalue()))
    assert len(backup2["collections"]["users"]) == 1

    # BSON round-trip: $oid / $date survive json -> json
    raw_match = backup["collections"]["matches"][0]
    assert "$date" in raw_match["metadata"]["started_at"]
    assert "$oid" in raw_match["_id"]
    rt = _dejsonify(json.loads(json.dumps(_jsonify(mmr_doc))))
    assert rt["_id"] == mmr_doc["_id"] and rt["mmr"] == mmr_doc["mmr"]

    # --- recoverseason: guards ----------------------------------------------
    ctx3 = FakeCtx(FakeMessage([]))
    asyncio.run(cog.recoverseason(ctx3, arg=""))
    assert "confirm" in ctx3.sent[0][0][0]
    assert len(mc.all_matches.docs) == 3, "guard must not touch data"

    ctx4 = FakeCtx(FakeMessage([FakeAttachment("notes.txt", b"{}")]))
    asyncio.run(cog.recoverseason(ctx4, arg="confirm"))
    assert "json" in ctx4.sent[0][0][0].lower()

    ctx5 = FakeCtx(FakeMessage([FakeAttachment("x.json", b"not json")]))
    asyncio.run(cog.recoverseason(ctx5, arg="confirm"))
    assert "json" in ctx5.sent[0][0][0].lower()

    ctx6 = FakeCtx(
        FakeMessage([FakeAttachment("x.json", json.dumps({"foo": 1}).encode())])
    )
    asyncio.run(cog.recoverseason(ctx6, arg="confirm"))
    assert "invalid" in ctx6.sent[0][0][0].lower()

    # --- recoverseason: full overwrite from snapshot ------------------------
    # Live state drifts from the snapshot: extra player doc, extra match,
    # modified mmr, different season counter.
    mc.all_matches.docs.append({"_id": "new-extra", "season_number": season_num})
    mc.mmr_collection.docs.append({"_id": "zzz", "player_id": "99", "mmr": 1})
    mc.mmr_collection.docs[0]["mmr"] = 0.0
    mc.seasons.docs[0]["matches_played"] = 99

    # Snapshot the live (drifted) state before recovery so the on-disk safety
    # backup can be checked after the command runs.
    safety_dir = Path(mc.globals_mod.__file__).parent / "backups"
    # The snapshot is round-tripped gzipped, exactly as !snapshotseason sends it.
    gz_snapshot = gzip.compress(json.dumps(backup).encode("utf-8"))
    ctx7 = FakeCtx(FakeMessage([FakeAttachment("snap.json.gz", gz_snapshot)]))
    asyncio.run(cog.recoverseason(ctx7, arg="confirm"))
    # matches: snapshot's 2 restored; the drifted extra deleted; other season kept
    assert len(mc.all_matches.docs) == 3, mc.all_matches.docs
    assert {str(d["_id"]) for d in mc.all_matches.docs} == {oid_hex, "legacy", "other"}
    restored = [d for d in mc.all_matches.docs if str(d["_id"]) == oid_hex][0]
    assert isinstance(restored["metadata"]["started_at"], datetime)
    # mmr_data: exactly the snapshot's docs, values restored
    assert len(mc.mmr_collection.docs) == 1
    assert mc.mmr_collection.docs[0]["player_id"] == "42"
    assert mc.mmr_collection.docs[0]["mmr"] == 1234.5
    assert mc.mmr_collection.docs[0]["duck_coins"] == 9
    # season doc fully replaced (matches_played back to 2, not merged)
    assert mc.seasons.find_one({"_id": "current"})["matches_played"] == 2
    # The success reply names the safety backup that was written.
    assert "safety backup" in ctx7.sent[0][0][0].lower(), ctx7.sent[0][0][0]
    safety_files = list(safety_dir.glob("season_pre_recovery_*.json"))
    assert len(safety_files) == 1, safety_files
    # The safety backup captured the pre-recovery drift (mmr 0.0, counter 99).
    safety = json.loads(safety_files[0].read_text())
    assert safety["collections"]["mmr_data"][0]["mmr"] == 0.0
    assert safety["collections"]["seasons"][0]["matches_played"] == 99

    # --- adminhelp: embed fields must never exceed Discord's 1024-char limit
    long_lines = [f"**`!cmd{i} <arg>`**\n↪ {'x' * 120}" for i in range(20)]
    chunks = mc._chunk_embed_lines(long_lines)
    assert all(len(c) <= 1000 for c in chunks), [len(c) for c in chunks]
    # Whole lines are preserved: rejoining the chunks reproduces the input.
    assert "\n".join(chunks) == "\n".join(long_lines), "chunking must not lose lines"
    # A list that fits stays in a single chunk.
    assert mc._chunk_embed_lines(["a", "b"]) == ["a\nb"]
    # Empty input falls back to the em-dash placeholder.
    assert mc._chunk_embed_lines([]) == ["—"]

    # --- adminhelp: curated help map constraints ---------------------------
    # Every description must be 10 words or fewer (the embed contract), and
    # every entry must carry a section for the grouped layout.
    for name, (section, usage_args, desc) in mc.ADMIN_COMMAND_HELP.items():
        assert len(desc.split()) <= 10, (name, desc)
        assert usage_args == usage_args.strip(), name
        assert section, name
    # Fallbacks: a command absent from the map derives usage from its
    # signature and truncates its docstring to 10 words.
    import inspect

    def _fake_cmd(name, params, doc):
        c = types.SimpleNamespace(name=name, help=doc, enabled=True, checks=[])
        c.clean_params = params
        return c

    import inspect as _inspect

    no_args = _fake_cmd("unknowncmd", {}, "First line here.\nSecond line.")
    assert mc._usage_args_of(no_args) == ""
    assert mc._short_desc_of(no_args) == "First line here."
    sig = _fake_cmd(
        "withargs",
        {
            "required": _inspect.Parameter(
                "required", _inspect.Parameter.POSITIONAL_OR_KEYWORD
            ),
            "optional": _inspect.Parameter(
                "optional", _inspect.Parameter.POSITIONAL_OR_KEYWORD, default="x"
            ),
            "rest": _inspect.Parameter("rest", _inspect.Parameter.VAR_POSITIONAL),
        },
        "one two three four five six seven eight nine ten eleven twelve",
    )
    assert mc._usage_args_of(sig) == "<required> [optional] <rest...>"
    assert (
        mc._short_desc_of(sig) == "one two three four five six seven eight nine ten..."
    )
    # Curated entries win over signature/docstring for known commands.
    known = _fake_cmd("addcoins", {}, "long docstring ignored")
    assert mc._usage_args_of(known) == "<@user|Name#Tag> <amount>"
    assert mc._short_desc_of(known) == "Grant or remove a player's Duck Coins"
    tmpdir.cleanup()
    print("snapshot/recover season self-checks passed")


if __name__ == "__main__":
    demo()
