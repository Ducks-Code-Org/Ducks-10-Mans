"""Conduct reporting, Duck Council voting, and council applications (issue #269).

The Duck Council votes anonymously on conduct reports and applications. This
module owns the case/sanction/application state machine and every Discord
side effect; the views only translate interactions into calls here.
"""

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import discord

from database import conduct_applications, conduct_cases, conduct_sanctions

log = logging.getLogger(__name__)

COUNCIL_CHANNEL_NAME = "duck-council"
PUBLIC_LOG_CHANNEL_NAME = "announcements"
COUNCIL_ROLE_NAME = "Duck Council"
OWNER_ROLE_NAME = "Owner"

BAN = "ban"
WARN = "warn"
CLOSED = "closed"
APP_OPEN = "open"
APP_APPROVED = "approved"
APP_REJECTED = "rejected"

VOTE_WINDOW = timedelta(hours=48)
APPLICATION_WINDOW = timedelta(days=7)
WARNING_LIFETIME = timedelta(days=365)  # 12 months
PURGE_AFTER = timedelta(days=90)
TICK_SECONDS = 60


class ConductError(Exception):
    """A user-facing failure; the message is safe to show ephemerally."""


class SetupError(ConductError):
    """The guild is missing required conduct channels or role permissions."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """Mongo returns naive UTC datetimes; normalize before comparing."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


# ---------------------------------------------------------------------------
# Vote math
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PhaseResult:
    yes: int
    no: int
    cast: int
    prior: int
    quorum: int
    quorum_met: bool
    passed: bool


def quorum_size(roster: list[str]) -> int:
    """At least half of the snapshotted council, rounded up."""
    return (len(roster) + 1) // 2


def phase_counts(doc: dict, phase: str) -> tuple[int, int, int]:
    votes = (doc.get("votes") or {}).get(phase) or {}
    yes = sum(1 for choice in votes.values() if choice == "yes")
    no = sum(1 for choice in votes.values() if choice == "no")
    return yes, no, len(votes)


def evaluate_phase(doc: dict, phase: str) -> PhaseResult:
    yes, no, cast = phase_counts(doc, phase)
    prior = 1 if phase == BAN and doc.get("prior_warning") else 0
    roster = doc.get("roster") or []
    quorum = quorum_size(roster)
    # The prior-warning +1 must never create a quorum by itself: a phase with
    # no human council voters cannot be quorate.
    quorum_met = bool(roster) and cast >= quorum
    total = yes + no + prior
    if phase == BAN:
        passed = quorum_met and total > 0 and (yes + prior) * 2 > total
    else:
        # A Warn tie passes: ties default to the lesser sanction.
        passed = quorum_met and total > 0 and yes * 2 >= total
    return PhaseResult(yes, no, cast, prior, quorum, quorum_met, passed)


def should_close(doc: dict, phase: str, now: datetime) -> bool:
    """Close when all voted, at the deadline, or when the result is decided."""
    roster = doc.get("roster") or []
    result = evaluate_phase(doc, phase)
    remaining = len(roster) - result.cast
    if not roster:
        # No council members were snapshotted: only the deadline can close.
        return now >= as_utc(doc["deadline"])
    if result.cast >= len(roster):
        return True
    if now >= as_utc(doc["deadline"]):
        return True
    if phase == BAN:
        # Passed even if every remaining human votes no.
        if result.quorum_met and result.yes + result.prior > result.no + remaining:
            return True
        # Cannot pass even if every remaining human votes yes (ties go to Warn).
        if result.yes + result.prior + remaining <= result.no:
            return True
    else:
        # Warn passes even if every remaining human votes no (tie passes).
        if result.quorum_met and result.yes >= result.no + remaining:
            return True
        # Warn fails even if every remaining human votes yes.
        if result.no > result.yes + remaining:
            return True
    return False


def phase_result_line(phase: str, result: PhaseResult) -> str:
    if phase == BAN:
        if result.prior:
            tally = (
                f"{result.yes} council + 1 prior warning = "
                f"{result.yes + result.prior} to {result.no}"
            )
        else:
            tally = f"{result.yes} to {result.no}"
        label = "Ban"
    else:
        tally = f"{result.yes} to {result.no}"
        label = "Warn"
    verdict = "passed" if result.passed else "not passed"
    return f"{label}: {tally} — {verdict}"


def quorum_line(result: PhaseResult) -> str:
    status = "met" if result.quorum_met else "not met"
    return (
        f"Quorum: {result.cast}/{result.quorum} council members voted "
        f"({result.quorum} required) — {status}"
    )


def vote_tally_line(doc: dict, phase: str) -> str:
    result = evaluate_phase(doc, phase)
    if phase == BAN and result.prior:
        return (
            f"Yes: {result.yes} council + 1 prior warning\n"
            f"No: {result.no}\n"
            f"Quorum: {result.cast}/{result.quorum} voted — "
            f"{'met' if result.quorum_met else 'not met'}"
        )
    return (
        f"Yes: {result.yes}\nNo: {result.no}\n"
        f"Quorum: {result.cast}/{result.quorum} voted — "
        f"{'met' if result.quorum_met else 'not met'}"
    )


def application_result_line(doc: dict) -> str:
    tally = doc.get("tally")
    if tally:
        yes = tally.get("yes", 0)
        no = tally.get("no", 0)
        not_voted = tally.get("not_voted", 0)
    else:
        votes = doc.get("votes") or {}
        yes = sum(1 for choice in votes.values() if choice == "yes")
        no = sum(1 for choice in votes.values() if choice == "no")
        not_voted = max(0, len(doc.get("roster") or []) - len(votes))
    verdict = "approved" if doc.get("status") == APP_APPROVED else "rejected"
    return f"Yes: {yes} · No: {no} · Not voted: {not_voted} — {verdict}"


# ---------------------------------------------------------------------------
# Sanctions and history
# ---------------------------------------------------------------------------


