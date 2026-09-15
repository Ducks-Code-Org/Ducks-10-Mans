"""Running this file starts the bot."""

import discord

from globals import BOT_TOKEN
from ops.logging_setup import setup_logging

# Set up logging (level from bot.ini) before importing bot/database so their
# import-time logs (e.g. the Mongo ping) reach the configured handlers.
discord_logger = setup_logging()

from bot import CustomBot

# Set up bot
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = CustomBot(
    command_prefix="!",
    activity=discord.Game(name="10 Mans!"),
    intents=intents,
    help_command=None,
)
bot.discord_log_handler = discord_logger

# Run the bot
bot.run(BOT_TOKEN)
