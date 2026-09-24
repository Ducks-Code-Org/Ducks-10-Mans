"""This view creates and maintains a signup interaction, contained within its own channel."""

import asyncio
import logging

import aiohttp
import discord
from discord.ui import Button

from database import users
from game.recent_queue import remember_recent_queue
from services.riot_api import verify_riot_account_async
from game.stats_helper import DEFAULT_MMR
from tracker_links import tracker_link_for
from views import safe_reply
from views.mode_vote_view import ModeVoteView
from game.voice_presence import voice_presence_enabled, wait_for_lobby

log = logging.getLogger(__name__)


class ChannelContext:
    """A minimal ctx bound to one channel (the match channel).

    Slash contexts post interaction followups to the channel the command was
    invoked in, so after the signup moves into the generated match channel the
    downstream vote/draft views need a ctx whose `send` targets that channel
    (the prefix path previously faked this by reassigning ctx.channel).
    Exposes only what the setup views use: send, guild, channel, author.
    """

    def __init__(self, channel, guild=None, author=None):
        self.channel = channel
        self.guild = guild if guild is not None else getattr(channel, "guild", None)
        self.author = author

    async def send(self, content=None, **kwargs):
        # Interactions-only kwargs (ephemeral/silent) don't apply to channel sends.
        kwargs.pop("ephemeral", None)
        return await self.channel.send(content, **kwargs)