def _is_active_warning(sanction: dict, now: datetime) -> bool:
    if sanction.get("type") != WARN or sanction.get("revoked_at"):
        return False
    expires = as_utc(sanction.get("expires_at"))
    return expires is not None and expires > now


def active_warnings(subject_id: str, now: datetime | None = None) -> list[dict]:
    now = now or utcnow()
    docs = conduct_sanctions.find({"subject_id": str(subject_id), "type": WARN})
    return [doc for doc in docs if _is_active_warning(doc, now)]


def active_warning_count(subject_id: str, now: datetime | None = None) -> int:
    return len(active_warnings(subject_id, now))


def history_lines(subject_id: str, now: datetime | None = None) -> list[str]:
    now = now or utcnow()
    docs = list(conduct_sanctions.find({"subject_id": str(subject_id)}))
    active = expired = revoked = bans = 0
    for doc in docs:
        if doc.get("type") == BAN:
            bans += 1
        elif doc.get("revoked_at"):
            revoked += 1
        elif _is_active_warning(doc, now):
            active += 1
        else:
            expired += 1
    return [
        f"Active warnings: {active}",
        f"Expired warnings (inactive): {expired}",
        f"Revoked warnings: {revoked}",
        f"Prior bans: {bans}",
    ]


def history_field(subject_id: str, now: datetime | None = None) -> str:
    return "\n".join(history_lines(subject_id, now))


def public_warning_line(name: str, active_count: int) -> str:
    return (
        f'User "{name}" has been given a warning for breaking the code of '
        f"conduct. They have {active_count} active warning(s)."
    )


def public_ban_line(name: str) -> str:
    return f'User "{name}" has been banned for breaking the code of conduct.'


def public_accept_line(name: str) -> str:
    return f'User "{name}" has been accepted into the Duck Council.'


def _clip(value: str, limit: int = 1024) -> str:
    value = value or "—"
    return value if len(value) <= limit else value[: limit - 1] + "…"


_LINK_RE = re.compile(r"https?://\S+")


def extract_links(text: str) -> list[str]:
    """Evidence links a reporter pasted into the description field."""
    return _LINK_RE.findall(text or "")


def _subject_name(doc: dict) -> str:
    return doc.get("subject_name") or f"<@{doc.get('subject_id')}>"


def _applicant_name(doc: dict) -> str:
    return doc.get("applicant_name") or f"<@{doc.get('applicant_id')}>"


def build_case_embed(doc: dict, now: datetime | None = None) -> discord.Embed:
    now = now or utcnow()
    embed = discord.Embed(title="Conduct Report", color=discord.Color.orange())
    embed.add_field(name="Reported user", value=_subject_name(doc), inline=True)
    if doc.get("approx_time"):
        embed.add_field(
            name="Approximate time", value=_clip(doc["approx_time"], 256), inline=True
        )
    embed.add_field(name="Rule broken", value=_clip(doc.get("rule")), inline=False)
    embed.add_field(
        name="Description",
        value=_clip(doc.get("description") or "[purged]"),
        inline=False,
    )
    evidence = "\n".join(doc.get("links") or [])
    uploaded = int(doc.get("evidence_files") or 0)
    if uploaded:
        note = f"{uploaded} uploaded file(s) attached"
        evidence = f"{evidence}\n{note}".strip() if evidence else note
    embed.add_field(name="Evidence", value=_clip(evidence), inline=False)
    embed.add_field(
        name="Conduct history",
        value=_clip(history_field(doc.get("subject_id"), now)),
        inline=False,
    )
    if doc.get("status") == WARN and doc.get("ban_result"):
        result = PhaseResult(**doc["ban_result"])
        embed.add_field(
            name="Ban result",
            value=_clip(f"{phase_result_line(BAN, result)}\n{quorum_line(result)}"),
            inline=False,
        )
    if doc.get("status") in (BAN, WARN):
        embed.add_field(
            name="Current phase",
            value="Ban vote" if doc["status"] == BAN else "Warn vote",
            inline=True,
        )
        embed.add_field(
            name="Tally",
            value=_clip(vote_tally_line(doc, doc["status"])),
            inline=True,
        )
    else:
        lines = list(doc.get("result_lines") or [])
        embed.add_field(name="Result", value=_clip("\n".join(lines)), inline=False)
    return embed


def build_application_embed(doc: dict, now: datetime | None = None) -> discord.Embed:
    now = now or utcnow()
    embed = discord.Embed(
        title="Duck Council Application", color=discord.Color.blurple()
    )
    embed.add_field(name="Applicant", value=_applicant_name(doc), inline=True)
    embed.add_field(
        name="Answer", value=_clip(doc.get("answer") or "[purged]"), inline=False
    )
    embed.add_field(
        name="Conduct history",
        value=_clip(history_field(doc.get("applicant_id"), now)),
        inline=False,
    )
    if doc.get("status") == APP_OPEN:
        votes = doc.get("votes") or {}
        yes = sum(1 for choice in votes.values() if choice == "yes")
        no = sum(1 for choice in votes.values() if choice == "no")
        embed.add_field(
            name="Tally", value=f"Yes: {yes}\nNo: {no}\nOne no rejects.", inline=False
        )
    else:
        embed.add_field(
            name="Result",
            value=_clip(
                "\n".join(
                    [application_result_line(doc)] + list(doc.get("result_lines") or [])
                )
            ),
            inline=False,
        )
    return embed


# ---------------------------------------------------------------------------
# Guild setup
# ---------------------------------------------------------------------------


def _channel_perms_ok(channel, guild, *, attachments: bool = False) -> bool:
    checker = getattr(channel, "permissions_for", None)
    if checker is None:
        return True
    try:
        perms = checker(guild.me)
    except Exception:
        return True
    if not (perms.view_channel and perms.send_messages):
        return False
    if attachments:
        return bool(
            getattr(perms, "embed_links", True)
            and getattr(perms, "attach_files", True)
            and getattr(perms, "read_message_history", True)
        )
    return True


