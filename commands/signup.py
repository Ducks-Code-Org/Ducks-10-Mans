"Starts the signup process for a new match."

import random

import discord
from discord.ext import commands

from commands import BotCommands
from views.signup_view import SignupView
from identity import ensure_current_riot_identity


async def setup(bot):
    await bot.add_cog(SignupCommand(bot))


class SignupCommand(BotCommands):
    @commands.command()
    async def signup(self, ctx):
        if not await ensure_perms(ctx):
            return

        if self.bot.signup_active:
            await ctx.send("A signup is already in progress.")
            return

        if self.bot.match_not_reported:
            await ctx.send("Report the last match before starting another one.")
            return

        ok, msg, _db_user = await ensure_current_riot_identity(ctx.author.id)
        if not ok:
            await ctx.send(msg)
            return

        self.bot.load_mmr_data()
        print("[DEBUG] Reloaded MMR data at start of signup")

        # Clear any existing signup view and state
        if self.bot.signup_view is not None:
            self.bot.signup_view.cancel_signup_refresh()
            self.bot.signup_view = None

        # Reset all match related states
        self.bot.signup_active = True
        self.bot.queue = []
        self.bot.captain1 = None
        self.bot.captain2 = None
        self.bot.team1 = []
        self.bot.team2 = []
        self.bot.chosen_mode = None
        self.bot.selected_map = None

        self.bot.match_name = f"match-{random.randrange(1, 10**4):04}"

        try:
            self.bot.match_role = await ctx.guild.create_role(
                name=self.bot.match_name, hoist=True
            )
            await ctx.guild.edit_role_positions(positions={self.bot.match_role: 5})

            match_channel_permissions = {
                ctx.guild.default_role: discord.PermissionOverwrite(
                    send_messages=False
                ),
                self.bot.match_role: discord.PermissionOverwrite(send_messages=True),
            }

            self.bot.match_channel = await ctx.guild.create_text_channel(
                name=self.bot.match_name,
                category=ctx.channel.category,
                position=0,
                overwrites=match_channel_permissions,
            )

            self.bot.signup_view = SignupView(ctx, self.bot)

            self.bot.current_signup_message = await self.bot.match_channel.send(
                embed=self.bot.signup_view.get_signup_embed(), view=self.bot.signup_view
            )

            await ctx.send(f"Queue started! Signup: <#{self.bot.match_channel.id}>")
        except Exception as e:
            # Cleanup
            self.bot.signup_active = False
            if hasattr(self.bot, "match_role") and self.bot.match_role:
                try:
                    await self.bot.match_role.delete()
                except:
                    pass
            if hasattr(self.bot, "match_channel") and self.bot.match_channel:
                try:
                    await self.bot.match_channel.delete()
                except:
                    pass
            await ctx.send(f"Error setting up queue: {str(e)}")


async def ensure_perms(ctx) -> bool:
    me = ctx.guild.me
    missing = []
    if not me.guild_permissions.manage_roles:
        missing.append("Manage Roles")
    if not me.guild_permissions.manage_channels:
        missing.append("Manage Channels")
    if missing:
        await ctx.send(
            f"I need the following permissions in this server: {', '.join(missing)}"
        )
        return False
    return True
