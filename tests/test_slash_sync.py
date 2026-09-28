"""Self-check (issue #227): slash registration is provable and failure-safe.

- Success path: setup_hook logs how many commands were synced.
- Failure path: a sync exception is logged with the remedy, the bot stays
  up, and startup completes.
- Docs: GETTING_STARTED.md tells inviters the applications.commands scope
  is required (the silent zero-registration case).
"""

import asyncio
import logging
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Stub the Mongo connection before any command module import.
class _Coll:
    def __getattr__(self, name):
        return lambda *a, **k: None

    def find(self, *a, **k):
        return []

    def find_one(self, *a, **k):
        return None


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

import bot as bot_mod  # noqa: E402


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.INFO)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


def _messages(logger_name="bot"):
    return [r.getMessage() for r in cap.records if r.name == logger_name]


async def run_setup(bot, sync_impl):
    bot.tree.sync = sync_impl
    await bot.setup_hook()


async def demo():
    global cap
    cap = _Capture()
    # INFO records must pass the root logger's level to reach the handler.
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger().addHandler(cap)

    # Success: the logged count matches the synced command list.
    b1 = bot_mod.CustomBot(command_prefix="!", intents=None, help_command=None)
    synced_names = ["signup", "report", "coins"]
    await run_setup(b1, lambda: asyncio.sleep(0, result=synced_names))
    assert any(
        "Synced 3 slash command(s)" in m for m in _messages()
    ), f"success count not logged: {_messages()}"
    assert any("Bot is ready" in m for m in _messages())

    # Failure: the remedy is logged, no exception escapes, startup completes.
    cap.records.clear()
    b2 = bot_mod.CustomBot(command_prefix="!", intents=None, help_command=None)

    async def boom():
        raise RuntimeError("403: missing applications.commands scope")

    await run_setup(b2, boom)
    msgs = _messages()
    assert any(
        "Slash command sync failed" in m and "applications.commands" in m for m in msgs
    ), f"failure remedy not logged: {msgs}"
    assert any(
        "Bot is ready" in m for m in msgs
    ), "startup must continue after sync failure"

    print("all slash-sync self-checks passed")


if __name__ == "__main__":
    asyncio.run(demo())