async def ensure_setup(bot, guild):
    """Find (create if missing) the council channel and role, and find the
    server's announcements channel used for the public conduct log.

    Existing objects are verified but never rewritten. The announcements
    channel is reused as-is and never created. Returns
    ``(council_channel, announcements_channel, role, role_error)`` where
    role_error is set only when the bot cannot manage the role (applications
    must refuse).
    """
    council = discord.utils.get(guild.text_channels, name=COUNCIL_CHANNEL_NAME)
    council_existed = council is not None
    role = discord.utils.get(guild.roles, name=COUNCIL_ROLE_NAME)
    if role is None:
        try:
            role = await guild.create_role(
                name=COUNCIL_ROLE_NAME,
                mentionable=True,
                permissions=discord.Permissions.none(),
                reason="Conduct reporting setup (issue #269)",
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            log.error(
                "Conduct setup: could not create the Duck Council role: %s",
                e,
                exc_info=e,
            )
            raise SetupError(
                "Could not create the Duck Council role; check Manage Roles."
            ) from e
        try:
            if guild.owner is not None:
                await guild.owner.send(
                    "I created the Duck Council role. Assign it to the council "
                    "members so they receive conduct votes."
                )
        except (discord.Forbidden, discord.HTTPException, AttributeError):
            log.warning("Could not DM the guild owner about the Duck Council role")
        if council_existed:
            # The channel predates the role. Add the missing role overwrite
            # without touching any existing overwrite.
            setter = getattr(council, "set_permissions", None)
            if setter is not None:
                try:
                    await setter(
                        role,
                        overwrite=discord.PermissionOverwrite(
                            view_channel=True,
                            send_messages=True,
                            embed_links=True,
                            attach_files=True,
                            read_message_history=True,
                        ),
                        reason="Grant the new Duck Council role access (issue #269)",
                    )
                except (discord.Forbidden, discord.HTTPException) as e:
                    log.error(
                        "Conduct setup: could not grant the new Duck Council role "
                        "access to the existing #duck-council: %s",
                        e,
                        exc_info=e,
                    )
                    raise SetupError(
                        "The Duck Council role cannot access the existing "
                        "#duck-council; grant it access or delete the channel."
                    ) from e
    if not getattr(role, "mentionable", True):
        log.warning(
            "Conduct setup: the Duck Council role is not mentionable; role "
            "pings may not notify the council"
        )

    if council is None:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                embed_links=True,
                attach_files=True,
                read_message_history=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                embed_links=True,
                attach_files=True,
                read_message_history=True,
            ),
        }
        try:
            council = await guild.create_text_channel(
                COUNCIL_CHANNEL_NAME,
                overwrites=overwrites,
                reason="Conduct reporting setup (issue #269)",
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            log.error(
                "Conduct setup: could not create #duck-council: %s", e, exc_info=e
            )
            raise SetupError(
                "Could not create #duck-council; check Manage Channels."
            ) from e

    log_channel = discord.utils.get(guild.text_channels, name=PUBLIC_LOG_CHANNEL_NAME)
    if log_channel is None:
        log.error(
            "Conduct setup: #%s was not found; conduct outcomes cannot be "
            "announced publicly",
            PUBLIC_LOG_CHANNEL_NAME,
        )
        raise SetupError(
            f"Could not find #{PUBLIC_LOG_CHANNEL_NAME}; create it so conduct "
            "outcomes can be announced."
        )

    if not _channel_perms_ok(council, guild, attachments=True):
        log.error(
            "Conduct setup: missing view/send/embed/attach/history "
            "permissions in #duck-council"
        )
        raise SetupError(
            "I cannot view, send, embed links, attach files, and read history "
            "in #duck-council."
        )
    if not _channel_perms_ok(log_channel, guild):
        log.error(
            "Conduct setup: missing view/send permissions in #%s",
            PUBLIC_LOG_CHANNEL_NAME,
        )
        raise SetupError(
            f"I cannot view and send messages in #{PUBLIC_LOG_CHANNEL_NAME}."
        )

    guild_perms = getattr(guild.me, "guild_permissions", None)
    if guild_perms is not None:
        missing = [
            name
            for name in ("ban_members", "manage_roles", "manage_channels")
            if not getattr(guild_perms, name, False)
        ]
        if missing:
            log.error(
                "Conduct setup: bot is missing required permissions: %s",
                ", ".join(missing),
            )

    role_error = None
    if guild_perms is not None and not getattr(guild_perms, "manage_roles", True):
        role_error = (
            "I need the Manage Roles permission to grant the Duck Council role."
        )
    if getattr(guild.me, "top_role", None) is not None and (
        role.position >= guild.me.top_role.position
    ):
        role_error = (
            "The Duck Council role is above my highest role; I cannot grant "
            "it. Move my role above Duck Council and try again."
        )
        log.error("Conduct setup: %s", role_error)
    return council, log_channel, role, role_error


def council_roster(role) -> list[str]:
    return sorted(str(member.id) for member in role.members if not member.bot)


async def _dm(bot, guild, user_id: str, content: str, purpose: str) -> bool:
    user = guild.get_member(int(user_id))
    if user is None:
        user = bot.get_user(int(user_id))
    if user is None:
        try:
            user = await bot.fetch_user(int(user_id))
        except (discord.NotFound, discord.HTTPException):
            user = None
    if user is None:
        log.warning("Conduct DM (%s) failed: user %s not found", purpose, user_id)
        return False
    try:
        await user.send(content)
        return True
    except (discord.Forbidden, discord.HTTPException) as e:
        log.warning("Conduct DM (%s) failed for %s: %s", purpose, user_id, e)
        return False


async def _send_public_log(guild, line: str) -> bool:
    log_channel = discord.utils.get(guild.text_channels, name=PUBLIC_LOG_CHANNEL_NAME)
    if log_channel is None:
        log.error(
            "Conduct public log unavailable: #%s not found",
            PUBLIC_LOG_CHANNEL_NAME,
        )
        return False
    try:
        await log_channel.send(line)
        return True
    except (discord.Forbidden, discord.HTTPException) as e:
        log.error("Could not post to the conduct public log: %s", e, exc_info=e)
        return False


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


async def file_report(
    bot,
    interaction,
    subject,
    rule: str,
    description: str,
    links: list[str],
    approx_time: str,
    attachments,
):
    """Validate and publish an anonymous conduct report. Returns the case."""
    guild = interaction.guild
    if guild is None:
        raise ConductError("This command can only be used in a server.")
    if subject is None:
        raise ConductError("Select the reported user.")
    if subject.id == interaction.user.id:
        raise ConductError("You cannot report yourself.")
    if getattr(subject, "bot", False):
        raise ConductError("You cannot report a bot.")
    if getattr(subject, "roles", None) is None:
        raise ConductError("That user is not a member of this server.")
    role_names = {getattr(role, "name", "") for role in getattr(subject, "roles", [])}
    if COUNCIL_ROLE_NAME in role_names:
        raise ConductError("Duck Council members cannot be reported.")
    if OWNER_ROLE_NAME in role_names:
        raise ConductError("The server owner cannot be reported.")
    if not (rule or "").strip():
        raise ConductError("Enter the rule that was broken.")
    if not (description or "").strip():
        raise ConductError("Describe what happened.")
    links = [link.strip() for link in (links or []) if link and link.strip()]
    attachments = list(attachments or [])
    if not links:
        links = extract_links(description)
    if not links and not attachments:
        raise ConductError("Attach at least one file or link as evidence.")

    files = []
    for attachment in attachments:
        try:
            evidence_file = await attachment.to_file()
            # Neutralize filenames: a reporter's name can be in the file name.
            original = getattr(evidence_file, "filename", "") or ""
            suffix = "." + original.rsplit(".", 1)[1] if "." in original else ""
            evidence_file.filename = f"evidence-{len(files) + 1}{suffix}"
            files.append(evidence_file)
        except (discord.HTTPException, AttributeError, OSError) as e:
            log.warning("Could not re-upload conduct evidence: %s", type(e).__name__)
    if not links and not files:
        raise ConductError(
            "Could not re-upload the evidence; paste it as a link instead."
        )

    council, _, role, _ = await ensure_setup(bot, guild)
    now = utcnow()
    subject_id = str(subject.id)
    doc = {
        "_id": uuid.uuid4().hex,
        "guild_id": guild.id,
        "subject_id": subject_id,
        "subject_name": getattr(subject, "display_name", None) or subject.name,
        "rule": rule.strip(),
        "description": description.strip(),
        "links": links,
        "approx_time": (approx_time or "").strip() or None,
        "evidence_files": len(files),
        "status": BAN,
        "phase": BAN,
        "deadline": now + VOTE_WINDOW,
        "roster": council_roster(role),
        "votes": {BAN: {}, WARN: {}},
        "prior_warning": active_warning_count(subject_id, now) > 0,
        "created_at": now,
        "channel_id": council.id,
        "message_ids": [],
        "message_id": None,
        "ban_result": None,
        "warn_result": None,
        "outcome": None,
        "result_lines": [],
        "result_posted": False,
        "notified": False,
        "public_logged": False,
        "ban_applied": False,
        "warn_board_posted": True,
        "closed_at": None,
        "purge_after": None,
        "purged": False,
    }
    conduct_cases.insert_one(doc)
    embed = build_case_embed(doc, now)
    from views.conduct_views import ConductVoteView

    view = ConductVoteView(doc["_id"], BAN)
    if hasattr(bot, "add_view"):
        bot.add_view(view)
    try:
        message = await council.send(
            content=f"{role.mention} A code of conduct report has been filed.",
            embed=embed,
            view=view,
            files=files,
        )
    except (discord.Forbidden, discord.HTTPException) as e:
        conduct_cases.delete_one({"_id": doc["_id"]})
        log.error("Could not post the conduct report: %s", e, exc_info=e)
        raise ConductError("Could not post the report to #duck-council.") from e
    conduct_cases.update_one(
        {"_id": doc["_id"]},
        {
            "$set": {
                "message_id": message.id,
                "message_ids": [message.id],
            }
        },
    )
    return conduct_cases.find_one({"_id": doc["_id"]})


# ---------------------------------------------------------------------------
# Voting and outcomes
# ---------------------------------------------------------------------------


def cast_vote(case_id: str, phase: str, voter_id: str, choice: str) -> dict:
    """Record one final vote per council member; raises on any invalid vote."""
    doc = conduct_cases.find_one({"_id": case_id})
    if doc is None or doc.get("status") != phase:
        raise ConductError("This vote is closed.")
    if utcnow() >= as_utc(doc["deadline"]):
        raise ConductError("This vote is closed.")
    if str(voter_id) not in (doc.get("roster") or []):
        raise ConductError("You were not on the council when this vote opened.")
    result = conduct_cases.update_one(
        {"_id": case_id, f"votes.{phase}.{voter_id}": {"$exists": False}},
        {"$set": {f"votes.{phase}.{voter_id}": choice}},
    )
    if not result.modified_count:
        raise ConductError("You have already voted in this phase.")
    return conduct_cases.find_one({"_id": case_id})


async def _board_message(bot, doc, message=None):
    if message is not None:
        return message
    channel = None
    guild = bot.get_guild(doc.get("guild_id"))
    if guild is not None:
        channel = guild.get_channel(doc.get("channel_id"))
    if channel is None or doc.get("message_id") is None:
        return None
    try:
        return await channel.fetch_message(doc["message_id"])
    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
        log.warning("Could not fetch the conduct board message: %s", e)
        return None


async def _edit_board(message, embed, view=None, interaction=None):
    """Write the board; interaction-driven votes complete their own defer."""
    if interaction is not None:
        try:
            await interaction.edit_original_response(embed=embed, view=view)
            return True
        except (discord.NotFound, discord.HTTPException) as e:
            log.error("Could not update the conduct board: %s", e, exc_info=e)
            return False
    if message is None:
        return False
    try:
        await message.edit(embed=embed, view=view)
        return True
    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
        log.error("Could not update the conduct board: %s", e, exc_info=e)
        return False


async def _post_warn_board(bot, doc: dict, now=None) -> bool:
    """Post the Warn phase board; retried by the tick if the send failed."""
    now = now or utcnow()
    guild = bot.get_guild(doc.get("guild_id"))
    if guild is None:
        log.error("Could not post the Warn phase board: guild missing")
        return False
    role = discord.utils.get(guild.roles, name=COUNCIL_ROLE_NAME)
    channel = guild.get_channel(doc.get("channel_id"))
    if role is None or channel is None:
        log.error("Could not post the Warn phase board: role or channel missing")
        return False
    from views.conduct_views import ConductVoteView

    view = ConductVoteView(doc["_id"], WARN)
    if hasattr(bot, "add_view"):
        bot.add_view(view)
    try:
        message = await channel.send(
            content=f"{role.mention} The Warn vote is now open for a case.",
            embed=build_case_embed(doc, now),
            view=view,
        )
    except (discord.Forbidden, discord.HTTPException) as e:
        log.error("Could not post the Warn phase board: %s", e, exc_info=e)
        return False
    conduct_cases.update_one(
        {"_id": doc["_id"]},
        {
            "$set": {"message_id": message.id, "warn_board_posted": True},
            "$push": {"message_ids": message.id},
        },
    )
    return True


async def close_phase(
    bot, doc: dict, phase: str, now=None, message=None, interaction=None
):
    """Close a phase if due, applying the outcome and all notifications."""
    now = now or utcnow()
    doc = conduct_cases.find_one({"_id": doc["_id"]})
    if doc is None or doc.get("status") != phase:
        return False
    if not should_close(doc, phase, now):
        return False
    guild = bot.get_guild(doc.get("guild_id"))
    result = evaluate_phase(doc, phase)
    board = await _board_message(bot, doc, message) if interaction is None else None

    if phase == BAN:
        ban_result = {
            "yes": result.yes,
            "no": result.no,
            "cast": result.cast,
            "prior": result.prior,
            "quorum": result.quorum,
            "quorum_met": result.quorum_met,
            "passed": result.passed,
        }
        if result.passed:
            lines = [
                phase_result_line(BAN, result),
                quorum_line(result),
                "Outcome: Banned",
            ]
            conduct_cases.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "status": CLOSED,
                        "phase": CLOSED,
                        "outcome": BAN,
                        "ban_result": ban_result,
                        "result_lines": lines,
                        "closed_at": now,
                        "purge_after": now + PURGE_AFTER,
                    }
                },
            )
            doc = conduct_cases.find_one({"_id": doc["_id"]})
            board_ok = await _edit_board(
                board, build_case_embed(doc, now), view=None, interaction=interaction
            )
            await finish_case(bot, doc["_id"], now)
            return board_ok

        # Ban did not pass: open the Warn phase with a fresh roster snapshot.
        ban_board = dict(doc)
        ban_board["status"] = CLOSED
        ban_board["result_lines"] = [
            phase_result_line(BAN, result),
            quorum_line(result),
        ]
        role = None
        if guild is not None:
            role = discord.utils.get(guild.roles, name=COUNCIL_ROLE_NAME)
        roster = council_roster(role) if role is not None else []
        conduct_cases.update_one(
            {"_id": doc["_id"]},
            {
                "$set": {
                    "status": WARN,
                    "phase": WARN,
                    "deadline": now + VOTE_WINDOW,
                    "roster": roster,
                    "ban_result": ban_result,
                    "votes.warn": {},
                    "warn_board_posted": False,
                }
            },
        )
        doc = conduct_cases.find_one({"_id": doc["_id"]})
        board_ok = await _edit_board(
            board, build_case_embed(ban_board, now), view=None, interaction=interaction
        )
        if guild is None or role is None:
            log.error("Could not open the Warn phase: guild or role missing")
            return board_ok
        await _post_warn_board(bot, doc, now)
        return board_ok

    # Warn phase.
    outcome = WARN if result.passed else None
    warn_result = {
        "yes": result.yes,
        "no": result.no,
        "cast": result.cast,
        "prior": result.prior,
        "quorum": result.quorum,
        "quorum_met": result.quorum_met,
        "passed": result.passed,
    }
    ban_result_obj = PhaseResult(**doc["ban_result"])
    lines = [
        phase_result_line(BAN, ban_result_obj),
        quorum_line(ban_result_obj),
        phase_result_line(WARN, result),
        quorum_line(result),
        "Outcome: Warned" if outcome == WARN else "Outcome: No sanction",
    ]
    conduct_cases.update_one(
        {"_id": doc["_id"]},
        {
            "$set": {
                "status": CLOSED,
                "phase": CLOSED,
                "outcome": outcome,
                "warn_result": warn_result,
                "result_lines": lines,
                "closed_at": now,
                "purge_after": now + PURGE_AFTER,
            }
        },
    )
    doc = conduct_cases.find_one({"_id": doc["_id"]})
    board_ok = await _edit_board(
        board, build_case_embed(doc, now), view=None, interaction=interaction
    )
    await finish_case(bot, doc["_id"], now)
    return board_ok


