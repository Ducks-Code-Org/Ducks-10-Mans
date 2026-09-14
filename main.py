"""Running this file starts the bot."""

import discord

from bot import CustomBot
from globals import BOT_TOKEN
from logging_setup import setup_logging

# Set up logging (level from bot.ini; must run before any module logs)
discord_logger = setup_logging()

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
