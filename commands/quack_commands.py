"""Slash commands for Quack Coins: betting, doubledown, and map overrides (issue #34)."""

import discord
from discord import app_commands

from commands import BotCommands
from quack_coins import doubledown, place_bet, setmap_override


async def setup(bot):
    await bot.add_cog(QuackCommands(bot))


class QuackCommands(BotCommands):
    bet = app_commands.Group(name="bet", description="Bet Quack Coins on the match")

    @bet.command(name="attackers", description="Bet on the Attackers")
    @app_commands.describe(amount="How many Quack Coins to bet (min 1)")
    async def bet_attackers(self, interaction: discord.Interaction, amount: int):
        await self._gated_reply(
            interaction,
            lambda: place_bet(self.bot, str(interaction.user.id), "attackers", amount),
        )

    @bet.command(name="defenders", description="Bet on the Defenders")
    @app_commands.describe(amount="How many Quack Coins to bet (min 1)")
    async def bet_defenders(self, interaction: discord.Interaction, amount: int):
        await self._gated_reply(
            interaction,
            lambda: place_bet(self.bot, str(interaction.user.id), "defenders", amount),
        )

    @app_commands.command(
        name="doubledown",
        description="Spend 5 Quack Coins to double your MMR change for this match",
    )
    async def doubledown_command(self, interaction: discord.Interaction):
        await self._gated_reply(
            interaction, lambda: doubledown(self.bot, str(interaction.user.id))
        )

    @app_commands.command(
        name="setmap",
        description="Spend Quack Coins to override the chosen map (costs 3, +1 each repeat)",
    )
    @app_commands.describe(map_name="Map from the All Maps pool")
    async def setmap_command(self, interaction: discord.Interaction, map_name: str):
        await self._gated_reply(
            interaction,
            lambda: setmap_override(self.bot, str(interaction.user.id), map_name),
            ephemeral=False,
        )

    async def _gated_reply(self, interaction, message_factory, ephemeral: bool = True):
        from globals import feature_enabled

        if not feature_enabled("quack_coins"):
            await interaction.response.send_message(
                "Quack Coins features are disabled.", ephemeral=True
            )
            return
        if not self.bot.match_ongoing:
            await interaction.response.send_message(
                "No match is running right now.", ephemeral=True
            )
            return
        await interaction.response.send_message(message_factory(), ephemeral=ephemeral)