async def finish_case(bot, case_id: str, now=None):
    """Apply sanctions, DMs, public log, and the result message (idempotent)."""
    now = now or utcnow()
    doc = conduct_cases.find_one({"_id": case_id})
    if doc is None or doc.get("status") != CLOSED:
        return
    guild = bot.get_guild(doc.get("guild_id"))
    outcome = doc.get("outcome")
    subject_id = doc.get("subject_id")
    subject_name = doc.get("subject_name") or subject_id

    if outcome in (BAN, WARN) and guild is not None:
        existing = conduct_sanctions.find_one({"case_id": case_id, "type": outcome})
        if outcome == BAN:
            # The DM must go out while the bot still shares a guild with the
            # subject, i.e. before the ban call.
            if not doc.get("notified"):
                await _dm(
                    bot,
                    guild,
                    subject_id,
                    "You have been banned from Ducks 10 Mans for breaking the "
                    "code of conduct. If you believe this is a mistake, contact "
                    "a server owner.",
                    "ban",
                )
                conduct_cases.update_one({"_id": case_id}, {"$set": {"notified": True}})
            if existing is None:
                conduct_sanctions.insert_one(
                    {
                        "_id": uuid.uuid4().hex,
                        "guild_id": doc.get("guild_id"),
                        "subject_id": subject_id,
                        "subject_name": subject_name,
                        "type": BAN,
                        "case_id": case_id,
                        "issued_at": now,
                        "expires_at": None,
                        "revoked_at": None,
                        "revoked_by": None,
                        "revoke_reason": None,
                    }
                )
            ban_applied = bool(doc.get("ban_applied"))
            if not ban_applied:
                try:
                    await guild.ban(
                        discord.Object(id=int(subject_id)),
                        reason="Code of conduct ban (Duck Council vote)",
                    )
                    ban_applied = True
                    conduct_cases.update_one(
                        {"_id": case_id}, {"$set": {"ban_applied": True}}
                    )
                except (discord.Forbidden, discord.HTTPException) as e:
                    log.error("Could not apply the conduct ban: %s", e, exc_info=e)
            if ban_applied and not doc.get("public_logged"):
                if await _send_public_log(guild, public_ban_line(subject_name)):
                    conduct_cases.update_one(
                        {"_id": case_id}, {"$set": {"public_logged": True}}
                    )
        else:
            if existing is None:
                conduct_sanctions.insert_one(
                    {
                        "_id": uuid.uuid4().hex,
                        "guild_id": doc.get("guild_id"),
                        "subject_id": subject_id,
                        "subject_name": subject_name,
                        "type": WARN,
                        "case_id": case_id,
                        "issued_at": now,
                        "expires_at": now + WARNING_LIFETIME,
                        "revoked_at": None,
                        "revoked_by": None,
                        "revoke_reason": None,
                    }
                )
            if not doc.get("notified"):
                await _dm(
                    bot,
                    guild,
                    subject_id,
                    "You have been given a warning for breaking the code of "
                    "conduct. This warning is active for 12 months and may "
                    "lead to a ban. If you believe this is a mistake, contact "
                    "a server owner.",
                    "warning",
                )
                conduct_cases.update_one({"_id": case_id}, {"$set": {"notified": True}})
            if not doc.get("public_logged"):
                line = public_warning_line(
                    subject_name, active_warning_count(subject_id, now)
                )
                if await _send_public_log(guild, line):
                    conduct_cases.update_one(
                        {"_id": case_id}, {"$set": {"public_logged": True}}
                    )

    doc = conduct_cases.find_one({"_id": case_id})
    if doc and not doc.get("result_posted") and doc.get("result_lines"):
        if guild is not None:
            channel = guild.get_channel(doc.get("channel_id"))
            if channel is not None:
                try:
                    await channel.send("\n".join(doc["result_lines"]))
                    conduct_cases.update_one(
                        {"_id": case_id}, {"$set": {"result_posted": True}}
                    )
                except (discord.Forbidden, discord.HTTPException) as e:
                    log.error("Could not post the conduct result: %s", e, exc_info=e)


