from discord import Interaction


async def safe_reply(interaction: Interaction, *args, **kwargs):
    """Send an interaction reply exactly once; afterward, use followup."""
    if interaction.response.is_done():
        await interaction.followup.send(*args, **kwargs)
    else:
        await interaction.response.send_message(*args, **kwargs)
