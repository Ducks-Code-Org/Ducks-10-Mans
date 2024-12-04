import discord
import os
from bot import CustomBot

# Set up bot
intents = discord.Intents.default()
intents.message_content = True
bot = CustomBot(command_prefix="!", intents=intents, help_command=None)
bot_token = os.getenv("bot_token")


bot.load_mmr_data()

# Run the bot
bot.run(bot_token)