# ---------------------------------------------------------------------------
# Council applications
# ---------------------------------------------------------------------------


async def file_application(bot, interaction, answer: str):
    guild = interaction.guild
    if guild is None:
        raise ConductError("This command can only be used in a server.")
    applicant = interaction.user
    if getattr(applicant, "bot", False):
        raise ConductError("Bots cannot apply to the Duck Council.")
    if not (answer or "").strip():
        raise ConductError("Answer why you should be let into the Duck Council.")
    if (
        discord.utils.get(getattr(applicant, "roles", []), name=COUNCIL_ROLE_NAME)
        is not None
    ):
        raise ConductError("You are already on the Duck Council.")
    if conduct_applications.find_one(
        {"applicant_id": str(applicant.id), "status": APP_OPEN}
    ):
        raise ConductError("You already have an open Duck Council application.")

    council, _, role, role_error = await ensure_setup(bot, guild)
    if role_error:
        raise SetupError(role_error)
    now = utcnow()
    doc = {
        "_id": uuid.uuid4().hex,
        "guild_id": guild.id,
        "applicant_id": str(applicant.id),
        "applicant_name": getattr(applicant, "display_name", None) or applicant.name,
        "answer": answer.strip(),
        "status": APP_OPEN,
        "deadline": now + APPLICATION_WINDOW,
        "roster": council_roster(role),
        "votes": {},
        "created_at": now,
        "channel_id": council.id,
        "message_ids": [],
        "message_id": None,
        "result_lines": [],
        "result_posted": False,
        "notified": False,
        "public_logged": False,
        "role_granted": False,
        "role_grant_error": False,
        "closed_at": None,
        "purge_after": None,
        "purged": False,
    }
    conduct_applications.insert_one(doc)
    from views.conduct_views import ApplicationVoteView

    view = ApplicationVoteView(doc["_id"])
    if hasattr(bot, "add_view"):
        bot.add_view(view)
    try:
        message = await council.send(
            content=f"{role.mention} A Duck Council application is waiting.",
            embed=build_application_embed(doc, now),
            view=view,
        )
    except (discord.Forbidden, discord.HTTPException) as e:
        conduct_applications.delete_one({"_id": doc["_id"]})
        log.error("Could not post the council application: %s", e, exc_info=e)
        raise ConductError("Could not post the application to #duck-council.") from e
    conduct_applications.update_one(
        {"_id": doc["_id"]},
        {"$set": {"message_id": message.id, "message_ids": [message.id]}},
    )
    return conduct_applications.find_one({"_id": doc["_id"]})


