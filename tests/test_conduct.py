"""Self-checks for conduct reporting, council votes, and applications (#269).

Run with: python tests/test_conduct.py
"""

import asyncio
import io
import logging
import os
import sys
import types
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord  # noqa: E402  (real library: only Discord objects are faked)

# ---------------------------------------------------------------------------
# In-memory Mongo stand-in
# ---------------------------------------------------------------------------

_MISSING = object()


def _path_get(doc, path):
    current = doc
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _path_set(doc, path, value):
    parts = path.split(".")
    current = doc
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def _matches(doc, query):
    for key, expected in query.items():
        actual = _path_get(doc, key)
        if isinstance(expected, dict):
            for op, value in expected.items():
                if op == "$exists":
                    if (actual is not _MISSING) != value:
                        return False
                elif op == "$in":
                    if actual is _MISSING or actual not in value:
                        return False
                elif op == "$ne":
                    if actual is not _MISSING and actual == value:
                        return False
                elif op == "$gte":
                    if actual is _MISSING or actual < value:
                        return False
                elif op == "$lte":
                    if actual is _MISSING or actual > value:
                        return False
                else:  # pragma: no cover - unsupported operator
                    raise AssertionError(op)
        elif actual is _MISSING or actual != expected:
            return False
    return True


class FakeCollection:
    def __init__(self):
        self.docs = {}
        self.insert_calls = 0

    def insert_one(self, doc):
        self.insert_calls += 1
        self.docs[doc["_id"]] = doc

    def find_one(self, query):
        for doc in self.docs.values():
            if _matches(doc, query):
                return doc
        return None

    def find(self, query=None):
        return [doc for doc in self.docs.values() if _matches(doc, query or {})]

    def update_one(self, query, update, upsert=False):
        for doc in self.docs.values():
            if not _matches(doc, query):
                continue
            for path, value in (update.get("$set") or {}).items():
                _path_set(doc, path, value)
            for path, value in (update.get("$push") or {}).items():
                existing = _path_get(doc, path)
                if existing is _MISSING or existing is None:
                    _path_set(doc, path, [])
                    existing = _path_get(doc, path)
                existing.append(value)
            return types.SimpleNamespace(matched_count=1, modified_count=1)
        return types.SimpleNamespace(matched_count=0, modified_count=0)

    def delete_one(self, query):
        for doc_id, doc in list(self.docs.items()):
            if _matches(doc, query):
                del self.docs[doc_id]
                return types.SimpleNamespace(deleted_count=1)
        return types.SimpleNamespace(deleted_count=0)

    def latest(self):
        return list(self.docs.values())[-1] if self.docs else None


CASES = FakeCollection()
SANCTIONS = FakeCollection()
APPLICATIONS = FakeCollection()

_database_stub = types.ModuleType("database")
_database_stub.users = types.SimpleNamespace(find_one=lambda *a, **k: None)
_database_stub.mmr_collection = types.SimpleNamespace(find=lambda *a, **k: [])
_database_stub.seasons = types.SimpleNamespace(update_one=lambda *a, **k: None)
_database_stub.all_matches = types.SimpleNamespace()
_database_stub.recent_queue = types.SimpleNamespace()
_database_stub.coin_escrow = types.SimpleNamespace()
_database_stub.conduct_cases = CASES
_database_stub.conduct_sanctions = SANCTIONS
_database_stub.conduct_applications = APPLICATIONS
sys.modules["database"] = _database_stub

import game.conduct as conduct  # noqa: E402
from game.conduct import (  # noqa: E402
    APP_APPROVED,
    APP_OPEN,
    APP_REJECTED,
    BAN,
    CLOSED,
    WARN,
)
from views.conduct_views import (  # noqa: E402
    ApplicationModal,
    ApplicationVoteView,
    ConductVoteView,
    ReportModal,
    UnwarnSelectView,
)

# ---------------------------------------------------------------------------
# Fake Discord objects
# ---------------------------------------------------------------------------

_next_id = [1000]


def next_id():
    _next_id[0] += 1
    return _next_id[0]


class FakePerms:
    view_channel = True
    send_messages = True


class FakeRole:
    def __init__(self, role_id, name, position=0, members=None, mentionable=False):
        self.id = role_id
        self.name = name
        self.position = position
        self.members = list(members or [])
        self.mentionable = mentionable
        self.mention = f"<@&{role_id}>"


class FakeMember:
    def __init__(self, member_id, name, roles=None, bot=False, top_role=None):
        self.id = int(member_id)
        self.name = name
        self.display_name = name
        self.roles = list(roles or [])
        self.bot = bot
        self.top_role = top_role
        self.dms = []
        self.fail_add_roles = False
        self.fail_dms = False

    async def send(self, content=None, **kwargs):
        if self.fail_dms:
            raise discord.Forbidden(
                types.SimpleNamespace(status=403, reason="Forbidden"), "missing"
            )
        self.dms.append(content)

    async def add_roles(self, role):
        if self.fail_add_roles:
            raise discord.Forbidden(
                types.SimpleNamespace(status=403, reason="Forbidden"), "missing"
            )
        self.roles.append(role)


class FakeMessage:
    def __init__(self, message_id, channel, content=None, **kwargs):
        self.id = message_id
        self.channel = channel
        self.content = content
        self.embed = kwargs.get("embed")
        self.view = kwargs.get("view")
        self.files = kwargs.get("files") or []
        self.edits = []
        self.deleted = False

    async def edit(self, **kwargs):
        self.edits.append(kwargs)
        if "embed" in kwargs:
            self.embed = kwargs["embed"]
        self.view = kwargs.get("view", self.view)

    async def delete(self):
        self.deleted = True


