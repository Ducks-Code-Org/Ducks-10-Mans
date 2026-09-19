"""Self-checks for !pingrecent cancel-flag (issue #168) and stats link (issue #167)."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import types  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

# Stub discord before imports
sys.modules["discord"] = MagicMock()
sys.modules["discord.ext"] = MagicMock()
sys.modules["discord.ext.commands"] = MagicMock()

# Stub database with an in-memory recent_queue collection
_docs = {}


class _Collection:
    def __init__(self, name):
        self.name = name

    def update_one(self, query, update, upsert=False):
        _id = query["_id"]
        doc = _docs.setdefault((self.name, _id), {"_id": _id})
        doc.update(update["$set"])

    def find_one(self, query):
        return _docs.get((self.name, query["_id"]))


sys.modules["database"] = types.SimpleNamespace(
    recent_queue=_Collection("recent_queue"),
)

import game.recent_queue as recent_queue  # noqa: E402


def test_cancelled_flag():
    _docs.clear()
    players = [{"id": 1}, {"id": 2}]

    recent_queue.remember_recent_queue(players, cancelled=True)
    ids, cancelled = recent_queue.get_recent_queue()
    assert ids == ["1", "2"], ids
    assert cancelled is True, cancelled

    recent_queue.remember_recent_queue(players, cancelled=False)
    ids, cancelled = recent_queue.get_recent_queue()
    assert cancelled is False, cancelled

    # Default is cancelled=True (matches !cancel / timeout call sites)
    recent_queue.remember_recent_queue(players)
    _, cancelled = recent_queue.get_recent_queue()
    assert cancelled is True, cancelled

    print("test_cancelled_flag: OK")


def test_pingrecent_messages():
    cancelled_ids, _ = recent_queue.get_recent_queue()
    assert cancelled_ids == ["1", "2"]

    recent_queue.remember_recent_queue([{"id": 5}], cancelled=False)
    ids, cancelled = recent_queue.get_recent_queue()
    msg = recent_queue.pingrecent_message(ids, cancelled)
    assert "A new queue has started!" in msg, msg
    assert "<@5>" in msg

    recent_queue.remember_recent_queue([{"id": 5}], cancelled=True)
    ids, cancelled = recent_queue.get_recent_queue()
    msg = recent_queue.pingrecent_message(ids, cancelled)
    assert "was cancelled" in msg, msg

    print("test_pingrecent_messages: OK")


if __name__ == "__main__":
    test_cancelled_flag()
    test_pingrecent_messages()
    print("All checks passed.")