def cast_application_vote(app_id: str, voter_id: str, choice: str) -> dict:
    doc = conduct_applications.find_one({"_id": app_id})
    if doc is None or doc.get("status") != APP_OPEN:
        raise ConductError("This application vote is closed.")
    if utcnow() >= as_utc(doc["deadline"]):
        raise ConductError("This application vote is closed.")
    if str(voter_id) not in (doc.get("roster") or []):
        raise ConductError("You were not on the council when this vote opened.")
    result = conduct_applications.update_one(
        {"_id": app_id, f"votes.{voter_id}": {"$exists": False}},
        {"$set": {f"votes.{voter_id}": choice}},
    )
    if not result.modified_count:
        raise ConductError("You have already voted on this application.")
    return conduct_applications.find_one({"_id": app_id})


def should_close_application(doc: dict, now: datetime) -> bool:
    votes = doc.get("votes") or {}
    roster = doc.get("roster") or []
    if any(choice == "no" for choice in votes.values()):
        return True
    if roster and len(votes) >= len(roster):
        return True
    return now >= as_utc(doc["deadline"])


async def close_application(bot, doc: dict, now=None, message=None, interaction=None):
    now = now or utcnow()
    doc = conduct_applications.find_one({"_id": doc["_id"]})
    if doc is None or doc.get("status") != APP_OPEN:
        return False
    if not should_close_application(doc, now):
        return False
    votes = doc.get("votes") or {}
    roster = doc.get("roster") or []
    approved = (
        bool(roster)
        and len(votes) >= len(roster)
        and all(choice == "yes" for choice in votes.values())
    )
    status = APP_APPROVED if approved else APP_REJECTED
    tally = {
        "yes": sum(1 for choice in votes.values() if choice == "yes"),
        "no": sum(1 for choice in votes.values() if choice == "no"),
        "not_voted": max(0, len(roster) - len(votes)),
    }
    conduct_applications.update_one(
        {"_id": doc["_id"]},
        {
            "$set": {
                "status": status,
                "tally": tally,
                "result_lines": [f"Outcome: {'Accepted' if approved else 'Rejected'}"],
                "closed_at": now,
                "purge_after": now + PURGE_AFTER,
            }
        },
    )
    doc = conduct_applications.find_one({"_id": doc["_id"]})
    board = (
        await _application_board_message(bot, doc, message)
        if interaction is None
        else None
    )
    board_ok = await _edit_board(
        board, build_application_embed(doc, now), view=None, interaction=interaction
    )
    await finish_application(bot, doc["_id"], now)
    return board_ok


