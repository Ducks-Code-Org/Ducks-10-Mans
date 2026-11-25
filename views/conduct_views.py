"""Modals and persistent vote views for conduct reporting (issue #269)."""

import logging

import discord

from game.conduct import (
    BAN,
    ConductError,
    build_application_embed,
    build_case_embed,
    cast_application_vote,
    cast_vote,
    close_application,
    close_phase,
    file_application,
    file_report,
    revoke_warning,
    should_close,
    should_close_application,
    utcnow,
)
from views import defer_component, safe_reply

log = logging.getLogger(__name__)


async def _complete_ack(interaction) -> None:
    """Finish a component update defer without changing the board."""
    try:
        await interaction.edit_original_response()
    except (discord.NotFound, discord.HTTPException) as e:
        log.warning("Could not complete the conduct vote acknowledgement: %s", e)


class ReportModal(discord.ui.Modal, title="Report a player"):
    def __init__(self):
        super().__init__(timeout=None)
        self.subject = discord.ui.UserSelect(
            placeholder="Select the reported user", min_values=1, max_values=1
        )
        self.rule = discord.ui.TextInput(
            style=discord.TextStyle.short,
            placeholder="Which rule was broken?",
            max_length=200,
        )
        self.description = discord.ui.TextInput(
            style=discord.TextStyle.paragraph,
            placeholder="What happened? Include evidence links here.",
            max_length=2000,
        )
        self.evidence_files = discord.ui.FileUpload(
            required=False, min_values=0, max_values=5
        )
        self.approx_time = discord.ui.TextInput(
            style=discord.TextStyle.short,
            placeholder="Approximate time, if you know it",
            required=False,
            max_length=100,
        )
        self.add_item(
            discord.ui.Label(
                text="Reported user",
                description=(
                    "All answers are anonymous and your name is not attached to "
                    "the report."
                ),
                component=self.subject,
            )
        )
        self.add_item(
            discord.ui.Label(
                text="Rule broken",
                description=(
                    "The fact that you used this command is visible in the channel."
                ),
                component=self.rule,
            )
        )
        self.add_item(
            discord.ui.Label(
                text="What happened?",
                description=(
                    "Paste evidence links here. Attach at least one file or link."
                ),
                component=self.description,
            )
        )
        self.add_item(
            discord.ui.Label(text="Evidence files", component=self.evidence_files)
        )
        self.add_item(
            discord.ui.Label(text="Approximate time", component=self.approx_time)
        )

    async def on_submit(self, interaction, /):
        await interaction.response.defer(ephemeral=True)
        selected = self.subject.values[0] if self.subject.values else None
        subject = None
        if selected is not None:
            subject = interaction.guild.get_member(selected.id) or selected
        try:
            await file_report(
                interaction.client,
                interaction,
                subject,
                self.rule.value,
                self.description.value,
                [],
                self.approx_time.value,
                self.evidence_files.values,
            )
        except ConductError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        except Exception as e:  # never log the reporter's identity
            log.error("Anonymous conduct report failed: %s", type(e).__name__)
            await interaction.followup.send(
                "Could not file the report right now.", ephemeral=True
            )
            return
        await interaction.followup.send(
            "Your report has been filed anonymously.", ephemeral=True
        )


class ApplicationModal(discord.ui.Modal, title="Apply to the Duck Council"):
    def __init__(self):
        super().__init__(timeout=None)
        self.answer = discord.ui.TextInput(
            style=discord.TextStyle.paragraph,
            placeholder="Why should you be let into the Duck Council?",
            max_length=4000,
        )
        self.add_item(
            discord.ui.Label(
                text="Why should you be let into the Duck Council?",
                component=self.answer,
            )
        )

    async def on_submit(self, interaction, /):
        await interaction.response.defer(ephemeral=True)
        try:
            await file_application(interaction.client, interaction, self.answer.value)
        except ConductError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        except Exception as e:
            log.error("Council application failed: %s", e, exc_info=e)
            await interaction.followup.send(
                "Could not submit the application right now.", ephemeral=True
            )
            return
        await interaction.followup.send(
            "Your application has been submitted to the Duck Council.",
            ephemeral=True,
        )


class UnwarnReasonModal(discord.ui.Modal, title="Revoke warning"):
    def __init__(self, sanction_id: str, subject: str):
        super().__init__(timeout=None)
        self.sanction_id = sanction_id
        self.subject = subject
        self.reason = discord.ui.TextInput(
            style=discord.TextStyle.paragraph,
            placeholder="Optional reason",
            required=False,
            max_length=500,
        )
        self.add_item(discord.ui.Label(text="Reason (optional)", component=self.reason))

    async def on_submit(self, interaction, /):
        await interaction.response.defer(ephemeral=True)
        try:
            revoke_warning(
                self.sanction_id,
                interaction.user.id,
                self.reason.value,
            )
        except ConductError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        except Exception as e:
            log.error("Warning revocation failed: %r", e, exc_info=e)
            await interaction.followup.send(
                "Could not revoke that warning right now.", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"Revoked a warning for {self.subject}.", ephemeral=True
        )