class FakeChannel:
    def __init__(self, channel_id, name):
        self.id = channel_id
        self.name = name
        self.sent = []
        self.messages = {}
        self.created_overwrites = None
        self.fail_next_send = False

    async def send(self, content=None, **kwargs):
        if self.fail_next_send:
            self.fail_next_send = False
            raise discord.HTTPException(
                types.SimpleNamespace(status=500, reason="Server Error"), "send failed"
            )
        message = FakeMessage(next_id(), self, content, **kwargs)
        self.messages[message.id] = message
        self.sent.append((content, kwargs))
        return message

    async def fetch_message(self, message_id):
        return self.messages[message_id]

    def permissions_for(self, member):
        return FakePerms()


class FakeGuild:
    def __init__(self, roster_size=5, with_setup=True):
        self.id = 100
        self.name = "Test Guild"
        self.members = {}
        self.bans = []
        self.dm_count_at_ban = []
        self.role_creates = []
        self.channel_creates = []
        self.default_role = FakeRole(1, "@everyone", 0)
        self.owner_role = FakeRole(2, "Owner", 20)
        self.owner = FakeMember(90, "Owner", [self.owner_role])
        self.me = FakeMember(99, "Bot", top_role=FakeRole(4, "Bot", 50))
        self.members[self.owner.id] = self.owner
        self.members[self.me.id] = self.me

        self.council_role = FakeRole(3, "Duck Council", 10)
        self.council_members = []
        for index in range(roster_size):
            member = FakeMember(10 + index, f"Council{index}", [self.council_role])
            self.members[member.id] = member
            self.council_role.members.append(member)
            self.council_members.append(member)

        self.subject = FakeMember(20, "Reported Player")
        self.members[self.subject.id] = self.subject
        self.reporter = FakeMember(30, "Reporter")
        self.members[self.reporter.id] = self.reporter

        self.roles = [self.default_role, self.owner_role, self.me.top_role]
        if with_setup:
            self.roles.insert(1, self.council_role)
        self.text_channels = []
        self.council = None
        self.log_channel = None
        if with_setup:
            self.council = FakeChannel(200, "duck-council")
            self.log_channel = FakeChannel(201, "council-log")
            self.text_channels = [self.council, self.log_channel]

    def get_member(self, member_id):
        return self.members.get(int(member_id))

    def get_channel(self, channel_id):
        for channel in self.text_channels:
            if channel.id == channel_id:
                return channel
        return None

    async def fetch_member(self, member_id):
        member = self.get_member(member_id)
        if member is None:
            raise discord.NotFound(
                types.SimpleNamespace(status=404, reason="Not Found"), "missing"
            )
        return member

    async def create_role(self, name, mentionable=False, permissions=None, reason=None):
        role = FakeRole(
            next_id(), name, position=len(self.roles), mentionable=mentionable
        )
        self.role_creates.append((name, mentionable, permissions))
        self.roles.append(role)
        return role

    async def create_text_channel(self, name, overwrites=None, reason=None):
        channel = FakeChannel(next_id(), name)
        channel.created_overwrites = overwrites
        self.channel_creates.append((name, overwrites))
        self.text_channels.append(channel)
        if name == "duck-council":
            self.council = channel
        elif name == "council-log":
            self.log_channel = channel
        return channel

    async def ban(self, user, reason=None):
        self.bans.append((user.id, reason))
        self.dm_count_at_ban.append(len(self.subject.dms))


class FakeBot:
    def __init__(self, guild):
        self.guilds = [guild]
        self.views = []
        self.users = {}

    def get_guild(self, guild_id):
        return self.guilds[0] if guild_id == self.guilds[0].id else None

    def get_user(self, user_id):
        return self.users.get(int(user_id))

    async def fetch_user(self, user_id):
        return self.users.get(int(user_id))

    def add_view(self, view):
        self.views.append(view)


class FakeResponse:
    def __init__(self):
        self.done = False
        self.messages = []
        self.modal = None
        self.deferred = None

    def is_done(self):
        return self.done

    async def send_message(self, content=None, **kwargs):
        self.messages.append((content, kwargs))
        self.done = True

    async def send_modal(self, modal):
        self.modal = modal
        self.done = True

    async def defer(self, **kwargs):
        self.done = True
        self.deferred = kwargs


class FakeFollowup:
    def __init__(self):
        self.messages = []

    async def send(self, content=None, **kwargs):
        self.messages.append((content, kwargs))


class FakeInteraction:
    def __init__(self, bot, user, guild, channel=None, message=None):
        self.client = bot
        self.user = user
        self.guild = guild
        self.channel = channel or (guild.council if guild else None)
        self.message = message
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.edited = None

    async def edit_original_response(self, **kwargs):
        self.edited = kwargs
        if self.message is not None:
            await self.message.edit(**kwargs)


class Harness:
    def __init__(self, roster_size=5, with_setup=True):
        CASES.docs.clear()
        SANCTIONS.docs.clear()
        APPLICATIONS.docs.clear()
        self.guild = FakeGuild(roster_size=roster_size, with_setup=with_setup)
        self.bot = FakeBot(self.guild)
        self.council = self.guild.council
        self.log = self.guild.log_channel
        self.reporter = self.guild.reporter

    def interaction(self, user, message=None, channel=None):
        return FakeInteraction(
            self.bot, user, self.guild, channel or self.council, message
        )

    def owner(self):
        return self.guild.owner

    def council_member(self, index=0):
        return self.guild.council_members[index]