async def _application_board_message(bot, doc, message=None):
    if message is not None:
        return message
    guild = bot.get_guild(doc.get("guild_id"))
    channel = guild.get_channel(doc.get("channel_id")) if guild else None
    if channel is None or doc.get("message_id") is None:
        return None
    try:
        return await channel.fetch_message(doc["message_id"])
    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
        log.warning("Could not fetch the application board message: %s", e)
        return None


async def finish_application(bot, app_id: str, now=None):
    now = now or utcnow()
    doc = conduct_applications.find_one({"_id": app_id})
    if doc is None or doc.get("status") == APP_OPEN:
        return
    guild = bot.get_guild(doc.get("guild_id"))
    applicant_id = doc.get("applicant_id")
    applicant_name = doc.get("applicant_name") or applicant_id
    if guild is None:
        return

    if doc["status"] == APP_APPROVED:
        if not doc.get("role_granted") and not doc.get("role_grant_error"):
            role = discord.utils.get(guild.roles, name=COUNCIL_ROLE_NAME)
            member = guild.get_member(int(applicant_id))
            if member is None:
                try:
                    member = await guild.fetch_member(int(applicant_id))
                except (discord.NotFound, discord.HTTPException):
                    member = None
            try:
                if role is None or member is None:
                    raise ConductError("role or member missing")
                await member.add_roles(role)
                conduct_applications.update_one(
                    {"_id": app_id}, {"$set": {"role_granted": True}}
                )
                doc = conduct_applications.find_one({"_id": app_id})
            except (discord.Forbidden, discord.HTTPException, ConductError) as e:
                log.error("Could not grant the Duck Council role: %s", e, exc_info=e)
                conduct_applications.update_one(
                    {"_id": app_id}, {"$set": {"role_grant_error": True}}
                )
                owner_role = discord.utils.get(guild.roles, name=OWNER_ROLE_NAME)
                channel = guild.get_channel(doc.get("channel_id"))
                notice = (
                    "I approved a Duck Council application but could not grant "
                    "the role. Please grant it manually."
                )
                if owner_role is not None:
                    notice = f"{owner_role.mention} {notice}"
                if channel is not None:
                    try:
                        await channel.send(notice)
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                doc = conduct_applications.find_one({"_id": app_id})

        if doc.get("role_granted"):
            if not doc.get("notified"):
                await _dm(
                    bot,
                    guild,
                    applicant_id,
                    "You have been accepted into the Duck Council.",
                    "application accepted",
                )
                conduct_applications.update_one(
                    {"_id": app_id}, {"$set": {"notified": True}}
                )
            if not doc.get("public_logged"):
                if await _send_public_log(guild, public_accept_line(applicant_name)):
                    conduct_applications.update_one(
                        {"_id": app_id}, {"$set": {"public_logged": True}}
                    )
        elif not doc.get("notified"):
            await _dm(
                bot,
                guild,
                applicant_id,
                "Your Duck Council application was approved, but I could not "
                "grant the role. An owner has been notified.",
                "application accepted but role grant failed",
            )
            conduct_applications.update_one(
                {"_id": app_id}, {"$set": {"notified": True}}
            )
    else:
        if not doc.get("notified"):
            await _dm(
                bot,
                guild,
                applicant_id,
                "Your Duck Council application was rejected. You may apply "
                "again at any time.",
                "application rejected",
            )
            conduct_applications.update_one(
                {"_id": app_id}, {"$set": {"notified": True}}
            )

    doc = conduct_applications.find_one({"_id": app_id})
    if doc and not doc.get("result_posted"):
        if doc["status"] != APP_REJECTED:
            # Accepted applications are announced in #announcements only.
            conduct_applications.update_one(
                {"_id": app_id}, {"$set": {"result_posted": True}}
            )
            return
        channel = guild.get_channel(doc.get("channel_id"))
        if channel is not None:
            content = "\n".join(
                [application_result_line(doc)] + list(doc.get("result_lines") or [])
            )
            try:
                await channel.send(content)
                conduct_applications.update_one(
                    {"_id": app_id}, {"$set": {"result_posted": True}}
                )
            except (discord.Forbidden, discord.HTTPException) as e:
                log.error("Could not post the application result: %s", e, exc_info=e)


