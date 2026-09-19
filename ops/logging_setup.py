"""Central logging setup for the bot (issue #52).

Configured from the [logging] section of bot.ini:

    [logging]
    level = info          # debug, info, warning, error, critical
    log_to_discord = true # mirror WARNING+ records to a #bot-logs channel

All modules log through the standard `logging` module; nothing writes to
stdout directly, so console output always carries timestamps and levels.
The Discord mirror queues WARNING-and-above records and main.py attaches
the bot so a flush loop in on_ready replicates them into #bot-logs.
"""

import logging
import queue

import discord

from globals import BOT_CONFIG, feature_enabled

LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
DISCORD_FORMAT = "**[{levelname}]** {name}: {message}"
DISCORD_LEVEL_DEFAULT = logging.WARNING

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}


def configured_level() -> int:
    """Log level from bot.ini [logging] `level`, defaulting to INFO."""
    try:
        raw = BOT_CONFIG["logging"].get("level", "info").strip().lower()
    except (KeyError, AttributeError):
        return logging.INFO
    return _LEVELS.get(raw, logging.INFO)


def discord_mirror_enabled() -> bool:
    """Whether the [logging] `log_to_discord` flag in bot.ini is on."""
    try:
        raw = BOT_CONFIG["logging"].get("log_to_discord", "")
    except (KeyError, AttributeError):
        return False
    value = str(raw).strip().lower() in {"1", "true", "yes", "on"}
    if not value and raw:
        # Fall back to the shared feature-flag parser for other spellings.
        return feature_enabled("log_to_discord", default=False)
    return value


class DiscordLogHandler(logging.Handler):
    """Replicates log records into a #bot-logs channel.

    Records are queued here and flushed asynchronously by the bot's event
    loop once it is running, so logging from sync code or before connect
    never blocks or crashes the bot. Sending failures are swallowed: a
    missing/bot-logs-less guild must never break logging.
    """

    def __init__(self, bot=None, level: int = DISCORD_LEVEL_DEFAULT):
        super().__init__(level=level)
        # bot is attached by main.py before the flush loop starts, so the
        # handler can be created (and queue records) before the bot exists.
        self.bot = bot
        self.setFormatter(logging.Formatter(DISCORD_FORMAT, style="{"))
        self._pending: queue.SimpleQueue = queue.SimpleQueue()

    def emit(self, record: logging.LogRecord) -> None:
        # Queue from any thread/context; flush happens on the event loop.
        self._pending.put(record)

    async def flush_pending(self) -> None:
        """Send every queued record so far to #bot-logs (drain-on-call)."""
        channel = self._bot_logs_channel()
        drained: list[logging.LogRecord] = []
        while True:
            try:
                drained.append(self._pending.get_nowait())
            except queue.Empty:
                break
        if channel is None or not drained:
            return
        chunks = []
        for record in drained:
            chunks.append(self.format(record))
        text = "\n".join(chunks)
        for start in range(0, len(text), 1900):
            try:
                await channel.send(text[start : start + 1900])
            except (discord.HTTPException, discord.Forbidden, discord.NotFound):
                return

    def _bot_logs_channel(self):
        try:
            for guild in self.bot.guilds:
                channel = discord.utils.get(guild.text_channels, name="bot-logs")
                if channel is not None:
                    return channel
        except AttributeError:
            return None  # bot not ready yet; nothing to mirror into
        return None


class _VoiceExtrasNotInstalledFilter(logging.Filter):
    """Silence the one-shot 'voice will NOT be supported' startup warnings.

    discord.py logs these from discord.client when PyNaCl/davey are missing,
    but this bot only moves members between channels (game/voice_presence.py)
    and never joins a channel itself, so voice support is intentionally not
    installed. Only those exact messages are dropped; any other voice log
    (e.g. a real VoiceClient error) still gets through.
    """

    _SUPPRESSED = (
        "PyNaCl is not installed, voice will NOT be supported",
        "davey is not installed, voice will NOT be supported",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        return record.getMessage() not in self._SUPPRESSED


def setup_logging() -> DiscordLogHandler | None:
    """Configure the root logger. Returns the Discord mirror (if enabled)."""
    formatter = logging.Formatter(LOG_FORMAT)
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    # Drop the one-shot voice-extras warnings (bot never joins voice). This
    # must live on handlers, not the "discord" logger: logger filters are not
    # inherited by child loggers like discord.client, but handler filters
    # apply to every record that passes through.
    stream.addFilter(_VoiceExtrasNotInstalledFilter())

    root = logging.getLogger()
    root.setLevel(configured_level())
    # Replace any pre-existing handlers so re-running setup never duplicates.
    root.handlers[:] = [stream]

    discord_handler = None
    if discord_mirror_enabled():
        discord_handler = DiscordLogHandler()
        discord_handler.addFilter(_VoiceExtrasNotInstalledFilter())
        root.addHandler(discord_handler)

    # Discord.py has its own verbose logger; keep it on our level so its
    # connection events flow through the same handlers.
    logging.getLogger("discord").setLevel(configured_level())
    return discord_handler
