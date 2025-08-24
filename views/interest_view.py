# views/interest_view.py
import discord
from discord.ui import View, Button
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from database import interests, users


class InterestView(View):
    """
    A simple join/leave interest view for a planned Duck's 10 Mans slot.
    Each message is tied to one UTC timestamp (the slot time).
    """
    def __init__(self, scheduled_at_utc, message=None, timeout=None):
        super().__init__(timeout=timeout)
        self.scheduled_at_utc = scheduled_at_utc 
        self.message = message 

        # Buttons
        self.join_button = Button(style=discord.ButtonStyle.success, label="I’m in ✅")
        self.leave_button = Button(style=discord.ButtonStyle.secondary, label="Remove ❌")
        self.refresh_button = Button(style=discord.ButtonStyle.primary, label="Refresh 🔁")

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
        tz = ZoneInfo("America/Chicago")
        local = self.scheduled_at_utc.astimezone(tz)
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

    async def _render(self, interaction: discord.Interaction):
        doc = self._slot_doc() or {"interested_ids": []}
        count = len(doc.get("interested_ids") or [])
        body = self._format_list(doc)
        embed = discord.Embed(
            title="",
            description=f"{self._format_header()}\n\n**Interested ({count})**:\n{body}",
            color=discord.Color.green()
        )
        if self.message:
            await self.message.edit(embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)

    # end of helpers, start of callback functions
    async def join_callback(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        self._ensure_membership(user_id, add=True)
        await interaction.response.defer(thinking=False)
        await self._render(interaction)

    async def leave_callback(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        self._ensure_membership(user_id, add=False)
        await interaction.response.defer(thinking=False)
        await self._render(interaction)

    async def refresh_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=False)
        await self._render(interaction)