def revoke_warning(
    sanction_id: str, owner_id: str, reason: str | None, now=None
) -> dict:
    now = now or utcnow()
    doc = conduct_sanctions.find_one({"_id": sanction_id})
    if doc is None or doc.get("type") != WARN:
        raise ConductError("That warning no longer exists.")
    if doc.get("revoked_at"):
        raise ConductError("That warning has already been revoked.")
    conduct_sanctions.update_one(
        {"_id": sanction_id},
        {
            "$set": {
                "revoked_at": now,
                "revoked_by": str(owner_id),
                "revoke_reason": (reason or "").strip() or None,
            }
        },
    )
    return conduct_sanctions.find_one({"_id": sanction_id})


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def register_conduct_views(bot) -> None:
    """Register a persistent view for every open case and application."""
    from views.conduct_views import ApplicationVoteView, ConductVoteView

    for doc in conduct_cases.find({"status": {"$in": [BAN, WARN]}}):
        bot.add_view(ConductVoteView(doc["_id"], doc.get("status") or BAN))
    for doc in conduct_applications.find({"status": APP_OPEN}):
        bot.add_view(ApplicationVoteView(doc["_id"]))


async def purge_expired(bot, now: datetime | None = None) -> None:
    now = now or utcnow()
    for collection, text_fields, extra_fields, status_filter in (
        (
            conduct_cases,
            ("description", "rule", "approx_time"),
            ("links", "evidence_files"),
            {"status": CLOSED},
        ),
        (
            conduct_applications,
            ("answer",),
            (),
            {"status": {"$in": [APP_APPROVED, APP_REJECTED]}},
        ),
    ):
        for doc in list(collection.find(status_filter)):
            if doc.get("purged") or not doc.get("purge_after"):
                continue
            if as_utc(doc["purge_after"]) > now:
                continue
            guild = bot.get_guild(doc.get("guild_id"))
            channel = guild.get_channel(doc.get("channel_id")) if guild else None
            deleted_ok = True
            for message_id in doc.get("message_ids") or []:
                if channel is None:
                    # The channel is gone, so its messages are gone too.
                    continue
                try:
                    message = await channel.fetch_message(message_id)
                    await message.delete()
                except discord.NotFound:
                    pass
                except (discord.Forbidden, discord.HTTPException) as e:
                    deleted_ok = False
                    log.error(
                        "Could not purge a conduct message; will retry: %s",
                        e,
                        exc_info=e,
                    )
            update = {field: None for field in text_fields}
            update.update(
                {field: [] if field == "links" else 0 for field in extra_fields}
            )
            update.update({"votes": {}, "roster": [], "purged": deleted_ok})
            collection.update_one({"_id": doc["_id"]}, {"$set": update})


async def conduct_tick(bot, now: datetime | None = None) -> None:
    """Close due phases and run the 90-day purge; safe to call at any time."""
    now = now or utcnow()
    for doc in list(conduct_cases.find({"status": {"$in": [BAN, WARN]}})):
        if (
            doc.get("status") == WARN
            and not doc.get("warn_board_posted", True)
            and not should_close(doc, WARN, now)
        ):
            try:
                await _post_warn_board(bot, doc, now)
            except Exception as e:
                log.error("Conduct warn board recovery failed: %r", e, exc_info=e)
        if should_close(doc, doc["status"], now):
            try:
                await close_phase(bot, doc, doc["status"], now)
            except Exception as e:  # keep the sweep alive
                log.error("Conduct phase close failed: %r", e, exc_info=e)
    for doc in list(conduct_cases.find({"status": CLOSED})):
        try:
            await finish_case(bot, doc["_id"], now)
        except Exception as e:
            log.error("Conduct case finish failed: %r", e, exc_info=e)
    for doc in list(conduct_applications.find({"status": APP_OPEN})):
        if should_close_application(doc, now):
            try:
                await close_application(bot, doc, now)
            except Exception as e:
                log.error("Application close failed: %r", e, exc_info=e)
    for doc in list(conduct_applications.find({"status": {"$ne": APP_OPEN}})):
        try:
            await finish_application(bot, doc["_id"], now)
        except Exception as e:
            log.error("Application finish failed: %r", e, exc_info=e)
    await purge_expired(bot, now)


async def recover_conduct(bot) -> None:
    """Startup tail: close expired votes and post anything that was missed."""
    try:
        await conduct_tick(bot)
    except Exception as e:
        log.error("Conduct startup recovery failed: %r", e, exc_info=e)


async def conduct_loop(bot) -> None:
    while True:
        await asyncio.sleep(TICK_SECONDS)
        try:
            await conduct_tick(bot)
        except Exception as e:
            log.error("Conduct tick failed: %r", e, exc_info=e)
