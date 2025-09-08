import discord
from discord.ext import commands


async def setup(bot):
    await bot.add_cog(BugCommand(bot))


class BugCommand(commands.Cog):
    @commands.command()
    async def bug(self, ctx):
        nate_discord_id = 348901216723402753
        file_path = "assets/bug.webp"
        try:
            file = discord.File(file_path, filename="bug.webp")
            await ctx.send(f"<@{nate_discord_id}>", file=file)
        except Exception as e:
            await ctx.send(f"Failed to upload image: {e}")