async def submit_report(
    harness,
    reporter,
    subject="default",
    rule="Be nice",
    description="They were mean. https://example.com/evidence",
    approx_time="last night",
    files=None,
):
    if subject == "default":
        subject = harness.guild.subject
    modal = ReportModal()
    modal.subject._values = [subject] if subject is not None else []
    modal.rule._value = rule
    modal.description._value = description
    modal.approx_time._value = approx_time
    modal.evidence_files._values = list(files or [])
    interaction = harness.interaction(reporter)
    await modal.on_submit(interaction)
    return interaction


async def submit_application(harness, applicant, answer="I am helpful"):
    modal = ApplicationModal()
    modal.answer._value = answer
    interaction = harness.interaction(applicant)
    await modal.on_submit(interaction)
    return interaction


async def vote(harness, voter, choice, phase=None):
    case = CASES.latest()
    phase = phase or case["status"]
    message = harness.council.messages[case["message_id"]]
    view = ConductVoteView(case["_id"], phase)
    button = view.children[0] if choice == "yes" else view.children[1]
    interaction = harness.interaction(voter, message=message)
    await button.callback(interaction)
    return interaction


async def apply_vote(harness, voter, choice):
    doc = APPLICATIONS.latest()
    message = harness.council.messages[doc["message_id"]]
    view = ApplicationVoteView(doc["_id"])
    button = view.children[0] if choice == "yes" else view.children[1]
    interaction = harness.interaction(voter, message=message)
    await button.callback(interaction)
    return interaction


def embed_text(embed):
    fields = "\n".join(f"{f.name}: {f.value}" for f in embed.fields)
    return f"{embed.title}\n{embed.description or ''}\n{fields}"


