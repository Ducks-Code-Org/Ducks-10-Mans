"""Self-checks for the tracker_links display-name helpers.

Unlinked players (dead Riot account, stats preserved) must fall back to
their Discord display name on leaderboards and other displays.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker_links import (  # noqa: E402
    display_line_for,
    display_name_for,
    profile_url_for,
    tracker_link_for,
)


class FakeMember:
    def __init__(self, display_name):
        self.display_name = display_name


class FakeGuild:
    def __init__(self, members):
        self._members = members

    def get_member(self, uid):
        return self._members.get(int(uid))


LINKED = {"name": "foo", "tag": "bar", "discord_id": "123"}
UNLINKED = {"discord_id": "123"}  # purge cleared name/tag/puuid
GUILD = FakeGuild({123: FakeMember("DuckFan")})


def demo():
    # Linked players always show their Riot ID.
    assert display_name_for(LINKED) == "foo#bar"
    assert display_name_for(LINKED, guild=GUILD) == "foo#bar"
    assert tracker_link_for(LINKED) is not None
    assert display_line_for(LINKED).startswith("[foo#bar](")

    # Unlinked players fall back to their Discord display name.
    assert display_name_for(UNLINKED, guild=GUILD) == "DuckFan"
    assert tracker_link_for(UNLINKED) is None, "no profile to link to"
    assert display_line_for(UNLINKED, guild=GUILD) == "DuckFan"

    # Nobody findable: N/A everywhere.
    assert display_name_for(None, guild=GUILD) == "N/A"
    assert display_name_for(None) == "N/A"
    assert display_name_for(UNLINKED) == "N/A"  # no guild, no fallback
    assert display_line_for(None) == "N/A"
    assert display_line_for(UNLINKED) == "N/A"

    # Riot-id-less user doc but no member: still N/A (no crash on int()).
    assert display_name_for({"discord_id": "abc"}) == "N/A"

    # profile_url_for: bare URL for set_author(url=...), None when unlinked.
    url = profile_url_for(LINKED)
    assert url and url.startswith("https://tracker.gg/valorant/profile/riot/")
    assert "%23" in url, "the tag separator must be URL-encoded"
    assert profile_url_for(UNLINKED) is None
    assert profile_url_for(None) is None
    # The URL inside the markdown link matches the bare URL exactly.
    link = tracker_link_for(LINKED)
    assert link and link.endswith(f"]({url})")

    print("all tracker-link display self-checks passed")


if __name__ == "__main__":
    demo()
