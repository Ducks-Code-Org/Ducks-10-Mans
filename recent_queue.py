"Stores and retrieves the most recent 10 mans queue for the !pingrecent command."

from database import recent_queue

DOC_ID = "recent_queue"


def remember_recent_queue(players, cancelled: bool = True) -> None:
    """Save the current queue's discord IDs so !pingrecent can mention them."""
    ids = [str(p["id"]) for p in players]
    recent_queue.update_one(
        {"_id": DOC_ID},
        {"$set": {"player_ids": ids, "cancelled": cancelled}},
        upsert=True,
    )


def get_recent_queue() -> tuple[list[str], bool]:
    """Return the discord IDs of the last recorded queue and how it ended."""
    doc = recent_queue.find_one({"_id": DOC_ID})
    if not doc:
        return [], True
    return doc.get("player_ids", []), doc.get("cancelled", True)
