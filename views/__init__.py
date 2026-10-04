import logging

import discord
from discord import Interaction

log = logging.getLogger(__name__)


async def safe_reply(interaction: Interaction, *args, **kwargs):
    """Send an interaction reply exactly once; afterward, use followup."""
    if interaction.response.is_done():
        await interaction.followup.send(*args, **kwargs)
    else:
        await interaction.response.send_message(*args, **kwargs)


async def defer_component(interaction: Interaction) -> bool:
    """Component-style defer (update-message intent) for a vote click.

    The deferred acknowledgement is completed by reflect_board's
    edit_original_response, so the vote write and the board write are the
    same Discord request (issue #258). Returns False when the interaction
    expired — the caller must not queue or handle it.
    """
    if interaction.response.is_done():
        return True
    try:
        await interaction.response.defer(thinking=False)
    except (discord.NotFound, discord.HTTPException):
        return False
    return True


async def reflect_board(interaction: Interaction, view) -> bool:
    """Push the post-vote board through the click's own interaction.

    Returns False when the board write failed; the caller must not send the
    vote confirmation then, so the confirmation can never lead its board
    (issue #258).
    """
    try:
        await interaction.edit_original_response(view=view)
    except (discord.NotFound, discord.HTTPException) as e:
        log.warning("Could not update the vote board after a click: %s", e)
        return False
    return True
