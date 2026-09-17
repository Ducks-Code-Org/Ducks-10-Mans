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
                "**!stats <Name#Tag>** - Check a player's MMR and match statistics\n"
                "**!linkriot <Name#Tag>** - Link your Riot account\n"
                "**!coins <Name#Tag|@user>** - Check Duck Coin balance (yours by default)\n"
                "**!interest <time>** - Plan a time to play 10 mans (`!interest list` for upcoming)\n"
                "**!pingrecent** - Ping everyone from the most recent queue\n"
                "**!leaderboard <type>** - View the leaderboard\n"
                "↪ _Available types: `mmr` (default), `wins`, `losses`, `kd`, `acs`, `coins`_\n"
                "**!bet attackers|defenders <amount>** - Bet Duck Coins on the match\n"
                "**!doubledown** - Spend 5 Duck Coins to double your MMR change\n"
                "**!setmap <map>** - Spend Duck Coins to override the chosen map\n"
                "**!bug** - Report a bug (pings the maintainers)\n"
            ),
            inline=False,
        )

        help_embed.set_footer(text="Admins: !adminhelp lists maintenance commands.")

        await ctx.send(embed=help_embed)
