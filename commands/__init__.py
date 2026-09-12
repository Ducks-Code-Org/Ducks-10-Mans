from discord.ext import commands


class BotCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.dev_mode = False

        self.bot.chosen_mode = None
        self.bot.selected_map = None
        self.bot.match_not_reported = False
        self.bot.match_ongoing = False
        self.bot.player_names = {}
        self.bot.signup_active = False
        self.bot.queue = []
        self.bot.captain1 = None
        self.bot.captain2 = None
        self.bot.team1 = []
        self.bot.team2 = []
