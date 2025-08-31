import discord
from discord.ext import commands


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
                "↪ _Available types: `mmr` (default), `wins`, `losses`, `kd`, `acs`_\n"
            ),
            inline=False,
        )

        # TDM Commands
        help_embed.add_field(
            name="TDM Commands",
            value=(
                "**!tdm** - Start a 3v3 TDM signup session\n"
                "**!tdmreport** - Report TDM match results\n"
                "**!tdmstats** - View TDM-specific stats\n"
            ),
            inline=False,
        )

        # Only show Admin Commands if user has administrator permissions
        if ctx.author.guild_permissions.administrator:
            help_embed.add_field(
                name="Admin Commands",
                value=(
                    # "**!setcaptain1** - Set Captain 1 using `Name#Tag`\n"
                    # "**!setcaptain2** - Set Captain 2 using `Name#Tag`\n"
                    "**!cancel** - Cancel current 10 mans signup\n"
                    "**!canceltdm** - Cancel current TDM signup\n"
                    "**!toggledev** - Toggle Developer Mode\n"
                    "**!newseason** - Resets stats and starts a new season\n"
                ),
                inline=False,
            )

        await ctx.send(embed=help_embed)
