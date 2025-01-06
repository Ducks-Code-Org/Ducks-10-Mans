"""This view creates and maintains a signup interaction, contained within its own channel."""

import asyncio

import discord
from discord.ui import Button

from database import users
from views.mode_vote_view import ModeVoteView


class SignupView(discord.ui.View):
    def __init__(self, ctx, bot):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.bot = bot
        self.bot.origin_ctx = ctx
        # Start refresh tasks ONLY if needed
        self.signup_refresh_task = asyncio.create_task(self.refresh_signup_message())
        self.channel_name_refresh_task = asyncio.create_task(self.refresh_channel_name())

        self.sign_up_button = Button(
            label="Sign Up (0/10)", style=discord.ButtonStyle.green
        )
        self.leave_queue_button = Button(
            label="Leave Queue", style=discord.ButtonStyle.red
        )
        self.add_item(self.sign_up_button)
        self.add_item(self.leave_queue_button)

        self.setup_callbacks()

    def cleanup(self):
        """Clean up all running tasks and state"""
        self.cancel_signup_refresh()
        self.cancel_channel_name_refresh()
        
        # Clear references that could prevent garbage collection
        self.ctx = None
        self.bot = None
        
        # Disable all buttons
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

    async def sign_up_callback(self, interaction: discord.Interaction):
        existing_user = users.find_one({"discord_id": str(interaction.user.id)})
        if existing_user:
            string_id = str(interaction.user.id)
            if string_id not in [p["id"] for p in self.bot.queue]:
                self.bot.queue.append({"id": string_id, "name": interaction.user.name})

                if string_id not in self.bot.player_mmr:
                    self.bot.player_mmr[string_id] = {"mmr": 1000, "wins": 0, "losses": 0}

                self.bot.player_names[string_id] = interaction.user.name

                self.sign_up_button.label = f"Sign Up ({len(self.bot.queue)}/10)"
                riot_names = []
                for player in self.bot.queue:
                    discord_id = player["id"]
                    user_data = users.find_one({"discord_id": str(discord_id)})
                    riot_name = user_data.get("name","Unknown") if user_data else "Unknown"
                    riot_names.append(riot_name)

                # Add role to the new player
                member = interaction.guild.get_member(interaction.user.id) or await interaction.guild.fetch_member(interaction.user.id)
                if member:
                    try:
                        await member.add_roles(self.bot.match_role)
                    except discord.Forbidden:
                        await interaction.followup.send("Could not assign the role due to permissions.", ephemeral=True)
                else:
                    await interaction.followup.send("Could not assign the role. Member not found in guild.", ephemeral=True)

                # Update the message content
                await interaction.response.edit_message(
                    content="Click a button to manage your queue status!" + "\n" + f"Current queue ({len(self.bot.queue)}/10): {', '.join(riot_names)}", 
                    view=self
                )
                await interaction.followup.send(
                    f"{interaction.user.name} added to the queue! ({len(self.bot.queue)}/10)",
                    ephemeral=True
                )

                # Check if queue is full AFTER adding the role
                if len(self.bot.queue) == 10:
                    # Cancel the refresh tasks first
                    self.cancel_signup_refresh()
                    
                    # Send full queue message
                    await interaction.channel.send("The queue is now full, proceeding to the voting stage.")
                    
                    # Ping all players
                    await interaction.channel.send("__Players:__ " + " ".join([f"<@{p['id']}>" for p in self.bot.queue]))
                    
                    # Update bot state
                    self.bot.signup_active = False
                    self.ctx.channel = self.bot.match_channel

                    # Clean up the signup message
                    if self.bot.current_signup_message:
                        try:
                            await self.bot.current_signup_message.delete()
                        except discord.NotFound:
                            pass
                    self.bot.current_signup_message = None

                    # Start the mode vote
                    from views.mode_vote_view import ModeVoteView
                    mode_vote = ModeVoteView(self.ctx, self.bot)
                    await mode_vote.send_view()

            else:
                await interaction.response.send_message("You're already in the queue!", ephemeral=True)
        else:
            await interaction.response.send_message(
                "You must link your Riot account first. Use `!linkriot Name#Tag`",
                ephemeral=True
            )

    async def leave_queue_callback(self, interaction: discord.Interaction):
        player_id_str = str(interaction.user.id)
        self.bot.queue = [p for p in self.bot.queue if p["id"] != player_id_str]
        self.sign_up_button.label = f"Sign Up ({len(self.bot.queue)}/10)"
        riot_names = []
        for player in self.bot.queue:
            discord_id = player["id"]
            user_data = users.find_one({"discord_id": str(discord_id)})
            riot_name = user_data.get("name","Unknown") if user_data else "Unknown"
            riot_names.append(riot_name)
        await interaction.response.edit_message(
            content="Click a button to manage your queue status!" + "\n" + f"Current queue ({len(self.bot.queue)}/10): {', '.join(riot_names)}", view=self
        )

        await interaction.followup.send(f"{interaction.user.name} left the queue. ({len(self.bot.queue)}/10)", ephemeral=True)
        member = interaction.guild.get_member(interaction.user.id) or await interaction.guild.fetch_member(interaction.user.id)
        if member:
            try:
                await member.remove_roles(self.bot.match_role)
            except discord.Forbidden:
                await interaction.followup.send("Could not remove role due to permissions.", ephemeral=True)

    def setup_callbacks(self):
        self.sign_up_button.callback = self.sign_up_callback
        self.leave_queue_button.callback = self.leave_queue_callback

    async def refresh_signup_message(self):
        try:
            # Wait before first refresh to avoid immediate duplicate message
            await asyncio.sleep(60)
            while self.bot.signup_active:
                if self.bot.current_signup_message:
                    try:
                        await self.bot.current_signup_message.delete()
                    except discord.NotFound:
                        pass
                # Re-send the signup message after 60 seconds
                riot_names = []
                for player in self.bot.queue:
                    discord_id = player["id"]
                    user_data = users.find_one({"discord_id": str(discord_id)})
                    riot_name = user_data.get("name","Unknown") if user_data else "Unknown"
                    riot_names.append(riot_name)
                self.bot.current_signup_message = await self.bot.match_channel.send(
                    "Click a button to manage your queue status!" + "\n" + f"Current queue ({len(self.bot.queue)}/10): {', '.join(riot_names)}",
                    view=self,
                    silent=True,
                )
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            pass

    def cancel_signup_refresh(self):
        if self.signup_refresh_task:
            self.signup_refresh_task.cancel()
            self.signup_refresh_task = None

    async def refresh_channel_name(self):
        try:
            while self.bot.signup_active:
                try:
                    await self.bot.match_channel.edit(name=f"{self.bot.match_name}《{len(self.bot.queue)}∕10》")
                except discord.HTTPException:
                    pass
                await asyncio.sleep(720)
        except asyncio.CancelledError:
            pass

    def cancel_channel_name_refresh(self):
        if self.channel_name_refresh_task:
            self.channel_name_refresh_task.cancel()
            self.channel_name_refresh_task = None

    async def send_signup(self):
        return await self.ctx.send(
            content="Click a button to manage your queue status!", view=self
        )

    async def update_signup(self):
        if self.bot.current_signup_message:
            self.sign_up_button.label = f"Sign Up ({len(self.bot.queue)}/10)"
            await self.bot.current_signup_message.edit(
                content="Click a button to manage your queue status!", view=self
            )

    def add_player_to_queue(self, player):
        self.bot.queue.append(player)
