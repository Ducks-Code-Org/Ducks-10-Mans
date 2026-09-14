import io
import logging
import os
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from logging_setup import (
    DiscordLogHandler,
    configured_level,
    discord_mirror_enabled,
    setup_logging,
)


def demo():
    # Level is parsed from bot.ini [logging] and is a valid severity.
    level = configured_level()
    assert isinstance(level, int)
    valid = {
        logging.DEBUG,
        logging.INFO,
        logging.WARNING,
        logging.ERROR,
        logging.CRITICAL,
    }
    assert level in valid, f"configured level {level!r} not a valid severity"

    # The mirror flag parses from bot.ini without raising.
    assert isinstance(discord_mirror_enabled(), bool)

    # setup_logging returns a handler only when the mirror is enabled,
    # and every record emitted through the root logger is queued for it.
    handler = setup_logging()
    assert (
        handler is not None
    ), "bot.ini enables log_to_discord, expected a mirror handler"
    root = logging.getLogger()
    assert (
        handler in root.handlers
    ), "mirror handler must be attached to the root logger"

    test_logger = logging.getLogger("test.issue52")
    stream = io.StringIO()
    probe = logging.StreamHandler(stream)
    probe.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    root.addHandler(probe)
    try:
        test_logger.warning("issue-52 warning record")
    finally:
        root.removeHandler(probe)

    # Console side must have captured level + message (timestamp format).
    output = stream.getvalue()
    assert "WARNING issue-52 warning record" in output, output

    # Mirror side must have queued the record for the #bot-logs channel.
    assert not handler._pending.empty(), "record was not queued for the Discord mirror"

    # Level filter: a DEBUG record at info level must not queue.
    before = handler._pending.qsize()
    test_logger.debug("should be filtered out")
    assert handler._pending.qsize() == before, "debug record leaked past level filter"

    # Restored to defaults for other tests / modules.
    root.handlers[:] = [h for h in root.handlers if h is not handler]
    root.setLevel(logging.INFO)

    print("all logging setup self-checks passed")


if __name__ == "__main__":
    demo()
