import asyncio
import random
import discord
from discord.ext import commands
import requests
from table2ascii import table2ascii as t2a, PresetStyle
from views.tdm_map_vote_view import TDMMapVoteView
import os

from database import users, tdm_mmr_collection, tdm_matches

class TDMCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tdm_signup_active = False
        self.tdm_match_ongoing = False
        self.tdm_queue = []
        self.tdm_team1 = []
        self.tdm_team2 = []
        self.tdm_match_channel = None
        self.tdm_match_role = None
        self.tdm_current_message = None

    @commands.command()
    async def tdm(self, ctx):
        # Check if any match is in progress
        if self.tdm_signup_active:
            await ctx.send("❌ A TDM signup is already in progress.")
            return

        if self.bot.match_not_reported or self.tdm_match_ongoing:
            await ctx.send("❌ A match is still in progress. Report it before starting another one.")
            return

        # Initialize TDM state
        self.tdm_signup_active = True
        self.tdm_queue = []
        self.tdm_team1 = []
        self.tdm_team2 = []

        # Create match channel
        self.tdm_match_name = f"tdm-{random.randrange(1, 10**4):04}"
        
        try:
            # Create and position role
            self.tdm_match_role = await ctx.guild.create_role(
                name=self.tdm_match_name,
                hoist=True,
                reason="TDM Match Role"
            )
            await ctx.guild.edit_role_positions(positions={self.tdm_match_role: 5})

            # Set up channel permissions
            match_channel_permissions = {
                ctx.guild.default_role: discord.PermissionOverwrite(send_messages=False),
                self.tdm_match_role: discord.PermissionOverwrite(send_messages=True),
                ctx.guild.me: discord.PermissionOverwrite(send_messages=True, manage_messages=True)
            }

            # Create channel
            self.tdm_match_channel = await ctx.guild.create_text_channel(
                name=self.tdm_match_name,
                category=ctx.channel.category,
                position=0,
                overwrites=match_channel_permissions,
                reason="TDM Match Channel"
            )
        except discord.Forbidden:
            self.tdm_signup_active = False
            await ctx.send("❌ I don't have permission to create channels or roles!")
            return
        except discord.HTTPException as e:
            self.tdm_signup_active = False
            await ctx.send(f"❌ Failed to create match channel: {str(e)}")
            return

        # Create signup view
        view = discord.ui.View(timeout=None)
        signup_button = discord.ui.Button(
            label="Sign Up (0/6)",
            style=discord.ButtonStyle.green,
            emoji="✅"
        )
        leave_button = discord.ui.Button(
            label="Leave Queue",
            style=discord.ButtonStyle.red,
            emoji="❌"
        )

        async def signup_callback(interaction):
            if len(self.tdm_queue) >= 6:
                await interaction.response.send_message("❌ Queue is full!", ephemeral=True)
                return

            existing_user = users.find_one({"discord_id": str(interaction.user.id)})
            if not existing_user:
                await interaction.response.send_message(
                    "❌ You must link your Riot account first using `!linkriot Name#Tag`",
                    ephemeral=True
                )
                return

            if str(interaction.user.id) not in [p["id"] for p in self.tdm_queue]:
                self.tdm_queue.append({"id": str(interaction.user.id), "name": interaction.user.name})
                signup_button.label = f"Sign Up ({len(self.tdm_queue)}/6)"
                
                # Ensure TDM MMR exists
                self.bot.ensure_tdm_player_mmr(str(interaction.user.id))
                try:
                    # Add role
                    member = interaction.guild.get_member(interaction.user.id) or await interaction.guild.fetch_member(interaction.user.id)
                    await member.add_roles(self.tdm_match_role)
                except discord.Forbidden:
                    await interaction.response.send_message("⚠️ Could not assign role due to permissions.", ephemeral=True)
                
                # Get all queued players' Riot names
                riot_names = []
                for player in self.tdm_queue:
                    user_data = users.find_one({"discord_id": player["id"]})
                    if user_data:
                        riot_name = f"{user_data.get('name')}#{user_data.get('tag')}"
                        riot_names.append(riot_name)
                    else:
                        riot_names.append("Unknown")

                # Update embed with current queue
                embed = discord.Embed(
                    title="3v3 Team Deathmatch Queue",
                    description="Click below to join or leave the queue!",
                    color=discord.Color.blue()
                )
                embed.add_field(
                    name=f"Current Queue ({len(self.tdm_queue)}/6)",
                    value="\n".join(riot_names) if riot_names else "No players in queue",
                    inline=False
                )
                embed.add_field(
                    name="About TDM Mode",
                    value="• 3v3 Team Deathmatch\n• Balanced teams based on TDM MMR\n• First team to reach the kill limit wins",
                    inline=False
                )

                await interaction.message.edit(embed=embed, view=view)
                await interaction.response.send_message(
                    f"✅ You have successfully joined the queue as **{existing_user.get('name')}#{existing_user.get('tag')}**! ({len(self.tdm_queue)}/6)",
                    ephemeral=True
                )

                if len(self.tdm_queue) == 6:
                    map_vote = TDMMapVoteView(interaction.channel, self.bot)
                    await map_vote.setup()
                    await map_vote.send_vote_view()
                    await self.make_tdm_teams(interaction.channel)

        async def leave_callback(interaction):
            if str(interaction.user.id) in [p["id"] for p in self.tdm_queue]:
                self.tdm_queue = [p for p in self.tdm_queue if p["id"] != str(interaction.user.id)]
                signup_button.label = f"Sign Up ({len(self.tdm_queue)}/6)"
                
                try:
                    # Remove role
                    member = interaction.guild.get_member(interaction.user.id) or await interaction.guild.fetch_member(interaction.user.id)
                    await member.remove_roles(self.tdm_match_role)
                except discord.Forbidden:
                    await interaction.response.send_message("⚠️ Could not remove role due to permissions.", ephemeral=True)
                
                # Update queue display with remaining players
                riot_names = []
                for player in self.tdm_queue:
                    user_data = users.find_one({"discord_id": player["id"]})
                    if user_data:
                        riot_name = f"{user_data.get('name')}#{user_data.get('tag')}"
                        riot_names.append(riot_name)
                    else:
                        riot_names.append("Unknown")

                embed = discord.Embed(
                    title="3v3 Team Deathmatch Queue",
                    description="Click below to join or leave the queue!",
                    color=discord.Color.blue()
                )
                embed.add_field(
                    name=f"Current Queue ({len(self.tdm_queue)}/6)",
                    value="\n".join(riot_names) if riot_names else "No players in queue",
                    inline=False
                )
                embed.add_field(
                    name="About TDM Mode",
                    value="• 3v3 Team Deathmatch\n• Balanced teams based on TDM MMR\n• First team to reach the kill limit wins",
                    inline=False
                )

                await interaction.message.edit(embed=embed, view=view)
                await interaction.response.send_message(f"❌ You have left the queue. ({len(self.tdm_queue)}/6)", ephemeral=True)

        signup_button.callback = signup_callback
        leave_button.callback = leave_callback

        view.add_item(signup_button)
        view.add_item(leave_button)

        # Create initial messages
        embed = discord.Embed(
            title="3v3 Team Deathmatch Queue",
            description="Click below to join or leave the queue!",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="Current Queue (0/6)",
            value="No players in queue",
            inline=False
        )
        embed.add_field(
            name="About TDM Mode",
            value="• 3v3 Team Deathmatch\n• Balanced teams based on TDM MMR\n• First team to reach the kill limit wins",
            inline=False
        )

        self.tdm_current_message = await self.tdm_match_channel.send(embed=embed, view=view)
        await ctx.send(f"✅ TDM Queue started! Join here: <#{self.tdm_match_channel.id}>")

    async def make_tdm_teams(self, channel):
        players = self.tdm_queue[:]
        print(f"[DEBUG] Forming teams from {len(players)} players")

        # Initialize TDM MMR for any players who don't have it
        for player in players:
            self.bot.ensure_tdm_player_mmr(player["id"])
            print(f"[DEBUG] Player {player['id']} MMR: {self.bot.player_mmr[player['id']].get('tdm_mmr', 1000)}")

        # Sort players by MMR (highest to lowest)
        players.sort(
            key=lambda p: self.bot.player_mmr[p["id"]].get("tdm_mmr", 1000),
            reverse=True
        )

        # Try every possible combination of 3 players to find the most balanced teams
        best_mmr_diff = float('inf')
        best_team1 = None
        best_team2 = None

        from itertools import combinations
        for team1_players in combinations(players, 3):
            # Convert to list for easier handling
            team1 = list(team1_players)
            team2 = [p for p in players if p not in team1]

            # Calculate team MMRs
            team1_mmr = sum(self.bot.player_mmr[p["id"]].get("tdm_mmr", 1000) for p in team1) / 3
            team2_mmr = sum(self.bot.player_mmr[p["id"]].get("tdm_mmr", 1000) for p in team2) / 3

            mmr_diff = abs(team1_mmr - team2_mmr)
            print(f"[DEBUG] Team split MMR diff: {mmr_diff}")

            if mmr_diff < best_mmr_diff:
                best_mmr_diff = mmr_diff
                best_team1 = team1
                best_team2 = team2

        # Set the teams
        self.tdm_team1 = best_team1
        self.tdm_team2 = best_team2

        # Create teams display embed
        embed = discord.Embed(
            title="3v3 Team Deathmatch Teams",
            description="Teams have been balanced by TDM MMR",
            color=discord.Color.blue()
        )

        # Format team displays
        for team_num, team in [(1, self.tdm_team1), (2, self.tdm_team2)]:
            team_mmr = sum(self.bot.player_mmr[p["id"]].get("tdm_mmr", 1000) for p in team) / 3
            team_text = []
            
            for player in team:
                user_data = users.find_one({"discord_id": player["id"]})
                if user_data:
                    name = f"{user_data.get('name')}#{user_data.get('tag')}"
                    mmr = self.bot.player_mmr[player["id"]].get("tdm_mmr", 1000)
                    team_text.append(f"{name} (MMR: {mmr})")

            embed.add_field(
                name=f"Team {team_num} (Avg MMR: {team_mmr:.0f})",
                value="\n".join(team_text),
                inline=False
            )

        embed.add_field(
            name="MMR Difference",
            value=f"Team MMR difference: {best_mmr_diff:.1f}",
            inline=False
        )

        await channel.send(embed=embed)
        await channel.send("Teams are set! Start your match and use `!tdmreport` when finished.")
        
        # Update match status
        self.tdm_match_ongoing = True
        self.tdm_signup_active = False

        # Clean up signup message if it exists
        if self.tdm_current_message:
            try:
                await self.tdm_current_message.delete()
            except discord.NotFound:
                pass

    @commands.command()
    async def tdmreport(self, ctx):
        if not self.tdm_match_ongoing:
            await ctx.send("No TDM match is currently active.")
            return

        current_user = users.find_one({"discord_id": str(ctx.author.id)})
        if not current_user:
            await ctx.send("You need to link your Riot account first using `!linkriot Name#Tag`")
            return

        name = current_user.get("name", "").lower()
        tag = current_user.get("tag", "").lower()
        region = "na"
        platform = "pc"

        # Get match data from API
        url = f"https://api.henrikdev.xyz/valorant/v4/matches/{region}/{platform}/{name}/{tag}"
        try:
            response = requests.get(url, headers={"Authorization": os.getenv("api_key")}, timeout=30)
            match_data = response.json()

            if "data" not in match_data or not match_data["data"]:
                await ctx.send("Could not retrieve match data.")
                return

            match = match_data["data"][0]
            
            # Verify it's a TDM match
            if match.get("metadata", {}).get("mode") != "DEATHMATCH":
                await ctx.send("Most recent match is not a Team Deathmatch.")
                return

            # Get the match players and their stats
            match_players = match.get("players", [])
            if not match_players:
                await ctx.send("No player data found in match.")
                return

            # Verify queue players are in the match
            queue_riot_ids = set()
            for player in self.tdm_queue:
                user_data = users.find_one({"discord_id": str(player["id"])})
                if user_data:
                    player_name = user_data.get("name", "").lower()
                    player_tag = user_data.get("tag", "").lower()
                    queue_riot_ids.add((player_name, player_tag))

            match_player_ids = set()
            for player in match_players:
                player_name = player.get("name", "").lower()
                player_tag = player.get("tag", "").lower()
                match_player_ids.add((player_name, player_tag))

            if not queue_riot_ids.issubset(match_player_ids):
                await ctx.send("Not all queued players were found in the match.")
                return

            # Determine winning team by total kills
            team1_kills = sum(
                player.get("stats", {}).get("kills", 0) 
                for player in match_players 
                if self._is_player_in_team(player, self.tdm_team1)
            )
            
            team2_kills = sum(
                player.get("stats", {}).get("kills", 0)
                for player in match_players 
                if self._is_player_in_team(player, self.tdm_team2)
            )

            winning_team = self.tdm_team1 if team1_kills > team2_kills else self.tdm_team2
            losing_team = self.tdm_team2 if team1_kills > team2_kills else self.tdm_team1

            # Update player stats
            for player_stats in match_players:
                self._update_tdm_stats(player_stats)

            # Adjust MMR
            self.bot.adjust_tdm_mmr(winning_team, losing_team)
            self.bot.save_tdm_mmr_data()

            # Create results embed
            embed = discord.Embed(
                title="TDM Match Results",
                description=f"Final Score: {max(team1_kills, team2_kills)} - {min(team1_kills, team2_kills)}",
                color=discord.Color.blue()
            )

            # Add team stats to embed
            for team_num, team in enumerate([winning_team, losing_team], 1):
                team_stats = []
                for player in team:
                    user_data = users.find_one({"discord_id": str(player["id"])})
                    if user_data:
                        player_name = f"{user_data.get('name')}#{user_data.get('tag')}"
                        player_stats = next(
                            (p for p in match_players if p["name"].lower() == user_data["name"].lower()),
                            None
                        )
                        if player_stats:
                            kills = player_stats.get("stats", {}).get("kills", 0)
                            deaths = player_stats.get("stats", {}).get("deaths", 0)
                            kd = f"{kills}/{deaths} ({kills/deaths:.2f})" if deaths > 0 else f"{kills}/0 (∞)"
                            mmr_change = self._calculate_mmr_change(player["id"], team == winning_team)
                            team_stats.append(f"{player_name}: {kd} (MMR {mmr_change:+d})")

                embed.add_field(
                    name=f"{'Winner' if team_num == 1 else 'Loser'} - Team {team_num}",
                    value="\n".join(team_stats) or "No stats available",
                    inline=False
                )

            await ctx.send(embed=embed)
            await ctx.send("Match recorded! MMR has been updated.")

            # Save match to database
            tdm_matches.insert_one(match)
            
            # Cleanup
            if self.tdm_match_channel:
                await self.tdm_match_channel.delete()
            if self.tdm_match_role:
                await self.tdm_match_role.delete()
            
            self.tdm_match_ongoing = False
            self.tdm_queue = []
            self.tdm_team1 = []
            self.tdm_team2 = []

        except Exception as e:
            await ctx.send(f"An error occurred while processing the match: {str(e)}")
            return

    def _is_player_in_team(self, player_stats, team):
        player_name = player_stats.get("name", "").lower()
        player_tag = player_stats.get("tag", "").lower()
        
        for team_player in team:
            user_data = users.find_one({"discord_id": str(team_player["id"])})
            if user_data:
                if user_data.get("name", "").lower() == player_name and \
                user_data.get("tag", "").lower() == player_tag:
                    return True
        return False

    def _update_tdm_stats(self, player_stats):
        name = player_stats.get("name", "").lower()
        tag = player_stats.get("tag", "").lower()
        
        user_entry = users.find_one({"name": name, "tag": tag})
        if not user_entry:
            return

        discord_id = str(user_entry.get("discord_id"))
        stats = player_stats.get("stats", {})
        kills = stats.get("kills", 0)
        deaths = stats.get("deaths", 0)
        
        self.bot.ensure_tdm_player_mmr(discord_id)
        
        # Update stats
        player_data = self.bot.player_mmr[discord_id]
        total_matches = player_data.get("tdm_matches_played", 0) + 1
        total_kills = player_data.get("tdm_total_kills", 0) + kills
        total_deaths = player_data.get("tdm_total_deaths", 0) + deaths
        
        # Calculate averages
        avg_kills = total_kills / total_matches
        kd_ratio = total_kills / total_deaths if total_deaths > 0 else total_kills

        # Update player data
        player_data.update({
            "tdm_total_kills": total_kills,
            "tdm_total_deaths": total_deaths,
            "tdm_matches_played": total_matches,
            "tdm_avg_kills": avg_kills,
            "tdm_kd_ratio": kd_ratio
        })
    
    @commands.command()
    @commands.has_role("Owner")
    async def canceltdm(self, ctx):
        if not self.tdm_signup_active:
            await ctx.send("No TDM signup is active to cancel.")
            return

        self.tdm_signup_active = False
        self.tdm_queue = []

        await ctx.send("TDM signup cancelled.")

        if self.tdm_match_channel:
            try:
                await self.tdm_match_channel.delete()
            except discord.NotFound:
                pass
            self.tdm_match_channel = None

        if self.tdm_match_role:
            try:
                await self.tdm_match_role.delete()
            except discord.NotFound:
                pass
            self.tdm_match_role = None

    @commands.command()
    async def tdmstats(self, ctx, *, riot_input=None):
        """Check TDM stats for a player"""
        # Handle looking up other players if riot_input is provided
        if riot_input is not None:
            try:
                riot_name, riot_tag = riot_input.rsplit("#", 1)
            except ValueError:
                await ctx.send("Please provide your Riot ID in the format: `Name#Tag`")
                return
            
            player_data = users.find_one({"name": str(riot_name), "tag": str(riot_tag)})
            if player_data:
                player_id = str(player_data.get("discord_id"))
            else:
                await ctx.send("Could not find this player. Please check the name and tag.")
                return
        else:
            player_id = str(ctx.author.id)

        # Get TDM stats for the player
        if player_id in self.bot.player_mmr:
            stats_data = self.bot.player_mmr[player_id]
            
            # Initialize stats with default values
            tdm_mmr = stats_data.get("tdm_mmr", 1000)
            tdm_wins = stats_data.get("tdm_wins", 0)
            tdm_losses = stats_data.get("tdm_losses", 0)
            tdm_total_kills = stats_data.get("tdm_total_kills", 0)
            tdm_total_deaths = stats_data.get("tdm_total_deaths", 0)
            tdm_matches_played = stats_data.get("tdm_matches_played", tdm_wins + tdm_losses)
            tdm_avg_kills = stats_data.get("tdm_avg_kills", 0)
            tdm_kd_ratio = stats_data.get("tdm_kd_ratio", 0)

            # Calculate additional stats
            win_percent = (tdm_wins / tdm_matches_played * 100) if tdm_matches_played > 0 else 0

            # Get Riot name and tag
            user_data = users.find_one({"discord_id": str(player_id)})
            if user_data:
                riot_name = user_data.get("name", "Unknown")
                riot_tag = user_data.get("tag", "Unknown")
                player_name = f"{riot_name}#{riot_tag}"
            else:
                player_name = ctx.author.name

            # Find leaderboard position
            total_players = len([p for p in self.bot.player_mmr.values() if "tdm_mmr" in p])
            sorted_mmr = sorted(
                [(pid, stats) for pid, stats in self.bot.player_mmr.items() if "tdm_mmr" in stats],
                key=lambda x: x[1]["tdm_mmr"],
                reverse=True
            )
            position = None
            slash = "/"
            for idx, (pid, _) in enumerate(sorted_mmr, start=1):
                if pid == player_id:
                    position = idx
                    break

            # Create and send embed
            embed = discord.Embed(
                title=f"{player_name}'s TDM Stats",
                color=discord.Color.blue()
            )

            # Main stats
            embed.add_field(
                name="Rating",
                value=f"TDM MMR: {tdm_mmr}\nRank: {position}{slash}{total_players}",
                inline=False
            )

            # Match stats
            embed.add_field(
                name="Match Stats",
                value=f"Wins: {tdm_wins}\n"
                      f"Losses: {tdm_losses}\n"
                      f"Win Rate: {win_percent:.1f}%\n"
                      f"Total Matches: {tdm_matches_played}",
                inline=True
            )

            # Combat stats
            embed.add_field(
                name="Combat Stats",
                value=f"Total Kills: {tdm_total_kills}\n"
                      f"Total Deaths: {tdm_total_deaths}\n"
                      f"K/D Ratio: {tdm_kd_ratio:.2f}\n"
                      f"Avg Kills/Match: {tdm_avg_kills:.1f}",
                inline=True
            )

            await ctx.send(embed=embed)
        else:
            await ctx.send("You have not played any TDM matches yet!")