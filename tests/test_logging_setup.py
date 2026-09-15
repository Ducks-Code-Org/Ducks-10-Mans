import asyncio
import io
import logging
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops.logging_setup import (
    DiscordLogHandler,
    configured_level,
    discord_mirror_enabled,
    setup_logging,
)


def test_flush_requires_attached_bot():
    """flush_pending must send queued records once a bot is attached."""

    class FakeChannel:
        def __init__(self, name):
            self.name = name
            self.sent = []

        async def send(self, text):
            self.sent.append(text)

    channel = FakeChannel("bot-logs")
    bot = types.SimpleNamespace(guilds=[types.SimpleNamespace(text_channels=[channel])])
    # A bot must be attached for the handler to resolve #bot-logs.
    handler = DiscordLogHandler(bot=bot)
    handler._pending.put(
        logging.LogRecord("t", logging.WARNING, "f", 1, "boom", (), None)
    )
    asyncio.run(handler.flush_pending())
    assert channel.sent and "boom" in channel.sent[0], channel.sent

    # Without an attached bot the handler must not raise and sends nothing.
    unattached = DiscordLogHandler(bot=None)
    asyncio.run(unattached.flush_pending())


def demo():
    # The mirror handler must subclass logging.Handler so it can attach.
    assert issubclass(DiscordLogHandler, logging.Handler)

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

    test_flush_requires_attached_bot()

    print("all logging setup self-checks passed")


if __name__ == "__main__":
    demo()
