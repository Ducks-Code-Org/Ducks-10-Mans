"""Running this file starts the bot."""

import os
import discord
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

bot_token = os.getenv("bot_token")

# Run the bot
bot.run(bot_token)
