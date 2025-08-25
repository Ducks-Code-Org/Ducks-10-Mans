"""Running this file starts the bot."""
import sys, subprocess, importlib, os

REQS = {
    "beautifulsoup4": "bs4",
    "table2ascii": "table2ascii",
    "discord.py": "discord",     
    "aiohttp": "aiohttp",
    "pymongo": "pymongo",
    "requests": "requests",
    "dnspython": "dns",
    "propcache": "propcache",
}

def _ensure_pkg(pip_name, import_name):
    try:
        importlib.import_module(import_name)
        return
    except Exception:
        print(f"[BOOT] Installing {pip_name} for import '{import_name}'...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pip_name])
        importlib.invalidate_caches()
        importlib.import_module(import_name)

for pip_name, import_name in REQS.items():
    _ensure_pkg(pip_name, import_name)

print("[BOOT] Python:", sys.version)
print("[BOOT] sys.executable:", sys.executable)
print("[BOOT] sys.path[0:3]:", sys.path[:3])
for pip_name, import_name in REQS.items():
    try:
        m = importlib.import_module(import_name)
        path = getattr(m, "__file__", "?")
        ver = getattr(m, "__version__", getattr(m, "__VERSION__", "?"))
        print(f"[BOOT] {import_name} OK -> {ver} @ {path}")
    except Exception as e:
        print(f"[BOOT] {import_name} FAIL:", repr(e))

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
