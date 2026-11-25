"""Slash commands for conduct reporting and Duck Council applications (issue #269)."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from game.conduct import (
    OWNER_ROLE_NAME,
    active_warnings,
)
from views import safe_reply
from views.conduct_views import (
    ApplicationModal,
    ReportModal,
    UnwarnSelectView,
)

log = logging.getLogger(__name__)


def _dev_mode_blocked(interaction) -> bool:
    """Keep the bot-wide dev-mode admin gate on slash-only commands."""
    if not getattr(interaction.client, "dev_mode", False):
        return False
    permissions = getattr(interaction.user, "guild_permissions", None)
    return not (permissions and permissions.administrator)


async def setup(bot):
    await bot.add_cog(ConductCommands(bot))


class ConductCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="reportplayer",
        description="File an anonymous code-of-conduct report",
    )
    @app_commands.guild_only()
    async def reportplayer(self, interaction: discord.Interaction):
        # Deliberately no logging here: the reporter's identity must not be
        # written to any log or record.
        if _dev_mode_blocked(interaction):
            await interaction.response.send_message(
                "You can't use that command right now.", ephemeral=True
            )
            return
        try:
            await interaction.response.send_modal(ReportModal())
        except (discord.NotFound, discord.HTTPException) as e:
            log.error("Could not open the report modal: %s", type(e).__name__)
            await safe_reply(
                interaction,
                "Could not open the report form. Try again.",
                ephemeral=True,
            )

    @app_commands.command(
        name="applycouncil",
        description="Apply to join the Duck Council",
    )
    @app_commands.guild_only()
    async def applycouncil(self, interaction: discord.Interaction):
        if _dev_mode_blocked(interaction):
            await interaction.response.send_message(
                "You can't use that command right now.", ephemeral=True
            )
            return
        try:
            await interaction.response.send_modal(ApplicationModal())
        except (discord.NotFound, discord.HTTPException) as e:
            log.error("Could not open the council application modal: %s", e, exc_info=e)
            await safe_reply(
                interaction,
                "Could not open the application form. Try again.",
                ephemeral=True,
            )

    @app_commands.command(
        name="unwarn",
        description="Owner-only: revoke one of a player's active warnings",
    )
    @app_commands.guild_only()
    @app_commands.describe(user="The player whose warning to revoke")
    async def unwarn(self, interaction: discord.Interaction, user: discord.User):
        if _dev_mode_blocked(interaction):
            await interaction.response.send_message(
                "You can't use that command right now.", ephemeral=True
            )
            return
        try:
            await interaction.response.defer(ephemeral=True)
        except (discord.NotFound, discord.HTTPException) as e:
            log.error("Could not defer /unwarn: %s", e, exc_info=e)
            return
        if (
            discord.utils.get(
                getattr(interaction.user, "roles", []), name=OWNER_ROLE_NAME
            )
            is None
        ):
            await interaction.followup.send(
                "Only the Owner can revoke warnings.", ephemeral=True
            )
            return
        try:
            warnings = active_warnings(str(user.id))
        except Exception as e:
            log.error("Could not read warning history: %r", e, exc_info=e)
            await interaction.followup.send(
                "Could not read that player's warnings right now.", ephemeral=True
            )
            return
        if not warnings:
            await interaction.followup.send(
                f"{user.display_name} has no active warnings.", ephemeral=True
            )
            return
        try:
            await interaction.followup.send(
                f"Active warnings for {user.display_name}:",
                view=UnwarnSelectView(warnings, user.display_name),
                ephemeral=True,
            )
        except (discord.NotFound, discord.HTTPException) as e:
            log.error("Could not show the warning selector: %s", e, exc_info=e)
