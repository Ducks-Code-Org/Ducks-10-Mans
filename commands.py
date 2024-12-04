import discord
from discord.ext import commands
import requests

from database import users, all_matches, mmr_collection
from views.mode_vote_view import ModeVoteView
from views.signup_view import SignupView
from stats_helper import update_stats
import os

# Initialize API
api_key = os.getenv("api_key")
headers = {
    "Authorization": api_key,
}

# FOR TESTING ONLY
mock_match_data = {
    "players": [
        {
            "name": "SSL Wheel",
            "tag": "7126",
            "team_id": "Red",
            "stats": {
                "score": 8188,
                "kills": 31,
                "deaths": 12,
                "assists": 4
            }
        },
        {
            "name": "MetALz",
            "tag": "AZoN",
            "team_id": "Red",
            "stats": {
                "score": 6233,
                "kills": 22,
                "deaths": 11,
                "assists": 6
            }
        },
        {
            "name": "Luh4r",
            "tag": "i0n",
            "team_id": "Red",
            "stats": {
                "score": 5405,
                "kills": 19,
                "deaths": 17,
                "assists": 8
            }
        },
        {
            "name": "Crimsyn",
            "tag": "Rose",
            "team_id": "Red",
            "stats": {
                "score": 3772,
                "kills": 14,
                "deaths": 12,
                "assists": 4
            }
        },
        {
            "name": "ItzFitz",
            "tag": "1738",
            "team_id": "Red",
            "stats": {
                "score": 2829,
                "kills": 8,
                "deaths": 14,
                "assists": 9
            }
        },
        {
            "name": "Duck",
            "tag": "MST",
            "team_id": "Blue",
            "stats": {
                "score": 5405,
                "kills": 17,
                "deaths": 18,
                "assists": 3
            }
        },
        {
            "name": "NBK2003",
            "tag": "1584",
            "team_id": "Blue",
            "stats": {
                "score": 4416,
                "kills": 16,
                "deaths": 20,
                "assists": 3
            }
        },
        {
            "name": "galaxy",
            "tag": "KUJG",
            "team_id": "Blue",
            "stats": {
                "score": 3703,
                "kills": 11,
                "deaths": 20,
                "assists": 7
            }
        },
        {
            "name": "dShocc1",
            "tag": "LNEUP",
            "team_id": "Blue",
            "stats": {
                "score": 3174,
                "kills": 10,
                "deaths": 17,
                "assists": 3
            }
        },
        {
            "name": "mintychewinggum",
            "tag": "8056",
            "team_id": "Blue",
            "stats": {
                "score": 3082,
                "kills": 11,
                "deaths": 19,
                "assists": 5
            }
        },
    ],
    "teams": [
        {
            "team_id": "Red",
            "won": True,
            "rounds_won": 13,
            "rounds_lost": 11
        },
        {
            "team_id": "Blue",
            "won": False,
            "rounds_won": 11,
            "rounds_lost": 13
        }
    ]
}


class BotCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # Signup Command
    @commands.command()
    async def signup(self, ctx):
        # Check if we need to create the view
        if self.bot.signup_view is None:
            self.bot.signup_view = SignupView(ctx, self.bot)

        # Don't create a new signup if one is active
        if self.bot.signup_active:
            await ctx.send("A signup is already in progress. Please wait for it to complete.")
            return

        if self.bot.match_not_reported:
            await ctx.send("Report the last match before starting another one (credits to dshocc for bug testing)")

        self.bot.signup_active = True

        self.bot.current_signup_message = await ctx.send("Click a button to manage your queue status!", view=self.bot.signup_view)

    # Command to join queue without pressing the button
    @commands.command()
    async def join(self, ctx):
        if self.bot.signup_view is None:
            await ctx.send("No signup is currently active.")
            return

        if not self.bot.signup_active:
            await ctx.send("No signup is currently active.")
            return

        existing_user = users.find_one({"discord_id": str(ctx.author.id)})
        if existing_user:
            if ctx.author.id not in [player["id"] for player in self.bot.queue]:
                self.bot.signup_view.add_player_to_queue({"id": ctx.author.id, "name": ctx.author.name})
                if ctx.author.id not in self.bot.player_mmr:
                    self.bot.player_mmr[ctx.author.id] = {"mmr": 1000, "wins": 0, "losses": 0}
                self.bot.player_names[ctx.author.id] = ctx.author.name

                # Update the button label
                await self.bot.signup_view.update_signup()

                await ctx.send(
                    f"{ctx.author.name} added to the queue! Current queue count: {len(self.bot.queue)}")

                if len(self.bot.queue) == 10:
                    await ctx.send("The queue is now full, proceeding to the voting stage.")
                    self.bot.signup_view.cancel_signup_refresh()

                    voting_view = ModeVoteView(ctx, self.bot)

                    # Start vote for how teams will be decided
                    await voting_view.send_view()

                    self.bot.signup_active = False
            else:
                await ctx.send("You're already in the queue!")
        else:
            await ctx.send(
                "You must link your Riot account to join the queue. Use !linkriot Name#Tag to link your account.")

    # Leave queue command without pressing button
    @commands.command()
    async def leave(self, ctx):
        if self.bot.signup_view is None:
            await ctx.send("No signup is currently active.")
            return

        if not self.bot.signup_active:
            await ctx.send("No signup is currently active.")
            return

        if ctx.author.id in [player["id"] for player in self.bot.queue]:
            self.bot.queue[:] = [player for player in self.bot.queue if
                                             player["id"] != ctx.author.id]
            # Update the button label
            await self.bot.signup_view.update_signup()
            await ctx.send("You have left the queue.")

        else:
            await ctx.send("You're not in the queue.")

    @commands.command()
    async def status(self, ctx):
        if self.bot.signup_view is None:
            await ctx.send("No signup is currently active.")
            return

        if not self.bot.signup_active:
            await ctx.send("No signup is currently active.")
            return

        riot_names = []
        for player in self.bot.queue:
            discord_id = player['id']
            user_data = users.find_one({"discord_id": str(discord_id)})
            if user_data:
                riot_name = user_data.get("name", "Unknown")
                riot_names.append(riot_name)
            else:
                # INCASE SOMEONE HASN'T LINKED ACCOUNT
                riot_names.append("Unknown")

        queue_status = ", ".join(riot_names)
        await ctx.send(f"Current queue ({len(self.bot.queue)}/10): {queue_status}")

    # Report the match
    @commands.command()
    async def report(self, ctx):

        current_user = users.find_one({"discord_id": str(ctx.author.id)})
        if not current_user:
            await ctx.send("You need to link your Riot account first using `!linkriot Name#Tag`")
            return

        name = current_user.get("name")
        tag = current_user.get("tag")
        region = "na"
        platform = "pc"

        url = f"https://api.henrikdev.xyz/valorant/v4/matches/{region}/{platform}/{name}/{tag}"
        response = requests.get(url, headers=headers)
        match_data = response.json()
        match = match_data["data"][0]
        metadata = match.get("metadata", {})
        map_name = metadata.get("map", {}).get("name", "").lower()

        testing_mode = False  # TRUE WHILE TESTING

        if testing_mode:
            match = mock_match_data
            match_ongoing = True

            # Reconstruct queue, team1, and team2 from mock_match_data
            queue = []
            team1 = []
            team2 = []

            for player_data in match["players"]:
                player_name = player_data["name"]
                player_tag = player_data["tag"]

                user = users.find_one({"name": player_name, "tag": player_tag})
                if user:
                    discord_id = int(user["discord_id"])
                    player = {"id": discord_id, "name": player_name}

                    queue.append(player)

                    if player_data["team_id"] == "Red":
                        team1.append(player)
                    else:
                        team2.append(player)

                    if discord_id not in self.bot.player_mmr:
                        self.bot.player_mmr[discord_id] = {"mmr": 1000, "wins": 0, "losses": 0}
                    self.bot.player_names[discord_id] = player_name
                else:
                    await ctx.send(f"Player {player_name}#{player_tag} is not linked to any Discord account.")
                    return

            # For mocking match data, set to amount of rounds played
            total_rounds = 24
        else:
            if not self.bot.match_ongoing:
                await ctx.send("No match is currently active, use `!signup` to start one")
                return

            if not self.bot.selected_map:
                await ctx.send("No map was selected for this match.")
                return

            if self.bot.selected_map.lower() != map_name:
                await ctx.send("Map doesn't match your most recent match. Unable to report it.")
                return

            if "data" not in match_data or not match_data["data"]:
                await ctx.send("Could not retrieve match data.")
                return

            match = match_data["data"][0]

            # Get total rounds played from the match data
            teams = match.get('teams', [])
            if teams:
                team1_data = teams[0]
                total_rounds = metadata.get("total_rounds")
            else:
                await ctx.send("No team data found in match data.")
                return

        match_players = match.get("players", [])
        if not match_players:
            await ctx.send("No players found in match data.")
            return

        queue_riot_ids = set()
        for player in self.bot.queue:
            user_data = users.find_one({"discord_id": str(player["id"])})
            if user_data:
                player_name = user_data.get("name").lower()
                player_tag = user_data.get("tag").lower()
                queue_riot_ids.add((player_name, player_tag))

        # get the list of players in the match
        match_player_names = set()
        for player in match_players:
            player_name = player.get("name", "").lower()
            player_tag = player.get("tag", "").lower()
            match_player_names.add((player_name, player_tag))

        if not queue_riot_ids.issubset(match_player_names):
            await ctx.send("The most recent match does not match the 10-man's match.")
            return

        # Determine which team won
        teams = match.get("teams", [])
        if not teams:
            await ctx.send("No team data found in match data.")
            return

        winning_team_id = None
        for team in teams:
            if team.get("won"):
                winning_team_id = team.get("team_id")
                break

        if not winning_team_id:
            await ctx.send("Could not determine the winning team.")
            return

        match_team_players = {
            'Red': set(),
            'Blue': set()
        }

        for player in match_players:
            team_id = player.get("team_id")
            player_name = player.get("name", "").lower()
            player_tag = player.get("tag", "").lower()
            if team_id in match_team_players:
                match_team_players[team_id].add((player_name, player_tag))

        team1_riot_ids = set()
        for player in self.bot.team1:
            user_data = users.find_one({"discord_id": str(player["id"])})
            if user_data:
                player_name = user_data.get("name", "").lower()
                player_tag = user_data.get("tag").lower()
                team1_riot_ids.add((player_name, player_tag))

        team2_riot_ids = set()
        for player in self.bot.team2:
            user_data = users.find_one({"discord_id": str(player["id"])})
            if user_data:
                player_name = user_data.get("name", "").lower()
                player_tag = user_data.get("tag").lower()
                team2_riot_ids.add((player_name, player_tag))

        winning_match_team_players = match_team_players.get(winning_team_id, set())

        if winning_match_team_players == team1_riot_ids:
            winning_team = self.bot.team1
            losing_team = self.bot.team2
        elif winning_match_team_players == team2_riot_ids:
            winning_team = self.bot.team2
            losing_team = self.bot.team1
        else:
            await ctx.send("Could not match the winning team to our teams.")
            return

        for player in winning_team + losing_team:
            self.bot.ensure_player_mmr(player["id"])

        # Adjust MMR
        self.bot.adjust_mmr(winning_team, losing_team)
        await ctx.send("MMR Updated!")

        # Update stats for each player
        for player_stats in match_players:
            update_stats(player_stats, total_rounds, self.bot.player_mmr, self.bot.player_names)

        # Now save all updates to the database
        self.bot.save_mmr_data()
        await ctx.send("Player stats updated!")

        # Record every match played in a new collection
        all_matches.insert_one(match)

        self.bot.match_not_reported = False
        self.bot.match_ongoing = False

    # Allow players to check their MMR and stats
    @commands.command()
    async def stats(self, ctx):
        player_id = ctx.author.id
        if player_id in self.bot.player_mmr:
            stats_data = self.bot.player_mmr[player_id]
            mmr_value = stats_data["mmr"]
            wins = stats_data["wins"]
            losses = stats_data["losses"]
            matches_played = stats_data.get('matches_played', wins + losses)
            total_rounds_played = stats_data.get('total_rounds_played', 0)
            avg_cs = stats_data.get('average_combat_score', 0)
            kd_ratio = stats_data.get('kill_death_ratio', 0)
            win_percent = (wins / matches_played) * 100 if matches_played > 0 else 0

            # Get Riot name and tag
            user_data = users.find_one({"discord_id": str(player_id)})
            if user_data:
                riot_name = user_data.get("name", "Unknown")
                riot_tag = user_data.get("tag", "Unknown")
                player_name = f"{riot_name}#{riot_tag}"
            else:
                player_name = ctx.author.name

            await ctx.send(
                f"**{player_name}'s Stats:**\n"
                f"MMR: {mmr_value}\n"
                f"Wins: {wins}\n"
                f"Losses: {losses}\n"
                f"Win%: {win_percent:.2f}%\n"
                f"Matches Played: {matches_played}\n"
                f"Total Rounds Played: {total_rounds_played}\n"
                f"Average Combat Score: {avg_cs:.2f}\n"
                f"Kill/Death Ratio: {kd_ratio:.2f}"
            )
        else:
            await ctx.send("You do not have an MMR yet. Participate in matches to earn one!")

    # Display leaderboard
    @commands.command()
    async def leaderboard(self, ctx):
        if not self.bot.player_mmr:
            await ctx.send("No MMR data available yet.")
            return

        # Sort players by MMR and take the top 10
        sorted_mmr = sorted(self.bot.player_mmr.items(), key=lambda x: x[1]["mmr"], reverse=True)[:10]

        leaderboard_entries = []
        for idx, (player_id, stats) in enumerate(sorted_mmr, start=1):
            # Get the Riot name and tag from the users collection
            user_data = users.find_one({"discord_id": str(player_id)})
            if user_data:
                riot_name = user_data.get("name", "Unknown")
                riot_tag = user_data.get("tag", "Unknown")
                name = f"**{riot_name}#{riot_tag}**"
            else:
                name = "Unknown"

            mmr_value = stats["mmr"]
            wins = stats["wins"]
            losses = stats["losses"]
            matches_played = stats.get('matches_played', wins + losses)
            total_rounds_played = stats.get('total_rounds_played', 0)
            avg_cs = stats.get('average_combat_score', 0)
            kd_ratio = stats.get('kill_death_ratio', 0)
            win_percent = (wins / matches_played) * 100 if matches_played > 0 else 0

            leaderboard_entries.append(
                f"{idx}. {name} - MMR: {mmr_value}, Wins: {wins}, Losses: {losses}, "
                f"Win%: {win_percent:.1f}%, Avg CS: {avg_cs:.2f}, K/D: {kd_ratio:.2f}"
            )

        leaderboard_text = "\n".join(leaderboard_entries)
        await ctx.send(f"## MMR Leaderboard (Top 10 Players): ##\n{leaderboard_text}")

    @commands.command()
    @commands.has_role("Owner")  # Restrict this command to admins
    async def initialize_rounds(self, ctx):
        result = mmr_collection.update_many(
            {},  # Update all documents
            {'$set': {'total_rounds_played': 0}}
        )
        await ctx.send(f"Initialized total_rounds_played for {result.modified_count} players.")

    # To recalculate average combat score after bug
    @commands.command()
    @commands.has_role("Owner")
    async def recalculate(self, ctx):
        players = mmr_collection.find()
        updated_count = 0
        for player in players:
            player_id = int(player.get('player_id'))
            total_combat_score = player.get('total_combat_score', 0)
            total_rounds_played = player.get('total_rounds_played', 0)

            if total_rounds_played > 0:
                average_combat_score = total_combat_score / total_rounds_played
            else:
                average_combat_score = 0

            # Update the database
            mmr_collection.update_one(
                {'player_id': player_id},
                {'$set': {'average_combat_score': average_combat_score}}
            )

            # Update the in-memory player_mmr dictionary
            if player_id in self.bot.player_mmr:
                self.bot.player_mmr[player_id]['average_combat_score'] = average_combat_score
            else:
                # In case the player is not in player_mmr (should not happen)
                self.bot.player_mmr[player_id] = {
                    'average_combat_score': average_combat_score
                }

            updated_count += 1

        self.bot.load_mmr_data()

        await ctx.send(f"Recalculated average combat score for {updated_count} players.")

    # Simulate a queue
    @commands.command()
    async def simulate_queue(self, ctx):
        if self.bot.signup_view is None:
            self.bot.signup_view = SignupView(ctx, self.bot)
        if self.bot.signup_active:
            await ctx.send("A signup is already in progress. Resetting queue for simulation.")
            self.bot.queue.clear()

        # Add 10 dummy players to the queue
        queue = [{"id": i, "name": f"Player{i}"} for i in range(1, 11)]

        # Assign default MMR to the dummy players and map IDs to names
        for player in queue:
            if player["id"] not in self.bot.player_mmr:
                self.bot.player_mmr[player["id"]] = {"mmr": 1000, "wins": 0, "losses": 0}
            self.bot.player_names[player["id"]] = player["name"]

        self.bot.save_mmr_data()

        signup_active = True
        await ctx.send(f"Simulated full queue: {', '.join([player['name'] for player in queue])}")

        # Proceed to the voting stage
        await ctx.send("The queue is now full, proceeding to the voting stage.")

        mode_vote = ModeVoteView(ctx, self.bot)
        await mode_vote.send_view()

    # Link Riot Account
    @commands.command()
    async def linkriot(self, ctx, *, riot_input):
        try:
            riot_name, riot_tag = riot_input.rsplit("#", 1)
        except ValueError:
            await ctx.send("Please provide your Riot ID in the format: `Name#Tag`")
            return

        data = requests.get(f"https://api.henrikdev.xyz/valorant/v1/account/{riot_name}/{riot_tag}", headers=headers)
        user = data.json()

        if "data" not in user:
            await ctx.send("Could not find your Riot account. Please check the name and tag.")
        else:
            user_data = {
                "discord_id": str(ctx.author.id),
                "name": riot_name,
                "tag": riot_tag,
            }
            users.update_one(
                {"discord_id": str(ctx.author.id)},
                {"$set": user_data},
                upsert=True
            )
            await ctx.send(f"Successfully linked {riot_name}#{riot_tag} to your Discord account.")

    # Set captain1
    @commands.command()
    @commands.has_role("blood")
    async def setcaptain1(self, ctx, *, riot_name_tag):
        try:
            riot_name, riot_tag = riot_name_tag.rsplit("#", 1)
        except ValueError:
            await ctx.send("Please provide the Riot ID in the format: `Name#Tag`")
            return

        # Find the player in the queue with matching Riot name and tag
        player_in_queue = None
        for player in self.bot.queue:
            user_data = users.find_one({"discord_id": str(player["id"])})
            if user_data:
                user_riot_name = user_data.get("name", "").lower()
                user_riot_tag = user_data.get("tag", "").lower()
                if user_riot_name == riot_name.lower() and user_riot_tag == riot_tag.lower():
                    player_in_queue = player
                    break
        if not player_in_queue:
            await ctx.send(f"{riot_name}#{riot_tag} is not in the queue.")
            return

        if self.bot.captain2 and player_in_queue["id"] == self.bot.captain2["id"]:
            await ctx.send(f"{riot_name}#{riot_tag} is already selected as Captain 2.")
            return

        self.bot.captain1 = player_in_queue
        await ctx.send(f"Captain 1 set to {riot_name}#{riot_tag}")

    # Set captain2
    @commands.command()
    @commands.has_role("blood")
    async def setcaptain2(self, ctx, *, riot_name_tag):
        try:
            riot_name, riot_tag = riot_name_tag.rsplit("#", 1)
        except ValueError:
            await ctx.send("Please provide the Riot ID in the format: `Name#Tag`")
            return

        # Find the player in the queue with matching Riot name and tag
        player_in_queue = None
        for player in self.bot.queue:
            user_data = users.find_one({"discord_id": str(player["id"])})
            if user_data:
                user_riot_name = user_data.get("name", "").lower()
                user_riot_tag = user_data.get("tag", "").lower()
                if user_riot_name == riot_name.lower() and user_riot_tag == riot_tag.lower():
                    player_in_queue = player
                    break
        if not player_in_queue:
            await ctx.send(f"{riot_name}#{riot_tag} is not in the queue.")
            return

        if self.bot.captain1 and player_in_queue["id"] == self.bot.captain1["id"]:
            await ctx.send(f"{riot_name}#{riot_tag} is already selected as Captain 1.")
            return

        self.bot.captain2 = player_in_queue
        await ctx.send(f"Captain 2 set to {riot_name}#{riot_tag}")

    # Stop the signup process, only owner can do this
    @commands.command()
    @commands.has_role("Owner")
    async def cancel(self, ctx):
        if not self.bot.signup_active:
            await ctx.send("No signup is active to cancel")
            return
        if self.bot.current_signup_message:
            self.bot.signup_view.cancel_signup_refresh()
            try:
                await self.bot.current_signup_message.delete()
            except discord.NotFound:
                pass
            self.bot.current_signup_message = None
            await ctx.send("Canceled Signup")
            self.bot.signup_active = False
        else:
            await ctx.send("Nothing to cancel")

    # Custom Help Command
    @commands.command()
    async def help(self, ctx):
        help_embed = discord.Embed(
            title="Help Menu",
            description="Duck's 10 Mans Commands:",
            color=discord.Color.blue()
        )

        help_embed.add_field(name="!signup", value="Start a signup session for matches.", inline=False)
        help_embed.add_field(name="!report", value="Report the most recent match and update MMR.", inline=False)
        help_embed.add_field(name="!stats", value="Check your MMR and match stats.", inline=False)
        help_embed.add_field(name="!leaderboard", value="View the MMR leaderboard.", inline=False)
        help_embed.add_field(name="!linkriot", value="Link or update your Riot account using `Name#Tag`.", inline=False)
        help_embed.add_field(name="!join", value="Joins the queue.", inline=False)
        help_embed.add_field(name="!setcaptain1", value="Set Captain 1 using `Name#Tag` (only accessible by admins)",
                             inline=False)
        help_embed.add_field(name="!setcaptain2", value="Set Captain 2 using `Name#Tag` (only accessible by admins)",
                             inline=False)
        help_embed.add_field(name="!leave", value="Leaves the queue", inline=False)
        help_embed.add_field(name="!help", value="Display this help menu.", inline=False)

        # Send the embedded message
        await ctx.send(embed=help_embed)