class UnwarnSelectView(discord.ui.View):
    """Ephemeral picker: choose one of the target's active warnings."""

    def __init__(self, warnings: list[dict], subject: str):
        super().__init__(timeout=300)
        self.subject = subject
        options = []
        # shortcut: Discord caps select menus at 25 options, so only the first
        # 25 active warnings can be revoked; paginate if subjects ever exceed it.
        for warning in warnings[:25]:
            issued = warning.get("issued_at")
            expires = warning.get("expires_at")
            label = f"Warning issued {issued:%Y-%m-%d}"
            description = f"Expires {expires:%Y-%m-%d}"
            options.append(
                discord.SelectOption(
                    label=label,
                    description=description,
                    value=warning["_id"],
                )
            )
        self.selector = discord.ui.Select(
            placeholder="Choose a warning to revoke", options=options
        )
        self.selector.callback = self._select
        self.add_item(self.selector)

    async def _select(self, interaction):
        try:
            warning_id = self.selector.values[0]
            await interaction.response.send_modal(
                UnwarnReasonModal(warning_id, self.subject)
            )
        except (discord.NotFound, discord.HTTPException) as e:
            log.error("Could not open the warning revocation modal: %s", e, exc_info=e)
            await safe_reply(
                interaction,
                "Could not open the revocation form. Try `/unwarn` again.",
                ephemeral=True,
            )


async def handle_vote(interaction, case_id: str, phase: str, choice: str, view):
    bot = interaction.client
    if not await defer_component(interaction):
        return
    try:
        doc = cast_vote(case_id, phase, str(interaction.user.id), choice)
    except ConductError as e:
        await _complete_ack(interaction)
        await interaction.followup.send(str(e), ephemeral=True)
        return
    except Exception as e:
        log.error("Conduct vote failed: %r", e, exc_info=e)
        await _complete_ack(interaction)
        await interaction.followup.send(
            "Could not record that vote right now.", ephemeral=True
        )
        return
    now = utcnow()
    try:
        if should_close(doc, phase, now):
            board_ok = await close_phase(bot, doc, phase, now, interaction=interaction)
            if board_ok:
                await interaction.followup.send(
                    "Your vote has been recorded. The vote is closed.", ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "Your vote was recorded, but the board could not be updated.",
                    ephemeral=True,
                )
            return
        await interaction.edit_original_response(
            embed=build_case_embed(doc, now), view=view
        )
    except (discord.NotFound, discord.HTTPException) as e:
        log.error("Could not update the conduct board: %s", e, exc_info=e)
        await interaction.followup.send(
            "Your vote was recorded, but the board could not be updated.",
            ephemeral=True,
        )
        return
    except Exception as e:
        log.error("Conduct vote handling failed: %r", e, exc_info=e)
        await _complete_ack(interaction)
        await interaction.followup.send(
            "Could not update the vote board right now.", ephemeral=True
        )
        return
    await interaction.followup.send("Your vote has been recorded.", ephemeral=True)


class ConductVoteView(discord.ui.View):
    def __init__(self, case_id: str, phase: str):
        super().__init__(timeout=None)
        self.case_id = str(case_id)
        self.phase = phase
        label = "Ban" if phase == BAN else "Warn"
        yes = discord.ui.Button(
            label=f"{label}: Yes",
            style=discord.ButtonStyle.success,
            custom_id=f"conduct:{self.case_id}:{phase}:yes",
        )
        no = discord.ui.Button(
            label=f"{label}: No",
            style=discord.ButtonStyle.danger,
            custom_id=f"conduct:{self.case_id}:{phase}:no",
        )
        yes.callback = self._yes
        no.callback = self._no
        self.add_item(yes)
        self.add_item(no)

    async def _yes(self, interaction):
        await handle_vote(interaction, self.case_id, self.phase, "yes", self)

    async def _no(self, interaction):
        await handle_vote(interaction, self.case_id, self.phase, "no", self)


async def handle_application_vote(interaction, app_id: str, choice: str, view):
    bot = interaction.client
    if not await defer_component(interaction):
        return
    try:
        doc = cast_application_vote(app_id, str(interaction.user.id), choice)
    except ConductError as e:
        await _complete_ack(interaction)
        await interaction.followup.send(str(e), ephemeral=True)
        return
    except Exception as e:
        log.error("Application vote failed: %r", e, exc_info=e)
        await _complete_ack(interaction)
        await interaction.followup.send(
            "Could not record that vote right now.", ephemeral=True
        )
        return
    now = utcnow()
    try:
        if should_close_application(doc, now):
            board_ok = await close_application(bot, doc, now, interaction=interaction)
            if board_ok:
                await interaction.followup.send(
                    "Your vote has been recorded. The application is closed.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "Your vote was recorded, but the board could not be updated.",
                    ephemeral=True,
                )
            return
        await interaction.edit_original_response(
            embed=build_application_embed(doc, now), view=view
        )
    except (discord.NotFound, discord.HTTPException) as e:
        log.error("Could not update the application board: %s", e, exc_info=e)
        await interaction.followup.send(
            "Your vote was recorded, but the board could not be updated.",
            ephemeral=True,
        )
        return
    except Exception as e:
        log.error("Application vote handling failed: %r", e, exc_info=e)
        await _complete_ack(interaction)
        await interaction.followup.send(
            "Could not update the application board right now.", ephemeral=True
        )
        return
    await interaction.followup.send("Your vote has been recorded.", ephemeral=True)


class ApplicationVoteView(discord.ui.View):
    def __init__(self, app_id: str):
        super().__init__(timeout=None)
        self.app_id = str(app_id)
        yes = discord.ui.Button(
            label="Yes",
            style=discord.ButtonStyle.success,
            custom_id=f"conduct-app:{self.app_id}:yes",
        )
        no = discord.ui.Button(
            label="No",
            style=discord.ButtonStyle.danger,
            custom_id=f"conduct-app:{self.app_id}:no",
        )
        yes.callback = self._yes
        no.callback = self._no
        self.add_item(yes)
        self.add_item(no)

    async def _yes(self, interaction):
        await handle_application_vote(interaction, self.app_id, "yes", self)

    async def _no(self, interaction):
        await handle_application_vote(interaction, self.app_id, "no", self)
