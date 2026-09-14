import discord
from discord.ext import commands

from ranks import help_menu_text


async def setup(bot):
    await bot.add_cog(HelpCommand(bot))


class HelpCommand(commands.Cog):
    @commands.command()
    async def help(self, ctx):
        help_embed = discord.Embed(
            title="Help Menu",
            description="Duck's 10 Mans Bot Commands:",
            color=discord.Color.green(),
        )

        # General Commands
        help_embed.add_field(
            name="10 Mans Commands",
            value=(
                "**!signup** - Start a new 10 mans signup session\n"
                "**!report** - Report match results and update MMR\n"
                "**!stats** - Check a player's MMR and match statistics\n"
                "↪ _usage: `!stats <Name#Tag>`_\n"
                "**!linkriot** - Link your Riot account.\n"
                "↪ _usage: `!linkriot <Name#Tag>`_\n"
                "**!interest** - Plan a time to play 10 mans\n"
                "↪ _usage: `!interest <time>`_\n"
                "**!leaderboard <type>** - View the leaderboard\n"
                "↪ _Available types: `mmr` (default), `wins`, `losses`, `kd`, `acs`, `quacks`_\n"
                "**/bet attackers|defenders <amount>** - Bet Quack Coins on the match\n"
                "**/doubledown** - Spend 5 Quack Coins to double your MMR change\n"
                "**/setmap <map>** - Spend Quack Coins to override the chosen map\n"
            ),
            inline=False,
        )

        # Rank tiers and their MMR thresholds
        help_embed.add_field(
            name="Ranks",
            value=help_menu_text(),
            inline=False,
        )

        # Only show Admin Commands if user has administrator permissions
        if ctx.author.guild_permissions.administrator:
            help_embed.add_field(
                name="Admin Commands",
                value=(
                    # "**!setcaptain1** - Set Captain 1 using `Name#Tag`\n"
                    # "**!setcaptain2** - Set Captain 2 using `Name#Tag`\n"
                    "**!cancel** - Cancel current 10 mans signup or match\n"
                    "**!pingrecent** - Ping players from the most recent queue\n"
                    "**!toggledev** - Toggle Developer Mode\n"
                    "**!newseason** - Resets stats and starts a new season\n"
                    "**!rollback** - Revert the most recent match's stats\n"
                    "**!editplayer** - Edit a player's stats or Riot ID\n"
                    "↪ _usage: `!editplayer <@user> <mmr|wins|losses|riot> <value>`_\n"
                    "**!substitute** - Swap a player into the current match\n"
                    "↪ _usage: `!substitute <@Out> <@In>`_\n"
                    "**!enablereport** - Re-enable !report for the current match\n"
                    "**!fixmap <map>** - Force-set the current match's map\n"
                    "**!setconfig <key> <value>** - Update bot.ini settings live\n"
                    "**!showconfig** - Show current bot.ini feature flags\n"
                    "**!matchinfo** - Dump internal match/queue state\n"
                    "**!addcoins <@user> <amount>** - Grant Quack Coins\n"
                    "**!resetplayer <@user>** - Reset a player's season stats\n"
                ),
                inline=False,
            )

        await ctx.send(embed=help_embed)
