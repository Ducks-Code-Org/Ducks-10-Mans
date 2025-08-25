"""Running this file starts the bot."""

import discord

from bot import CustomBot
from globals import BOT_TOKEN

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

# Run the bot
bot.run(BOT_TOKEN)