class SignupView(discord.ui.View):
    def __init__(self, ctx, bot):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.bot = bot
        self.bot.origin_ctx = ctx
        # Capture the current setup cycle so we can detect a later !cancel.
        self.setup_generation = bot.setup_generation

        # Start Task Runners
        self.signup_request_queue = (
            asyncio.Queue()
        )  # Interaction Queue of (interaction, future)
        self.signup_queue_task = asyncio.create_task(self.process_signup_queue())
        self.refresh_signup_task = asyncio.create_task(self.refresh_signup_message())
        self.channel_rename_task = asyncio.create_task(self.channel_rename_worker())
        self.timeout_monitor_task = asyncio.create_task(self.monitor_queue())

        # Activity tracking
        self.last_activity_time = asyncio.get_event_loop().time()
        self.empty_since_time = None

        # Setup Interaction Buttons
        self.sign_up_button = Button(
            label="Sign Up (0/10)", style=discord.ButtonStyle.green
        )
        self.leave_queue_button = Button(
            label="Leave Queue", style=discord.ButtonStyle.red
        )
        self.add_item(self.sign_up_button)
        self.add_item(self.leave_queue_button)
        self.sign_up_button.callback = self.sign_up_callback
        self.leave_queue_button.callback = self.leave_queue_callback

        log.info("Starting new signup...")

    async def sign_up_callback(self, interaction: discord.Interaction):
        log.info("Sign up requested by: %s", interaction.user.name)

        # Defer the interaction if not already done, to allow time for processing
        if not interaction.response.is_done():
            try:
                await interaction.response.defer(ephemeral=True)
            except discord.errors.NotFound:
                # Interaction expired, do not queue
                return

        # Add the interaction to the signup queue and wait for processing
        loop: asyncio.AbstractEventLoop = asyncio.get_event_loop()
        fut: asyncio.Future[None] = loop.create_future()
        await self.signup_request_queue.put((interaction, fut))
        await fut  # Wait until this request is processed

    async def leave_queue_callback(self, interaction: discord.Interaction):
        log.info("Leave queue requested by: %s", interaction.user.name)

        # Defer the interaction if not already done, to allow time for processing
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        # If this signup was cancelled (e.g. by !cancel), stop processing.
        if self.bot is None or self.bot.setup_generation != self.setup_generation:
            await interaction.followup.send(
                "This signup was cancelled.", ephemeral=True
            )
            return

        # Check if the user is in the queue
        player_id: str = str(interaction.user.id)
        if player_id not in [p["id"] for p in self.bot.queue]:
            await interaction.followup.send("You're not in the queue!", ephemeral=True)
            return

        # Remove the user from the queue
        new_queue: list[dict] = []
        for player in self.bot.queue:
            if player["id"] != player_id:
                new_queue.append(player)
        self.bot.queue = new_queue
        log.info("%s left the queue successfully", interaction.user.name)

        # Update last activity
        self.last_activity_time = asyncio.get_event_loop().time()

        # Edit the queue message and button label to reflect the new queue.
        # Prefer the canonical signup message (see handle_signup, issue #181).
        self.sign_up_button.label = f"Sign Up ({len(self.bot.queue)}/10)"
        signup_message = self.bot.current_signup_message or interaction.message
        try:
            await signup_message.edit(
                embed=self.get_signup_embed(),
                view=self,
            )
        except discord.NotFound:
            self.bot.current_signup_message = await self.bot.match_channel.send(
                embed=self.get_signup_embed(),
                view=self,
                silent=True,
            )

        # Notify the user that they have left the queue
        await interaction.followup.send(
            f"{interaction.user.name} left the queue.",
            ephemeral=True,
        )

        # Remove the match role from the user
        member: discord.Member = interaction.guild.get_member(
            interaction.user.id
        ) or await interaction.guild.fetch_member(interaction.user.id)
        if member:
            await member.remove_roles(self.bot.match_role)

    async def process_signup_queue(self):
        while True:
            # Wait for the next signup request (blocks until available)
            interaction, fut = await self.signup_request_queue.get()
            try:
                # Process the signup for this interaction
                await self.handle_signup(interaction)
            except Exception as e:
                # Keep the queue alive so later signups still work
                log.error("Error processing signup interaction: %s", e, exc_info=e)
            finally:
                # Ensure the waiting coroutine is notified, even if an error occurs
                if not fut.done():
                    fut.set_result(None)

    def cancel_signup_queue_task(self):
        if self.signup_queue_task:
            self.signup_queue_task.cancel()
            self.signup_queue_task = None

    async def monitor_queue(self):
        try:
            while True:
                await asyncio.sleep(60)  # Check every minute
                now = asyncio.get_event_loop().time()

                if len(self.bot.queue) == 0:
                    if self.empty_since_time is None:
                        self.empty_since_time = now
                    elif now - self.empty_since_time > 600:  # 10 minutes
                        await self.cancel_signup(
                            "Queue has been empty for more than 10 minutes."
                        )
                        break
                else:
                    self.empty_since_time = None

                if now - self.last_activity_time > 7200:  # 120 minutes
                    await self.cancel_signup(
                        "Queue has been inactive for more than 120 minutes."
                    )
                    break
        except asyncio.CancelledError:
            pass

    def cancel_timeout_monitor_task(self):
        if self.timeout_monitor_task:
            self.timeout_monitor_task.cancel()
            self.timeout_monitor_task = None

    async def cancel_signup(self, reason):
        log.info("Signup cancelled: %s", reason)
        # Send message to original channel
        try:
            await self.ctx.send(f"Signup cancelled: {reason}")
        except discord.HTTPException:
            pass  # In case channel is deleted or something

        # Remember who was in the queue for /pingrecent
        if self.bot.queue:
            remember_recent_queue(self.bot.queue, cancelled=True)

        # Invalidate this setup cycle, then clear variables
        self.bot.setup_generation += 1
        self.bot.signup_active = False
        self.bot.queue = []
        self.bot.captain1 = None
        self.bot.captain2 = None
        self.bot.team1 = []
        self.bot.team2 = []
        self.bot.chosen_mode = None
        self.bot.selected_map = None

        from game.duck_coins import (
            announce_cancellation_async,
            refund_match_coins,
        )

        refunded = refund_match_coins(self.bot)
        if refunded:
            await announce_cancellation_async(self.bot, self.ctx.guild)

        # Delete role and channel
        try:
            await self.bot.match_role.delete()
        except discord.HTTPException:
            pass
        try:
            await self.bot.match_channel.delete()
        except discord.HTTPException:
            pass
        self.bot.match_setup_generation = None

        # Cleanup view
        self.stop()
        self.cancel_refresh_signup_task()
        self.cancel_channel_rename_task()
        self.cancel_signup_queue_task()
        self.cancel_timeout_monitor_task()

    async def handle_signup(self, interaction: discord.Interaction):
        """Queue a button signup through the shared signup_player path."""
        user_id: str = str(interaction.user.id)

        async def notify(msg: str):
            await safe_reply(interaction, msg, ephemeral=True)

        # Resolve the member for the match-role grant (best effort).
        member = None
        guild = interaction.guild
        if guild is not None:
            member = guild.get_member(interaction.user.id)
            if member is None:
                try:
                    member = await guild.fetch_member(interaction.user.id)
                except (discord.NotFound, discord.HTTPException):
                    member = None

        await self.signup_player(
            user_id,
            interaction.user.name,
            member=member,
            notify=notify,
            channel=interaction.channel,
        )

    async def signup_player(
        self,
        user_id: str,
        display_name: str,
        *,
        member=None,
        notify=None,
        channel=None,
        verified_user: dict | None = None,
    ) -> bool:
        """Core signup shared by the Sign Up button and the !signup runner.

        Runs the same gates (capacity, duplicates, Riot link), queue add,
        MMR seeding, match role, and signup-embed refresh for both paths.
        `verified_user` skips the Riot re-verification for callers that just
        verified the identity themselves (the /signup command, which runs
        ensure_current_riot_identity first). `notify` receives user-facing
        feedback (interaction followup or ctx.send); `channel` is where the
        full-queue handoff to match setup happens. Returns True when the
        player was added.
        """
        if notify is None:
            notify = lambda _msg: None  # noqa: E731 — silent by default

        async def send_notify(msg: str):
            """Run notify, awaiting async callables and calling sync ones.

            Best-effort: a failed user-facing reply (e.g. a followup whose
            interaction channel no longer exists — 10003 Unknown Channel)
            must never abort the signup pipeline, or a 10/10 queue silently
            never reaches match setup (issue #216).
            """
            try:
                result = notify(msg)
                if hasattr(result, "__await__"):
                    await result
            except discord.HTTPException as e:
                log.warning("Could not send signup notification %r: %s", msg, e)

        # If this signup was cancelled (e.g. by !cancel), stop processing.
        if self.bot is None or self.bot.setup_generation != self.setup_generation:
            await send_notify("This signup was cancelled.")
            return False

        # Only allow up to 10 players in the queue
        if len(self.bot.queue) >= 10:
            await send_notify(
                "❌ The queue is already full! Please wait for the next game."
            )
            return False

        # Check that the user is not already in the queue
        if user_id in [p["id"] for p in self.bot.queue]:
            await send_notify("You're already in the queue!")
            return False

        # Verify the user has linked their Riot account
        db_user: dict | None = (
            verified_user
            if verified_user is not None
            else users.find_one({"discord_id": user_id})
        )
        if not db_user:
            await send_notify(
                "❌ You must link your Riot account first using `/linkriot <Name#Tag>`."
            )
            return False

        if verified_user is None:
            # Verify the user's Riot account (async + rate-limited; 429s never
            # block the signup)
            user_name: str = (db_user.get("name") or "").lower().strip()
            user_tag: str = (db_user.get("tag") or "").lower().strip()
            async with aiohttp.ClientSession() as session:
                is_successful, reason = await verify_riot_account_async(
                    session, user_name, user_tag
                )
            if not is_successful:
                await send_notify(
                    f"❌ Your stored Riot ID `{user_name}#{user_tag}` could not be verified: {reason}"
                )
                return False

            # Verify the stored Riot ID is linked to THIS discord id in the database
            # (someone else may have linked the same Riot ID to their account)
            linked_user = users.find_one({"name": user_name, "tag": user_tag})
            if not linked_user or str(linked_user.get("discord_id")) != user_id:
                await send_notify(
                    "❌ Your Riot ID is linked to a different Discord account, or was changed "
                    "after another user linked it. Please re-link it using `/linkriot <Name#Tag>`."
                )
                return False

        # Re-check the cancellation gate AFTER the awaits above (issue #236):
        # the Riot verification can take seconds, and a !cancel or queue
        # timeout during that window invalidates this signup cycle. Without
        # this re-check the player is appended to a dead queue — and told
        # "added to the queue!" for a signup that no longer exists.
        if self.bot is None or self.bot.setup_generation != self.setup_generation:
            await send_notify("This signup was cancelled.")
            return False

        # Add the user the queue, and create mmr data if not present
        self.bot.queue.append({"id": user_id, "name": display_name})
        if user_id not in self.bot.player_mmr:
            self.bot.player_mmr[user_id] = {
                "mmr": DEFAULT_MMR,
                "wins": 0,
                "losses": 0,
            }
        self.bot.player_names[user_id] = display_name
        log.info("%s joined the queue successfully.", display_name)

        # Update last activity
        self.last_activity_time = asyncio.get_event_loop().time()

        # Add match role to the new player (best effort)
        if member is not None and getattr(self.bot, "match_role", None):
            try:
                await member.add_roles(self.bot.match_role)
            except discord.HTTPException:
                log.warning("Could not add the match role to %s", display_name)

        # Update the message and the signup button. Prefer the canonical
        # signup message: a stale/deleted message is recreated below so the
        # queue embed and live buttons are never left missing (issue #181).
        self.sign_up_button.label = f"Sign Up ({len(self.bot.queue)}/10)"
        try:
            await self.bot.current_signup_message.edit(
                embed=self.get_signup_embed(),
                view=self,
            )
        except (discord.NotFound, AttributeError):
            try:
                self.bot.current_signup_message = await self.bot.match_channel.send(
                    embed=self.get_signup_embed(),
                    view=self,
                    silent=True,
                )
            except (discord.NotFound, discord.HTTPException, AttributeError):
                pass
        await send_notify(f"{display_name} added to the queue!")

        # Check if queue is full
        if len(self.bot.queue) == 10 and channel is not None:
            await self.finalize_signup(channel)
        return True

    async def finalize_signup(self, channel):
        # If this signup was cancelled (e.g. by !cancel), don't start match setup.
        if self.bot.setup_generation != self.setup_generation:
            log.info("Skipping signup finalization because signup was cancelled.")
            return

        log.info("Signup full (%s players); starting match setup", len(self.bot.queue))
        await channel.send("The queue is now full, proceeding to the voting stage.")

        # Disable Buttons
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

        # Ping all players
        await channel.send(
            "__Players:__ " + " ".join([f"<@{p['id']}>" for p in self.bot.queue])
        )

        # Wait for everyone to join the lobby voice channel before setup
        # (feature-flagged in bot.ini).
        if voice_presence_enabled():
            ready = await wait_for_lobby(
                self.ctx.guild,
                self.bot.queue,
                send=channel.send,
                is_cancelled=lambda: self.bot is None
                or self.bot.setup_generation != self.setup_generation,
            )
            if not ready:
                if (
                    self.bot is not None
                    and self.bot.setup_generation == self.setup_generation
                ):
                    await self.cancel_signup(
                        "Players did not join the lobby voice channel in time."
                    )
                return

        self.bot.signup_active = False
        # From here on the setup runs inside the match channel. A slash ctx
        # always posts followups to the command's invocation channel, so bind
        # a channel-bound ctx instead of reassigning ctx.channel (which only
        # worked for prefix invocations).
        self.ctx = ChannelContext(
            self.bot.match_channel, guild=self.ctx.guild, author=self.ctx.author
        )

        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        await self.bot.current_signup_message.edit(view=self)

        self.bot.chosen_mode = None
        mode_vote = ModeVoteView(self.ctx, self.bot, self.setup_generation)
        await mode_vote.send_view()
        self.stop()
        self.cancel_refresh_signup_task()
        self.cancel_channel_rename_task()
        self.cancel_signup_queue_task()
        self.cancel_timeout_monitor_task()

    async def refresh_signup_message(self):
        try:
            await asyncio.sleep(60)
            while self.bot.signup_active:
                if self.bot.current_signup_message:
                    try:
                        await self.bot.current_signup_message.edit(
                            embed=self.get_signup_embed(),
                            view=self,
                        )
                    except discord.NotFound:
                        # Message deleted, recreate it
                        self.bot.current_signup_message = (
                            await self.bot.match_channel.send(
                                embed=self.get_signup_embed(),
                                view=self,
                                silent=True,
                            )
                        )
                else:
                    self.bot.current_signup_message = await self.bot.match_channel.send(
                        embed=self.get_signup_embed(),
                        view=self,
                        silent=True,
                    )

                await asyncio.sleep(60)
        except asyncio.CancelledError:
            pass

    def get_signup_embed(self) -> discord.Embed:
        # Construct a signup embed, listing players, in order of signup as <discord_name>(<Riot_id>)

        def get_user_data(player) -> tuple[str, str | None]:
            user_data = users.find_one({"discord_id": str(player["id"])})
            member = self.ctx.guild.get_member(int(player["id"]))
            display_name = member.display_name if member else "Unknown"
            # Only linked players get a tracker link; an unlinked player (dead
            # Riot account) would otherwise link to a nonexistent profile.
            link = tracker_link_for(user_data)

            return display_name, link

        player_embed_lines = []
        for player in self.bot.queue:
            display_name, link = get_user_data(player)
            line = f"{display_name} ({link})" if link else f"{display_name} (N/A)"
            player_embed_lines.append(line)

        embed = discord.Embed(
            title="Signup Queue",
            description=(
                "Click a button to manage your queue status!\n"
                "_Note: Pressing the button more than once does not help you "
                "join the queue faster._"
            ),
            color=discord.Color.yellow(),
        )
        if len(self.bot.queue):
            embed.add_field(
                name="Players:",
                value="\n".join(player_embed_lines),
                inline=False,
            )
        embed.set_footer(text=f"Total: {len(self.bot.queue)}/10")
        return embed

    def cancel_refresh_signup_task(self):
        if self.refresh_signup_task:
            self.refresh_signup_task.cancel()
            self.refresh_signup_task = None

    async def channel_rename_worker(self):
        await asyncio.sleep(720)
        try:
            while self.bot.signup_active:
                new_channel_name = f"{self.bot.match_name}《{len(self.bot.queue)}∕10》"

                if self.bot.match_channel.name != new_channel_name:
                    try:
                        await self.bot.match_channel.edit(name=new_channel_name)
                        log.info("Renamed channel to %s", new_channel_name)
                    except discord.HTTPException:
                        log.warning("Failed to rename channel %s", self.bot.match_name)
                    await asyncio.sleep(720)
                else:
                    await asyncio.sleep(10)  # small delay to avoid busy loop
        except asyncio.CancelledError:
            pass

    def cancel_channel_rename_task(self):
        if self.channel_rename_task:
            self.channel_rename_task.cancel()
            self.channel_rename_task = None

    def cleanup(self):
        """Used to cleanup the signup externally"""
        self.cancel_channel_rename_task()
        self.cancel_refresh_signup_task()
        self.cancel_signup_queue_task()
        self.cancel_timeout_monitor_task()

        self.ctx = None
        self.bot = None

        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
