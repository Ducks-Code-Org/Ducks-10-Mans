"""Self-check: /ranks posts its thresholds inside an embed (issue #214)."""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _Coll:
    def __getattr__(self, name):
        return lambda *a, **k: None

    def find(self, *a, **k):
        return []


_database_stub = types.ModuleType("database")
for name in (
    "users",
    "mmr_collection",
    "seasons",
    "all_matches",
    "coin_escrow",
    "interests",
    "recent_queue",
):
    setattr(_database_stub, name, _Coll())
_database_stub.client = types.SimpleNamespace(close=lambda: None)
sys.modules["database"] = _database_stub

import commands.ranks as ranks_mod  # noqa: E402
from game.ranks import SSR_NAME, RANKS  # noqa: E402


class _Role:
    def __init__(self, name):
        self.name = name
        self.mention = f"<@&{name}>"


class _Guild:
    def __init__(self, role_names):
        self.roles = [_Role(n) for n in role_names]


class _Sent:
    def __init__(self):
        self.kwargs = {}


class _Ctx:
    def __init__(self):
        self.guild = _Guild([name for _, name, _ in RANKS] + [SSR_NAME])
        self.author = types.SimpleNamespace(id=1, name="tester")
        self.sent = _Sent()

    async def send(self, *args, **kwargs):
        self.sent.kwargs = kwargs
        return None


class _Embed:
    def __init__(self, *, title=None, description=None, color=None):
        self.title = title
        self.description = description


_discord_stub = getattr(ranks_mod, "discord")


def demo():
    import asyncio

    ctx = _Ctx()
    cog = object.__new__(ranks_mod.RanksCommand)
    cog.bot = types.SimpleNamespace()
    cmd = ranks_mod.RanksCommand.ranks
    callback = getattr(cmd, "callback", cmd)
    asyncio.run(callback(cog, ctx))

    # The reply must carry an embed (issue #214), not raw text.
    assert "embed" in ctx.sent.kwargs, "ranks must reply with an embed"
    embed = ctx.sent.kwargs["embed"]
    description = embed.description

    # The content is unchanged: every rank + threshold range appears.
    assert "100-199 MMR" in description, description
    assert "0-99 MMR" in description, description
    assert "750+ MMR" in description, description
    assert "Rank 1" in description and SSR_NAME in description, description

    # The title exists so the embed renders as a proper card.
    assert embed.title, "embed needs a title"

    # /ranks stays a hidden personal reply (issue #210).
    assert ctx.sent.kwargs.get("ephemeral") is True, "/ranks must reply hidden"

    print("ranks-embed self-checks passed")


if __name__ == "__main__":
    demo()
