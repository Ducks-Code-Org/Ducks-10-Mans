import os
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from globals import BOT_CONFIG, BOT_FEATURES


def demo():
    # bot.ini must parse and expose the [features] section.
    assert BOT_CONFIG is not None
    assert BOT_CONFIG.has_section("features"), "bot.ini missing [features] section"
    assert BOT_FEATURES is not BOT_CONFIG["DEFAULT"]
    # Read-only: values must be strings parsed from the file.
    assert all(isinstance(v, str) for v in BOT_FEATURES.values())
    print("all bot.ini config self-checks passed")


if __name__ == "__main__":
    demo()
