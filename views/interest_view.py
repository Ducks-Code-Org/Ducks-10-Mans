# views/interest_view.py
import logging
from datetime import datetime, timezone

import discord
from discord.ui import Button, View

from database import interests, users
from globals import TIME_ZONE_CST

log = logging.getLogger(__name__)


def slot_is_past(scheduled_at_utc, now=None) -> bool:
    """True once the slot's exact time has passed — the board is retired."""
    return (now or datetime.now(timezone.utc)) >= scheduled_at_utc


class InterestView(View):
    """
    A simple join/leave interest view for a planned Duck's 10 Mans slot.
    Each message is tied to one UTC timestamp (the slot time).

    Every click edits the board through the click's own interaction
    (`response.edit_message`), whose webhook token is always fresh — the
    board works at any age until the slot passes (issue #257).
    """

    def __init__(self, scheduled_at_utc, timeout=None):
        super().__init__(timeout=timeout)
        self.scheduled_at_utc = scheduled_at_utc

        # Buttons
        self.join_button = Button(style=discord.ButtonStyle.success, label="I’m in ✅")
        self.leave_button = Button(
            style=discord.ButtonStyle.secondary, label="Remove ❌"
        )
        self.refresh_button = Button(
            style=discord.ButtonStyle.primary, label="Refresh 🔁"
        )

        self.join_button.callback = self.join_callback
        self.leave_button.callback = self.leave_callback
        self.refresh_button.callback = self.refresh_callback

        self.add_item(self.join_button)
        self.add_item(self.leave_button)
        self.add_item(self.refresh_button)

    # helper functions
    def _slot_doc(self):
        return interests.find_one({"scheduled_at_utc": self.scheduled_at_utc})

    def _ensure_membership(self, user_id: str, add: bool):
        if add:
            return interests.find_one_and_update(
                {"scheduled_at_utc": self.scheduled_at_utc},
                {"$addToSet": {"interested_ids": user_id}},
                return_document=True,
            )
        else:
            return interests.find_one_and_update(
                {"scheduled_at_utc": self.scheduled_at_utc},
                {"$pull": {"interested_ids": user_id}},
                return_document=True,
            )

    def _format_header(self):
        local = self.scheduled_at_utc.astimezone(TIME_ZONE_CST)
        stamp = int(self.scheduled_at_utc.timestamp())
        return (
            f"**Duck’s 10 Mans – Interest Slot**\n"
            f"Time: **{local.strftime('%Y-%m-%d %I:%M %p %Z')}**  •  <t:{stamp}:F> • <t:{stamp}:R>"
        )

    def _format_list(self, doc):
        ids = [str(i) for i in (doc.get("interested_ids") or [])]
        if not ids:
            return "_Nobody yet — click **I’m in** to be the first!_"

        lines = []
        for uid in ids:
            udoc = users.find_one({"discord_id": uid})
            if udoc and udoc.get("name") and udoc.get("tag"):
                lines.append(f"• **{udoc['name']}#{udoc['tag']}** (<@{uid}>)")
            else:
                lines.append(f"• <@{uid}>")
        return "\n".join(lines)

    def _board_embed(self, doc=None) -> discord.Embed:
        """The full board: header plus roster, built before posting."""
        doc = doc or self._slot_doc() or {"interested_ids": []}
        count = len(doc.get("interested_ids") or [])
        body = self._format_list(doc)
        return discord.Embed(
            title="",
            description=f"{self._format_header()}\n\n**Interested ({count})**:\n{body}",
            color=discord.Color.green(),
        )

    def _retired(self) -> "InterestView":
        """A copy of this board with every button grayed out (slot over)."""
        view = InterestView(self.scheduled_at_utc, timeout=None)
        for item in view.children:
            item.disabled = True
        return view

    async def _retire_if_past(self, interaction: discord.Interaction) -> bool:
        """On an expired slot: gray out the board, change nothing, return True."""
        if not slot_is_past(self.scheduled_at_utc):
            return False
        await interaction.response.edit_message(
            embed=self._board_embed(), view=self._retired()
        )
        return True

    # end of helpers, start of callback functions
    async def join_callback(self, interaction: discord.Interaction):
        if await self._retire_if_past(interaction):
            return
        user_id = str(interaction.user.id)
        self._ensure_membership(user_id, add=True)
        log.info("%s joined interest slot %s", interaction.user, self.scheduled_at_utc)
        # One atomic Discord write: the ack IS the board update, carrying
        # the member who just clicked (issue #257).
        await interaction.response.edit_message(
            embed=self._board_embed(), view=self
        )

    async def leave_callback(self, interaction: discord.Interaction):
        if await self._retire_if_past(interaction):
            return
        user_id = str(interaction.user.id)
        self._ensure_membership(user_id, add=False)
        log.info("%s left interest slot %s", interaction.user, self.scheduled_at_utc)
        await interaction.response.edit_message(
            embed=self._board_embed(), view=self
        )

    async def refresh_callback(self, interaction: discord.Interaction):
        if await self._retire_if_past(interaction):
            return
        await interaction.response.edit_message(
            embed=self._board_embed(), view=self
        )