async def demo():
    # ------------------------------------------------------------------
    # Setup: missing role and channels are created with the right perms.
    # ------------------------------------------------------------------
    harness = Harness(with_setup=False)
    guild = harness.guild
    council, log_channel, role, role_error = await conduct.ensure_setup(
        harness.bot, guild
    )
    assert role.name == "Duck Council" and role.mentionable
    assert role_error is None, role_error
    assert [name for name, _ in guild.channel_creates] == [
        "duck-council",
        "council-log",
    ]
    assert guild.owner.dms, "owner was not DMed about the created role"
    council_overwrites = council.created_overwrites
    assert (
        council_overwrites[guild.default_role].view_channel is False
    ), "duck-council must be private"
    assert (
        council_overwrites[role].send_messages is True
        and council_overwrites[role].attach_files is True
    ), "council role needs send/attach permissions"
    log_overwrites = log_channel.created_overwrites
    assert log_overwrites[guild.default_role].view_channel is True
    assert log_overwrites[guild.default_role].send_messages is False
    # Existing objects are verified, not rewritten.
    before = len(guild.channel_creates)
    await conduct.ensure_setup(harness.bot, guild)
    assert len(guild.channel_creates) == before, "existing channels were rewritten"

    # A council role at/above the bot's top role refuses applications.
    harness = Harness()
    harness.guild.council_role.position = 50
    interaction = await submit_application(harness, harness.guild.subject, "let me in")
    assert interaction.followup.messages, "application was not rejected"
    assert "above my highest role" in interaction.followup.messages[0][0]
    assert not APPLICATIONS.docs

    # Reports still run when only role granting is blocked.
    interaction = await submit_report(
        harness, harness.guild.owner, subject=harness.guild.subject
    )
    assert CASES.docs, "report should not depend on role position"
    assert "filed anonymously" in interaction.followup.messages[0][0]

    # ------------------------------------------------------------------
    # Report validation: every bad input is rejected with no case created.
    # ------------------------------------------------------------------
    harness = Harness()
    reporter = harness.reporter  # a normal member
    assert (await submit_report(harness, reporter, subject=None)).followup.messages
    assert not CASES.docs
    assert (
        await submit_report(harness, reporter, subject=reporter)
    ).followup.messages, "self-report was accepted"
    assert not CASES.docs

    bot_member = FakeMember(77, "SomeBot", bot=True)
    harness.guild.members[bot_member.id] = bot_member
    assert (
        await submit_report(harness, reporter, subject=bot_member)
    ).followup.messages, "bot report was accepted"
    assert not CASES.docs

    council_target = harness.council_member()
    assert (
        await submit_report(harness, reporter, subject=council_target)
    ).followup.messages, "council member report was accepted"
    assert not CASES.docs

    assert (
        await submit_report(harness, reporter, subject=harness.guild.owner)
    ).followup.messages, "owner report was accepted"
    assert not CASES.docs

    assert (
        await submit_report(harness, reporter, rule="")
    ).followup.messages, "empty rule was accepted"
    assert not CASES.docs
    assert (
        await submit_report(harness, reporter, description="")
    ).followup.messages, "empty description was accepted"
    assert not CASES.docs
    assert (
        await submit_report(harness, reporter, description="no evidence here", files=[])
    ).followup.messages, "report without evidence was accepted"
    assert not CASES.docs

    class BrokenAttachment:
        async def to_file(self):
            raise OSError("download failed")

    broken = await submit_report(
        harness,
        reporter,
        description="no link here",
        files=[BrokenAttachment()],
    )
    assert (
        "Could not re-upload" in broken.followup.messages[0][0]
    ), "failed evidence upload was silently accepted"
    assert not CASES.docs

    # A non-member cannot be reported (UserSelect can resolve a User).
    outsider = FakeMember(88, "Outsider", roles=None)
    del outsider.roles
    assert (
        await submit_report(harness, reporter, subject=outsider)
    ).followup.messages, "report against a non-member was accepted"
    assert not CASES.docs

    # ------------------------------------------------------------------
    # Valid report: case posted to #duck-council, role pinged, history shown,
    # evidence re-uploaded, and no reporter identity stored or logged.
    # ------------------------------------------------------------------
    harness = Harness()
    reporter = harness.reporter
    CASES.docs.clear()
    SANCTIONS.docs.clear()
    # An active and an expired warning for the subject.
    now = conduct.utcnow()
    SANCTIONS.insert_one(
        {
            "_id": "warn-active",
            "subject_id": str(harness.guild.subject.id),
            "type": WARN,
            "issued_at": now - timedelta(days=30),
            "expires_at": now + timedelta(days=300),
            "revoked_at": None,
        }
    )
    SANCTIONS.insert_one(
        {
            "_id": "warn-expired",
            "subject_id": str(harness.guild.subject.id),
            "type": WARN,
            "issued_at": now - timedelta(days=500),
            "expires_at": now - timedelta(days=100),
            "revoked_at": None,
        }
    )

    class FakeAttachment:
        async def to_file(self):
            return discord.File(io.BytesIO(b"evidence"), filename="evidence.png")

    log_records = []

    class Capture(logging.Handler):
        def emit(self, record):
            log_records.append(record.getMessage())

    logger = logging.getLogger("game.conduct")
    handler = Capture()
    logger.addHandler(handler)
    try:
        interaction = await submit_report(
            harness,
            reporter,
            subject=harness.guild.subject,
            files=[FakeAttachment()],
        )
    finally:
        logger.removeHandler(handler)
    assert "filed anonymously" in interaction.followup.messages[0][0]
    case = CASES.latest()
    assert case["status"] == BAN and case["roster"]
    assert case["prior_warning"] is True
    assert case["links"] == ["https://example.com/evidence"]
    assert case["evidence_files"] == 1
    content, kwargs = harness.council.sent[0]
    assert harness.guild.council_role.mention in content, "council role was not pinged"
    embed = kwargs["embed"]
    text = embed_text(embed)
    assert "Reported Player" in text
    assert "Active warnings: 1" in text
    assert "Expired warnings (inactive): 1" in text
    assert "Prior bans: 0" in text
    assert "https://example.com/evidence" in text
    assert reporter.name not in text, "reporter name leaked into the case embed"
    assert str(reporter.id) not in text, "reporter id leaked into the case embed"
    assert kwargs["files"], "uploaded evidence was not re-uploaded"
    assert reporter.name not in repr(case), "reporter name leaked into the case"
    assert all(
        value != str(reporter.id) and value != reporter.id for value in case.values()
    ), "reporter id leaked into the case"
    assert reporter.name not in "\n".join(
        log_records
    ), "reporter identity leaked into logs"
    assert harness.bot.views, "the case view was not registered"

    # ------------------------------------------------------------------
    # Ban phase: a majority yes bans, DMs before banning, and logs publicly.
    # ------------------------------------------------------------------
    harness = Harness()
    await submit_report(harness, harness.reporter)
    case = CASES.latest()
    for index in range(3):
        await vote(harness, harness.council_member(index), "yes")
    case = CASES.latest()
    assert case["status"] == CLOSED and case["outcome"] == BAN
    assert harness.guild.bans and harness.guild.bans[0][0] == harness.guild.subject.id
    assert harness.guild.dm_count_at_ban[0] >= 1, "ban DM was not sent before the ban"
    assert harness.guild.subject.dms[0].startswith("You have been banned")
    log_text = "\n".join(content or "" for content, _ in harness.log.sent)
    assert log_text == conduct.public_ban_line("Reported Player")
    assert not any(
        "Warn vote" in (content or "") for content, _ in harness.council.sent
    ), "a warn phase opened after a passed ban"
    result_text = "\n".join(
        content
        for content, _ in harness.council.sent
        if content and "Outcome:" in content
    )
    assert "Ban: 3 to 0 — passed" in result_text
    assert "Outcome: Banned" in result_text
    assert not any(
        harness.council_member(i).name in result_text for i in range(3)
    ), "voter names leaked into the result"

    # ------------------------------------------------------------------
    # Ban phase: majority no / tie / quorum failure all open the Warn phase.
    # ------------------------------------------------------------------
    async def open_warn_with(votes, roster_size=5):
        harness = Harness(roster_size=roster_size)
        await submit_report(harness, harness.reporter)
        for index, choice in enumerate(votes[:roster_size]):
            await vote(harness, harness.council_member(index), choice)
        # Advance to (or past) the ban deadline.
        case = CASES.latest()
        if case["status"] == BAN:
            case["deadline"] = conduct.utcnow() - timedelta(seconds=1)
            await conduct.conduct_tick(harness.bot)
        return harness, CASES.latest()

    harness, case = await open_warn_with(["no", "no", "yes", "yes", "no"])
    assert case["status"] == WARN, case["status"]
    assert case["deadline"] > conduct.utcnow(), "warn phase did not get a new clock"
    warn_board = harness.council.messages[case["message_id"]]
    assert "Warn vote" in embed_text(warn_board.embed)
    assert any(
        "Warn vote is now open" in (content or "")
        for content, _ in harness.council.sent
    ), "warn phase did not ping the council"
    assert case["ban_result"]["passed"] is False

    harness, case = await open_warn_with(["yes", "yes", "no", "no"])
    assert case["status"] == WARN, "ban tie did not open the warn phase"

    # A partial, still-changeable tally must not close early.
    harness = Harness()
    await submit_report(harness, harness.reporter)
    for index, choice in enumerate(["yes", "yes", "no", "no"]):
        await vote(harness, harness.council_member(index), choice)
    assert (
        CASES.latest()["status"] == BAN
    ), "ban phase closed on a partial, changeable tally"

    harness, case = await open_warn_with(["yes"], roster_size=4)
    assert case["status"] == WARN, "ban quorum failure did not open the warn phase"

    # The +1 applies to the ban tally, is shown separately, is capped at one,
    # and cannot satisfy quorum.
    harness = Harness(roster_size=4)
    now = conduct.utcnow()
    for sanction_id in ("prior-warn", "prior-warn-2"):
        SANCTIONS.insert_one(
            {
                "_id": sanction_id,
                "subject_id": str(harness.guild.subject.id),
                "type": WARN,
                "issued_at": now - timedelta(days=10),
                "expires_at": now + timedelta(days=300),
                "revoked_at": None,
            }
        )
    await submit_report(harness, harness.reporter)
    case = CASES.latest()
    assert case["prior_warning"] is True
    assert "+ 1 prior warning" in embed_text(
        harness.council.messages[case["message_id"]].embed
    )
    await vote(harness, harness.council_member(0), "yes")
    case["deadline"] = conduct.utcnow() - timedelta(seconds=1)
    await conduct.conduct_tick(harness.bot)
    case = CASES.latest()
    assert case["status"] == WARN, "+1 wrongly satisfied quorum"
    assert case["ban_result"]["yes"] == 1
    assert case["ban_result"]["prior"] == 1, "prior-warning weight was not capped"

    # A prior warning cannot form a quorum on an empty council.
    harness = Harness(roster_size=0)
    SANCTIONS.insert_one(
        {
            "_id": "empty-council-warn",
            "subject_id": str(harness.guild.subject.id),
            "type": WARN,
            "issued_at": now - timedelta(days=1),
            "expires_at": now + timedelta(days=300),
            "revoked_at": None,
        }
    )
    await submit_report(harness, harness.reporter)
    case = CASES.latest()
    assert case["prior_warning"] is True
    case["deadline"] = conduct.utcnow() - timedelta(seconds=1)
    await conduct.conduct_tick(harness.bot)
    case = CASES.latest()
    assert case["status"] == WARN, "+1 alone passed a ban with no human voters"
    case["deadline"] = conduct.utcnow() - timedelta(seconds=1)
    await conduct.conduct_tick(harness.bot)
    assert CASES.latest()["outcome"] is None

    # An expired warning does not add the +1.
    harness = Harness()
    SANCTIONS.insert_one(
        {
            "_id": "old-warn",
            "subject_id": str(harness.guild.subject.id),
            "type": WARN,
            "issued_at": now - timedelta(days=400),
            "expires_at": now - timedelta(days=30),
            "revoked_at": None,
        }
    )
    await submit_report(harness, harness.reporter)
    assert CASES.latest()["prior_warning"] is False

    # ------------------------------------------------------------------
    # Warn phase: majority yes and a tie warn; majority no and quorum
    # failure produce no sanction, no DM, and no public log.
    # ------------------------------------------------------------------
    async def reach_warn(roster_size=5):
        # Force the Ban phase to its deadline with no votes so the Warn phase
        # opens; this keeps the vote sequence below purely about Warn.
        harness = Harness(roster_size=roster_size)
        await submit_report(harness, harness.reporter)
        case = CASES.latest()
        case["deadline"] = conduct.utcnow() - timedelta(seconds=1)
        await conduct.conduct_tick(harness.bot)
        case = CASES.latest()
        assert case["status"] == WARN, case["status"]
        return harness

    harness = await reach_warn()
    for index in range(3):
        await vote(harness, harness.council_member(index), "yes")
    case = CASES.latest()
    assert case["status"] == CLOSED and case["outcome"] == WARN
    assert harness.guild.subject.dms[-1].startswith("You have been given a warning")
    assert "contact a server owner" in harness.guild.subject.dms[-1]
    assert conduct.public_warning_line("Reported Player", 1) in [
        content for content, _ in harness.log.sent
    ]

    harness = await reach_warn()
    harness.guild.subject.dms.clear()
    harness.log.sent.clear()
    for index, choice in enumerate(["yes", "yes", "no", "no"]):
        await vote(harness, harness.council_member(index), choice)
    case = CASES.latest()
    assert case["status"] == WARN, "warn phase closed before the deadline"
    case["deadline"] = conduct.utcnow() - timedelta(seconds=1)
    await conduct.conduct_tick(harness.bot)
    case = CASES.latest()
    assert case["status"] == CLOSED and case["outcome"] == WARN, "warn tie did not warn"
    assert "Warn: 2 to 2 — passed" in "\n".join(case["result_lines"])

    harness = await reach_warn()
    harness.guild.subject.dms.clear()
    harness.log.sent.clear()
    for index, choice in enumerate(["no", "no", "no", "yes"]):
        await vote(harness, harness.council_member(index), choice)
    case = CASES.latest()
    case["deadline"] = conduct.utcnow() - timedelta(seconds=1)
    await conduct.conduct_tick(harness.bot)
    case = CASES.latest()
    assert case["status"] == CLOSED and case["outcome"] is None
    assert not harness.guild.subject.dms, "no-sanction outcome sent a DM"
    assert not harness.log.sent, "no-sanction outcome was publicly logged"
    assert "Outcome: No sanction" in "\n".join(case["result_lines"])
    result_text = "\n".join(
        content
        for content, _ in harness.council.sent
        if content and "Outcome:" in content
    )
    assert "Ban:" in result_text and "Warn:" in result_text

    harness = await reach_warn(roster_size=4)
    harness.guild.subject.dms.clear()
    harness.log.sent.clear()
    # Ban needed a quorum fail; now one yes in a 4-person warn roster.
    await vote(harness, harness.council_member(0), "yes")
    case = CASES.latest()
    case["deadline"] = conduct.utcnow() - timedelta(seconds=1)
    await conduct.conduct_tick(harness.bot)
    case = CASES.latest()
    assert case["status"] == CLOSED and case["outcome"] is None
    assert case["warn_result"]["quorum_met"] is False

    # ------------------------------------------------------------------
    # One vote per member per phase; voter names never appear on the board.
    # ------------------------------------------------------------------
    harness = Harness()
    await submit_report(harness, harness.reporter)
    first = await vote(harness, harness.council_member(0), "yes")
    assert not first.followup.messages or "already" not in first.followup.messages[0][0]
    second = await vote(harness, harness.council_member(0), "no")
    assert "already voted" in second.followup.messages[0][0]
    case = CASES.latest()
    assert case["votes"][BAN] == {str(harness.council_member(0).id): "yes"}
    board = harness.council.messages[case["message_id"]]
    assert harness.council_member(0).name not in embed_text(board.embed)

    # Votes after the deadline are rejected, not counted.
    harness = Harness()
    await submit_report(harness, harness.reporter)
    case = CASES.latest()
    case["deadline"] = conduct.utcnow() - timedelta(seconds=1)
    late = await vote(harness, harness.council_member(0), "yes")
    assert "closed" in late.followup.messages[0][0]
    assert case["votes"][BAN] == {}

    # The roster is snapshotted: mid-phase role changes do not alter quorum,
    # the removed member stays eligible, and a later joiner cannot vote.
    harness = Harness()
    await submit_report(harness, harness.reporter)
    case = CASES.latest()
    snapshot = list(case["roster"])
    removed = harness.council_member(0)
    harness.guild.council_role.members.remove(removed)
    joiner = FakeMember(40, "LateJoiner", [harness.guild.council_role])
    harness.guild.members[joiner.id] = joiner
    harness.guild.council_role.members.append(joiner)
    assert CASES.latest()["roster"] == snapshot, "mid-phase role change altered roster"
    await vote(harness, removed, "yes")
    late = await vote(harness, joiner, "yes")
    assert "not on the council" in late.followup.messages[0][0]

    # A failed sanction DM is logged but does not block the outcome.
    harness = Harness()
    await submit_report(harness, harness.reporter)
    harness.guild.subject.fail_dms = True
    case = CASES.latest()
    case["deadline"] = conduct.utcnow() - timedelta(seconds=1)
    await conduct.conduct_tick(harness.bot)
    for index in range(3):
        await vote(harness, harness.council_member(index), "yes")
    case = CASES.latest()
    assert case["status"] == CLOSED and case["outcome"] == WARN
    assert harness.log.sent, "failed DM blocked the public log"

    # A subject who left the guild is still banned by Discord ID.
    harness = Harness()
    await submit_report(harness, harness.reporter)
    harness.guild.members.pop(harness.guild.subject.id)
    harness.bot.users[harness.guild.subject.id] = harness.guild.subject
    for index in range(3):
        await vote(harness, harness.council_member(index), "yes")
    assert harness.guild.bans and harness.guild.bans[0][0] == harness.guild.subject.id

    # ------------------------------------------------------------------
    # Applications: validation, one open per person, rejection and approval.
    # ------------------------------------------------------------------
    harness = Harness()
    applicant = harness.guild.subject

    interaction = await submit_application(harness, applicant, answer="  ")
    assert interaction.followup.messages, "empty application was accepted"
    assert not APPLICATIONS.docs

    interaction = await submit_application(harness, harness.council_member(0))
    assert interaction.followup.messages, "council member application was accepted"
    assert not APPLICATIONS.docs

    bot_applicant = FakeMember(78, "ApplyBot", bot=True)
    interaction = await submit_application(harness, bot_applicant)
    assert interaction.followup.messages, "bot application was accepted"
    assert not APPLICATIONS.docs

    interaction = await submit_application(harness, applicant)
    assert "has been submitted" in interaction.followup.messages[0][0]
    app = APPLICATIONS.latest()
    assert app["status"] == APP_OPEN and app["roster"]
    assert any(
        harness.guild.council_role.mention in (content or "")
        for content, _ in harness.council.sent
        if "application" in (content or "").lower()
    ), "application did not ping the council"
    interaction = await submit_application(harness, applicant, answer="again")
    assert "already have an open" in interaction.followup.messages[0][0]

    # Votes after the application deadline are rejected, not counted.
    harness = Harness()
    applicant = harness.guild.subject
    await submit_application(harness, applicant)
    app = APPLICATIONS.latest()
    app["deadline"] = conduct.utcnow() - timedelta(seconds=1)
    late = await apply_vote(harness, harness.council_member(0), "yes")
    assert "closed" in late.followup.messages[0][0]
    assert app["votes"] == {}

    # One no rejects immediately; the applicant is DMed and nothing is public.
    harness = Harness()
    applicant = harness.guild.subject
    await submit_application(harness, applicant)
    await apply_vote(harness, harness.council_member(0), "no")
    app = APPLICATIONS.latest()
    assert app["status"] == APP_REJECTED
    assert harness.guild.subject.dms[-1].startswith(
        "Your Duck Council application was rejected"
    )
    assert not harness.log.sent, "rejected application was posted publicly"
    assert any(
        "Outcome: Rejected" in (content or "") for content, _ in harness.council.sent
    )
    # Reapply immediately.
    interaction = await submit_application(harness, applicant, answer="second try")
    assert "has been submitted" in interaction.followup.messages[0][0]

    # All yes approves immediately, grants the role, DMs, and logs.
    harness = Harness()
    applicant = harness.guild.subject
    await submit_application(harness, applicant)
    for index in range(5):
        await apply_vote(harness, harness.council_member(index), "yes")
    app = APPLICATIONS.latest()
    assert app["status"] == APP_APPROVED and app["role_granted"]
    assert harness.guild.council_role in applicant.roles
    assert applicant.dms[-1].startswith("You have been accepted")
    assert harness.log.sent[-1][0] == conduct.public_accept_line("Reported Player")
    assert not any(
        harness.council_member(i).name in str(harness.log.sent) for i in range(5)
    )

    # A non-vote at the 7-day deadline rejects.
    harness = Harness()
    applicant = harness.guild.subject
    await submit_application(harness, applicant)
    for index in range(3):
        await apply_vote(harness, harness.council_member(index), "yes")
    app = APPLICATIONS.latest()
    app["deadline"] = conduct.utcnow() - timedelta(seconds=1)
    await conduct.conduct_tick(harness.bot)
    app = APPLICATIONS.latest()
    assert app["status"] == APP_REJECTED
    assert applicant.dms[-1].startswith("Your Duck Council application was rejected")
    assert not harness.log.sent

    # Role grant failure: DM the applicant, notify Owners, no public acceptance.
    harness = Harness()
    applicant = harness.guild.subject
    applicant.fail_add_roles = True
    await submit_application(harness, applicant)
    for index in range(5):
        await apply_vote(harness, harness.council_member(index), "yes")
    app = APPLICATIONS.latest()
    assert app["status"] == APP_APPROVED and app["role_grant_error"]
    assert "could not grant" in applicant.dms[-1]
    owner_notice = "\n".join(content or "" for content, _ in harness.council.sent)
    assert harness.guild.owner_role.mention in owner_notice
    assert not harness.log.sent, "false public acceptance was posted"

    # Owners may apply, open application views re-register, and a rejected
    # application is purged after 90 days while its outcome is kept.
    harness = Harness()
    interaction = await submit_application(harness, harness.guild.owner)
    assert "has been submitted" in interaction.followup.messages[0][0]
    app = APPLICATIONS.latest()
    harness.bot.views.clear()
    conduct.register_conduct_views(harness.bot)
    assert any(
        getattr(view, "app_id", None) == app["_id"] for view in harness.bot.views
    ), "open application view was not registered on startup"
    await apply_vote(harness, harness.council_member(0), "no")
    app = APPLICATIONS.latest()
    message = harness.council.messages[app["message_id"]]
    app["answer"] = "some private answer"
    app["purge_after"] = conduct.utcnow() - timedelta(seconds=1)
    await conduct.conduct_tick(harness.bot)
    app = APPLICATIONS.latest()
    assert app["purged"] is True and app["answer"] is None
    assert app["tally"]["no"] == 1, "application tally was lost at purge"
    assert "No: 1" in conduct.application_result_line(app)
    assert message.deleted, "application message was not deleted at purge"
    assert app["status"] == APP_REJECTED and app["result_lines"]

    # ------------------------------------------------------------------
    # /unwarn: owner-only, records the actor and reason, removes the +1.
    # ------------------------------------------------------------------
    from commands.conduct import ConductCommands

    harness = Harness()
    now = conduct.utcnow()
    SANCTIONS.insert_one(
        {
            "_id": "to-revoke",
            "subject_id": str(harness.guild.subject.id),
            "type": WARN,
            "issued_at": now - timedelta(days=10),
            "expires_at": now + timedelta(days=300),
            "revoked_at": None,
        }
    )
    cog = ConductCommands(harness.bot)
    interaction = harness.interaction(harness.guild.subject)
    await cog.unwarn.callback(cog, interaction, harness.guild.subject)
    assert "Only the Owner" in interaction.followup.messages[0][0]

    interaction = harness.interaction(harness.guild.owner)
    await cog.unwarn.callback(cog, interaction, harness.guild.subject)
    assert interaction.followup.messages, "owner got no warning selector"
    selector_view = interaction.followup.messages[0][1]["view"]
    assert isinstance(selector_view, UnwarnSelectView)
    selector_view.selector._values = ["to-revoke"]
    modal_interaction = harness.interaction(harness.guild.owner)
    await selector_view._select(modal_interaction)
    modal = modal_interaction.response.modal
    assert modal is not None
    modal.reason._value = "issued in error"
    await modal.on_submit(harness.interaction(harness.guild.owner))
    revoked = SANCTIONS.find_one({"_id": "to-revoke"})
    assert revoked["revoked_at"] is not None
    assert revoked["revoked_by"] == str(harness.guild.owner.id)
    assert revoked["revoke_reason"] == "issued in error"
    assert conduct.active_warning_count(str(harness.guild.subject.id)) == 0
    assert "Revoked warnings: 1" in conduct.history_field(str(harness.guild.subject.id))
    assert not harness.log.sent, "warning revocation posted to the public log"
    await submit_report(harness, harness.reporter)
    assert CASES.latest()["prior_warning"] is False, "revoked warning still weighted"

    # A failed warn-board send is retried by the tick instead of freezing
    # a buttonless phase until its deadline.
    harness = Harness()
    await submit_report(harness, harness.reporter)
    case = CASES.latest()
    case["deadline"] = conduct.utcnow() - timedelta(seconds=1)
    harness.council.fail_next_send = True
    await conduct.conduct_tick(harness.bot)
    case = CASES.latest()
    assert case["status"] == WARN and case["warn_board_posted"] is False
    await conduct.conduct_tick(harness.bot)
    case = CASES.latest()
    assert case["warn_board_posted"] is True
    warn_board = harness.council.messages[case["message_id"]]
    assert "Warn vote" in embed_text(warn_board.embed)

    # ------------------------------------------------------------------
    # Lifecycle: views re-register, expired phases close, and the 90-day
    # purge deletes the board but keeps sanctions and outcomes.
    # ------------------------------------------------------------------
    harness = Harness()
    await submit_report(harness, harness.reporter)
    case = CASES.latest()
    harness.bot.views.clear()
    conduct.register_conduct_views(harness.bot)
    assert any(
        getattr(view, "case_id", None) == case["_id"] for view in harness.bot.views
    ), "open case view was not registered on startup"

    # Expired phase is closed by the startup sweep.
    case["deadline"] = conduct.utcnow() - timedelta(seconds=1)
    await conduct.recover_conduct(harness.bot)
    case = CASES.latest()
    assert case["status"] in (WARN, CLOSED), "expired ban phase was not closed"

    # Finish the warn phase so we can test the purge.
    if case["status"] == WARN:
        for index in range(3):
            await vote(harness, harness.council_member(index), "yes")
        case = CASES.latest()
    assert case["status"] == CLOSED and case["outcome"] == WARN
    sanction = SANCTIONS.find_one({"case_id": case["_id"], "type": WARN})
    assert sanction is not None
    board_message = harness.council.messages[case["message_id"]]

    # A restart that missed the outcome/notifications finishes them later.
    case["notified"] = False
    case["public_logged"] = False
    case["result_posted"] = False
    harness.guild.subject.dms.clear()
    harness.log.sent.clear()
    await conduct.conduct_tick(harness.bot)
    case = CASES.latest()
    assert harness.guild.subject.dms, "missed sanction DM was not sent"
    assert harness.log.sent, "missed public log entry was not posted"
    assert case["notified"] and case["public_logged"] and case["result_posted"]

    case["purge_after"] = conduct.utcnow() - timedelta(seconds=1)
    await conduct.conduct_tick(harness.bot)
    case = CASES.latest()
    assert case["purged"] is True
    assert case["description"] in (None, "")
    assert case["links"] == []
    assert case["votes"] == {}
    assert board_message.deleted, "case message was not deleted at purge"
    assert SANCTIONS.find_one({"_id": sanction["_id"]}) is not None
    assert case["outcome"] == WARN

    # A warning expires after 12 months and is inactive history.
    expired_id = "expiry-check"
    SANCTIONS.insert_one(
        {
            "_id": expired_id,
            "subject_id": "555",
            "type": WARN,
            "issued_at": conduct.utcnow() - timedelta(days=366),
            "expires_at": conduct.utcnow() - timedelta(days=1),
            "revoked_at": None,
        }
    )
    assert conduct.active_warning_count("555") == 0
    assert "Expired warnings (inactive): 1" in conduct.history_field("555")

    # ------------------------------------------------------------------
    # /reportplayer opens the modal; the modal callback never logs identity.
    # ------------------------------------------------------------------
    harness = Harness()
    cog = ConductCommands(harness.bot)
    interaction = harness.interaction(harness.guild.subject)
    await cog.reportplayer.callback(cog, interaction)
    assert isinstance(interaction.response.modal, ReportModal)

    # The generic command/completion loggers skip /reportplayer, and its
    # error logger never includes the reporter's identity.
    import bot as bot_module

    records = []

    class LoggerCapture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = LoggerCapture()
    bot_module.log.addHandler(handler)
    try:
        command = types.SimpleNamespace(qualified_name="reportplayer")
        ctx = types.SimpleNamespace(
            command=command, author=harness.reporter, channel=harness.council
        )
        await bot_module.CustomBot.on_command(object(), ctx)
        log_interaction = harness.interaction(harness.reporter)
        log_interaction.command = command
        await bot_module.CustomBot.on_app_command_completion(
            object(), log_interaction, command
        )
        assert not records, "anonymous command was written to the generic log"
        await bot_module.CustomBot._on_app_command_error(
            object(), log_interaction, RuntimeError("boom")
        )
    finally:
        bot_module.log.removeHandler(handler)
    joined = "\n".join(records)
    assert "RuntimeError" in joined and "reportplayer" in joined
    assert (
        harness.reporter.name not in joined and str(harness.reporter.id) not in joined
    )

    print("all conduct reporting self-checks passed")


if __name__ == "__main__":
    asyncio.run(demo())
