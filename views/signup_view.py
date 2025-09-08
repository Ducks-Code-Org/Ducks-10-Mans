"""This view creates and maintains a signup interaction, contained within its own channel."""

import asyncio

import discord
from discord.ui import Button

from database import users
from riot_api import verify_riot_account
from views import safe_reply
from views.mode_vote_view import ModeVoteView


class SignupView(discord.ui.View):
    def __init__(self, ctx, bot):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.bot = bot
        self.bot.origin_ctx = ctx

        # Start Task Runners
        self.signup_request_queue = (
            asyncio.Queue()
        )  # Interaction Queue of (interaction, future)
        self.signup_queue_task = asyncio.create_task(self.process_signup_queue())
        self.refresh_signup_task = asyncio.create_task(self.refresh_signup_message())
        self.channel_rename_task = asyncio.create_task(self.channel_rename_worker())

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

        print("Starting new signup...")

    async def sign_up_callback(self, interaction: discord.Interaction):
        print(f"Sign up requested by: {interaction.user.name}")

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
        print(f"Leave queue requested by: {interaction.user.name}")

        # Defer the interaction if not already done, to allow time for processing
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

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
        riot_names: list[str] = self.get_riot_names()
        print(f"{interaction.user.name} left the queue successfully")

        # Edit the queue message and button label to reflect the new queue
        self.sign_up_button.label = f"Sign Up ({len(self.bot.queue)}/10)"
        await interaction.message.edit(
            embed=self.get_signup_embed(),
            view=self,
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
            finally:
                # Ensure the waiting coroutine is notified, even if an error occurs
                if not fut.done():
                    fut.set_result(None)

    def cancel_signup_queue_task(self):
        if self.signup_queue_task:
            self.signup_queue_task.cancel()
            self.signup_queue_task = None

    async def handle_signup(self, interaction: discord.Interaction):
        # Only allow up to 10 players in the queue
        if len(self.bot.queue) >= 10:
            await safe_reply(
                interaction,
                "❌ The queue is already full! Please wait for the next game.",
                ephemeral=True,
            )
            return

        # Check that the user is not already in the queue
        user_id: str = str(interaction.user.id)
        if user_id in [p["id"] for p in self.bot.queue]:
            await safe_reply(
                interaction, "You're already in the queue!", ephemeral=True
            )
            return

        # Verify the user has linked their Riot account
        db_user: dict | None = users.find_one({"discord_id": str(interaction.user.id)})
        if not db_user:
            await safe_reply(
                interaction,
                "❌ You must link your Riot account first using `!linkriot <Name#Tag>`.",
                ephemeral=True,
            )
            return

        # Verify the user's Riot account
        user_name: str = (db_user.get("name") or "").lower().strip()
        user_tag: str = (db_user.get("tag") or "").lower().strip()
        is_successful, reason = verify_riot_account(user_name, user_tag)
        if not is_successful:
            await safe_reply(
                interaction,
                f"❌ Your stored Riot ID `{user_name}#{user_tag}` could not be verified: {reason}",
                ephemeral=True,
            )
            return

        # Add the user the queue, and create mmr data if not present
        self.bot.queue.append({"id": user_id, "name": interaction.user.name})
        if user_id not in self.bot.player_mmr:
            self.bot.player_mmr[user_id] = {
                "mmr": 1000,
                "wins": 0,
                "losses": 0,
            }
        self.bot.player_names[user_id] = interaction.user.name
        riot_names: list[str] = self.get_riot_names()
        print(f"{interaction.user.name} joined the queue successfully.")

        # Add match role to the new player
        member = interaction.guild.get_member(
            interaction.user.id
        ) or await interaction.guild.fetch_member(interaction.user.id)
        if member:
            await member.add_roles(self.bot.match_role)

        # Update the message and the signup button
        self.sign_up_button.label = f"Sign Up ({len(self.bot.queue)}/10)"
        await interaction.message.edit(
            embed=self.get_signup_embed(),
            view=self,
        )
        await interaction.followup.send(
            f"{interaction.user.name} added to the queue!",
            ephemeral=True,
        )

        # Check if queue is full
        if len(self.bot.queue) == 10:
            await self.finalize_signup(interaction)

    async def finalize_signup(self, interaction: discord.Interaction):
        await interaction.channel.send(
            "The queue is now full, proceeding to the voting stage."
        )

        # Disable Buttons
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

        # Ping all players
        await interaction.channel.send(
            "__Players:__ " + " ".join([f"<@{p['id']}>" for p in self.bot.queue])
        )

        self.bot.signup_active = False
        self.ctx.channel = self.bot.match_channel

        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        await self.bot.current_signup_message.edit(view=self)

        self.bot.chosen_mode = None
        mode_vote = ModeVoteView(self.ctx, self.bot)
        await mode_vote.send_view()
        self.stop()
        self.cancel_refresh_signup_task()
        self.cancel_channel_rename_task()
        self.cancel_signup_queue_task()

    async def refresh_signup_message(self):
        try:
            await asyncio.sleep(60)
            while self.bot.signup_active:
                riot_names: list[str] = self.get_riot_names()

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

        def get_user_data(player) -> tuple[str, str, str]:
            user_data = users.find_one({"discord_id": str(player["id"])})
            member = self.ctx.guild.get_member(int(player["id"]))
            display_name = member.display_name if member else "Unknown"
            riot_name = user_data.get("name", "Unknown") if user_data else "Unknown"
            riot_tag = user_data.get("tag", "Unknown") if user_data else "Unknown"

            return display_name, riot_name, riot_tag

        player_embed_lines = []
        for player in self.bot.queue:
            display_name, riot_name, riot_tag = get_user_data(player)
            player_embed_lines.append(f"{display_name} (`{riot_name}#{riot_tag}`)")

        embed = discord.Embed(
            title="Signup Queue",
            description="Click a button to manage your queue status!",
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
                        print(f"Renamed channel to {new_channel_name}")
                    except discord.HTTPException:
                        print(f"Failed to rename channel {self.bot.match_name}")
                    await asyncio.sleep(720)
                else:
                    await asyncio.sleep(10)  # small delay to avoid busy loop
        except asyncio.CancelledError:
            pass

    def cancel_channel_rename_task(self):
        if self.channel_rename_task:
            self.channel_rename_task.cancel()
            self.channel_rename_task = None

    def get_riot_names(self) -> list[str]:
        riot_names: list[str] = []
        for player in self.bot.queue:
            discord_id = player["id"]
            user_data = users.find_one({"discord_id": str(discord_id)})
            riot_name = user_data.get("name", "Unknown") if user_data else "Unknown"
            riot_names.append(riot_name)
        return riot_names

    def cleanup(self):
        """Used to cleanup the signup externally"""
        self.cancel_channel_rename_task()
        self.cancel_refresh_signup_task()
        self.cancel_signup_queue_task()

        self.ctx = None
        self.bot = None

        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
