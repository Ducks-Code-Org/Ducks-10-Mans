"""This view is generated, allowing players to interactively draft team members in 'Captains' mode."""

import asyncio

import discord
from discord.ui import Select

from database import users
from views.map_type_vote_view import MapTypeVoteView


class CaptainsDraftingView(discord.ui.View):
    def __init__(self, ctx, bot):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.bot = bot

        self.player_select = Select(
            placeholder="Select a player to pick",
            options=[],
        )
        self.add_item(self.player_select)
        self.remaining_players = [
            p for p in self.bot.queue if p not in [self.bot.captain1, self.bot.captain2]
        ]
        self.pick_order = [
            self.bot.captain1,
            self.bot.captain2,
            self.bot.captain2,
            self.bot.captain1,
            self.bot.captain1,
            self.bot.captain2,
            self.bot.captain2,
            self.bot.captain1,
        ]
        self.pick_count = 0
        self.remaining_players_message = None
        self.drafting_message = None
        self.captain_pick_message = None

        if self.bot.captain1 and self.bot.captain2:
            # Get Riot names for captain
            captain1_data = users.find_one({"discord_id": str(self.bot.captain1["id"])})
            if captain1_data:
                self.captain1_name = f"{captain1_data.get('name', 'Unknown')}#{captain1_data.get('tag', 'Unknown')}"
            else:
                self.captain1_name = self.bot.captain1["name"]

            captain2_data = users.find_one({"discord_id": str(self.bot.captain2["id"])})
            if captain2_data:
                self.captain2_name = f"{captain2_data.get('name', 'Unknown')}#{captain2_data.get('tag', 'Unknown')}"
            else:
                self.captain2_name = self.bot.captain2["name"]

        # Link callbacks to buttons
        self.setup_callbacks()

    def setup_callbacks(self):
        self.player_select.callback = self.select_callback

    async def finalize_draft(self):
        self.pick_count = 0
        if self.remaining_players_message:
            await self.remaining_players_message.delete()
        if self.drafting_message:
            await self.drafting_message.delete()
        if self.captain_pick_message:
            await self.captain_pick_message.delete()

        print(f"Final team 1: {self.bot.team1}")
        print(f"Final team 2: {self.bot.team2}")
        # Get Riot names for team members
        team1_names = []
        for p in self.bot.team1:
            user_data = users.find_one({"discord_id": str(p["id"])})
            if user_data:
                riot_name = user_data.get("name", "Unknown")
                riot_tag = user_data.get("tag", "Unknown")
                team1_names.append(f"{riot_name}#{riot_tag}")
            else:
                team1_names.append(p["name"])

        team2_names = []
        for p in self.bot.team2:
            user_data = users.find_one({"discord_id": str(p["id"])})
            if user_data:
                riot_name = user_data.get("name", "Unknown")
                riot_tag = user_data.get("tag", "Unknown")
                team2_names.append(f"{riot_name}#{riot_tag}")
            else:
                team2_names.append(p["name"])

        # Create embed for the final teams
        final_teams_embed = discord.Embed(
            title="Final Teams", color=discord.Color.green()
        )
        final_teams_embed.add_field(
            name=f"Attackers (Captain: {self.captain1_name})",
            value="\n".join(team1_names),
            inline=False,
        )
        final_teams_embed.add_field(
            name=f"Defenders (Captain: {self.captain2_name})",
            value="\n".join(team2_names),
            inline=False,
        )

        # Display final teams
        await self.ctx.send(embed=final_teams_embed)

        map_type_vote = MapTypeVoteView(self.ctx, self.bot)

        # Begin vote for competitive or all maps
        await map_type_vote.send_view()

    async def select_callback(self, interaction: discord.Interaction):
        current_captain_id = self.pick_order[self.pick_count]["id"]
        if interaction.user.id != current_captain_id:
            await interaction.response.send_message(
                "It's not your turn to pick.", ephemeral=True
            )
            return

        selected_player_id = int(self.player_select.values[0])
        player_dict = next(
            (p for p in self.remaining_players if p["id"] == selected_player_id), None
        )
        if not player_dict:
            await interaction.response.send_message(
                "Player not available. Please select a valid player.", ephemeral=True
            )
            return

        print(player_dict)
        # Add the player to the right team
        if current_captain_id == self.bot.captain1["id"]:
            self.bot.team1.append(player_dict)
        else:
            self.bot.team2.append(player_dict)
        self.pick_count += 1
        self.remaining_players.remove(player_dict)

        # Let discord know the action was processed
        await interaction.response.defer()

        # Attempt to go to the next player to select
        await self.draft_next_player()

    async def draft_next_player(self):
        print(self.remaining_players)
        if len(self.remaining_players) == 0:
            await self.finalize_draft()
            return

        await self.send_current_draft_view()

    async def send_current_draft_view(self):
        print("printing draft")
        if self.bot.captain1 is None:
            await self.ctx.send("No captain 1 set. use !setcaptain1 <name>")
            return
        if self.bot.captain2 is None:
            await self.ctx.send("No captain 2 set. use !setcaptain2 <name>")
            return

        options = []
        for p in self.remaining_players:
            user_data = users.find_one({"discord_id": str(p["id"])})
            if user_data:
                riot_name = user_data.get("name", "Unknown")
                riot_tag = user_data.get("tag", "Unknown")
                label = f"{riot_name}#{riot_tag}"
            else:
                label = p["name"]
            options.append(discord.SelectOption(label=label, value=str(p["id"])))

        self.player_select.options = options

        # Construct the messages for the draft
        remaining_players_names = []
        for p in self.remaining_players:
            user_data = users.find_one({"discord_id": str(p["id"])})
            if user_data:
                riot_name = user_data.get("name", "Unknown")
                riot_tag = user_data.get("tag", "Unknown")
                name = f"{riot_name}#{riot_tag}"
            else:
                name = p["name"]
            remaining_players_names.append(name)

        remaining_players_embed = discord.Embed(
            title="Remaining Players",
            description="\n".join(remaining_players_names),
            color=discord.Color.blue(),
        )

        # Embed to display the currently drafted players
        # For team1
        team1_names = []
        for p in self.bot.team1:
            user_data = users.find_one({"discord_id": str(p["id"])})
            if user_data:
                riot_name = user_data.get("name", "Unknown")
                riot_tag = user_data.get("tag", "Unknown")
                name = f"{riot_name}#{riot_tag}"
            else:
                name = p["name"]
            team1_names.append(name)

        # For team2
        team2_names = []
        for p in self.bot.team2:
            user_data = users.find_one({"discord_id": str(p["id"])})
            if user_data:
                riot_name = user_data.get("name", "Unknown")
                riot_tag = user_data.get("tag", "Unknown")
                name = f"{riot_name}#{riot_tag}"
            else:
                name = p["name"]
            team2_names.append(name)

        # Get captains' Riot names (already obtained earlier)
        drafting_embed = discord.Embed(
            title="Current Draft", color=discord.Color.green()
        )
        drafting_embed.add_field(
            name=f"**{self.captain1_name}'s Team**",
            value="\n".join(team1_names) if team1_names else "No players yet",
            inline=False,
        )
        drafting_embed.add_field(
            name=f"**{self.captain2_name}'s Team**",
            value="\n".join(team2_names) if team2_names else "No players yet",
            inline=False,
        )

        # Get current captain's Riot name
        current_captain_id = self.pick_order[self.pick_count]["id"]
        current_captain_name = self.pick_order[self.pick_count]["name"]
        current_captain_data = users.find_one({"discord_id": str(current_captain_id)})

        if current_captain_data:
            current_captain_name = f"{current_captain_data.get('name', 'Unknown')}#{current_captain_data.get('tag', 'Unknown')}"

        # Send message for the first time
        message = f"**{current_captain_name}**, please pick a player:"

        if self.captain_pick_message is not None:
            await self.remaining_players_message.edit(embed=remaining_players_embed)
            await self.drafting_message.edit(embed=drafting_embed)
            await self.captain_pick_message.edit(content=message, view=self)
        else:
            self.remaining_players_message = await self.ctx.send(
                embed=remaining_players_embed
            )
            self.drafting_message = await self.ctx.send(embed=drafting_embed)
            self.captain_pick_message = await self.ctx.send(content=message, view=self)

        # Wait for the captain to make a selection or time out
        try:
            await self.bot.wait_for(
                "interaction",
                check=lambda i: i.data.get("component_type") == 3
                and i.user.id == current_captain_id,
                timeout=60,
            )
        except asyncio.TimeoutError:
            await self.ctx.send(
                f"{current_captain_name} took too long to pick. Drafting canceled. Count: {self.pick_count}"
            )

            self.bot.queue.clear()
            self.bot.signup_thread.delete()
