import discord
from discord.ext import commands


async def setup(bot):
    await bot.add_cog(HelpCommand(bot))


class HelpCommand(commands.Cog):
    @commands.command()
    async def help(self, ctx):
        help_embed = discord.Embed(
            title="Help Menu",
            description="Duck's 10 Mans & TDM Commands:",
            color=discord.Color.green(),
        )

        # General Commands
        help_embed.add_field(
            name="10 Mans Commands",
            value=(
                "**!signup** - Start a 10 mans signup session\n"
                "**!status** - View current queue status\n"
                "**!report** - Report match results and update MMR\n"
                "**!stats** - Check your MMR and match stats\n"
                "**!linkriot** - Link Riot account using `Name#Tag`\n"
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

        # Leaderboard Commands
        help_embed.add_field(
            name="Leaderboard Commands",
            value=(
                "**!leaderboard** - View MMR leaderboard\n"
                "**!leaderboard_KD** - View K/D leaderboard\n"
                "**!leaderboard_wins** - View wins leaderboard\n"
                "**!leaderboard_ACS** - View ACS leaderboard\n"
            ),
            inline=False,
        )

        # Admin Commands
        help_embed.add_field(
            name="Admin Commands",
            value=(
                "**!setcaptain1** - Set Captain 1 using `Name#Tag`\n"
                "**!setcaptain2** - Set Captain 2 using `Name#Tag`\n"
                "**!cancel** - Cancel current 10 mans signup\n"
                "**!canceltdm** - Cancel current TDM signup\n"
                "**!toggledev** - Toggle Developer Mode\n"
            ),
            inline=False,
        )

        # Footer
        help_embed.set_footer(text="Use commands with the ! prefix")

        await ctx.send(embed=help_embed)
